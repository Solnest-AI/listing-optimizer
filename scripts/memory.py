#!/usr/bin/env python3
"""
memory.py — run-history memory for the Listing Optimizer.

Every run produces a compact, PRICE-FREE summary of what the optimizer did
(ALE scores, the title/summary it wrote, hero + top-5 photos, comp amenity gaps,
the price-free funnel snapshot, forward occupancy, what cadence items refreshed).
That summary is:

  • ALWAYS upserted into a local history file → state/history.jsonl   (zero setup)
  • OPTIONALLY mirrored to Supabase           → table listing_optimizer_runs

BOTH layers key on (listing_slug, run_date) — see _KEY_COLS. A same-day re-run REPLACES
the row in both, last write wins. The local layer used to blind-append, so the two stores
silently disagreed about the same logical row and duplicates accumulated locally only.

so the next run can compare against the last one (ALE trend, "title last changed
3 weeks ago", views/CTR movement) — the measurement loop.

This module is stdlib-only and pure where it matters (summarize / guard / SQL),
so it's easy to test. The SKILL drives the actual Supabase I/O through whatever
Supabase MCP the user has connected (user-scoped → "just works" for anyone who
set up the Revenue Manager). A REST fallback is included for headless use.

ZERO-PRICING: every record is scanned with the SAME guardrail render_report.py
uses (PRICING_RE, mirrored below and asserted identical in tests). If anything
price-like is present, the write is REFUSED. The memory never stores rates.

Subcommands:
  record     --result result.json [--result-path P] [--season S] [--applied]
             [--cadence-marked a,b] [--history H] [--out record.json] [--no-local]
  prior      --listing SLUG [--history H] [--limit N] [--rest]
  dedupe     [--history H] [--dry-run]        repair duplicate rows in an older history file
  merge-rows --rows rows.json [--history H]   pull remote rows down into the local history
  sql-upsert --record record.json [--table T]
  sql-prior  --listing SLUG [--limit N] [--table T]
  rest-upsert --record record.json [--table T]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from artifacts import file_lock

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HISTORY = ROOT / "state" / "history.jsonl"
TABLE = "listing_optimizer_runs"

# ── Zero-pricing guardrail ───────────────────────────────────────────────────
# The record is a REPORT-level artifact (it carries diagnostic-derived fields
# like funnel.lever_focus, which legitimately say "hand off to a revenue/pricing
# tool"). So it uses render_report.py's PRICE_NUMBER_RE — the same scan the
# rendered report uses: it bars every price NUMBER / currency / rate from the DB
# but ALLOWS the bare meta-words "pricing"/"revenue" in that qualitative handoff.
# MUST stay byte-identical to render_report.py's PRICE_NUMBER_RE; tests/test_memory.py
# imports both and asserts the patterns match, so they can never silently drift.
PRICE_NUMBER_RE = re.compile(r"""(?ix)
    (?:
        [$€£¥₹]\s?\d
      | \b(?:USD|CAD|EUR|GBP|AUD|NZD|MXN)\s*\d
      | \b\d[\d,.]*\s*/\s*night\b
      | \b\d[\d,.]*\s+per\s+night\b
      | \b(?:nightly|daily|average\s+daily)\s+rate\b
      | \b\d+[\s-]?nights?\s+min(?:imum)?\b
      | \bmin(?:imum)?\s+(?:\d+[\s-]?)?nights?\b
      | \b\d[\d,.]*\s+(?:a|per)\s+(?:night|stay|nt)\b
      | \b\d[\d,.]*\s*(?:-|–|to)\s*\d[\d,.]*\s+(?:a\s+|per\s+|/\s*)?(?:night|stay|nightly)\b
      | \bdollars?\b
    )
""")

# Columns written by record/upsert, in order. listing_slug + run_date are the
# conflict key; everything else is updated on a same-day re-run.
COLUMNS = [
    "listing_slug", "listing_name", "city", "run_date", "season",
    "ale_total", "ale_scores", "title", "summary_char_count", "applied",
    "photo_hero", "photo_top5", "reshoot_count", "amenity_gaps", "funnel",
    "occupancy_forward_pct", "occupancy_monthly", "cadence_marked", "result_path",
]
_JSONB_COLS = {"ale_scores", "photo_top5", "amenity_gaps", "funnel", "occupancy_monthly", "cadence_marked"}
_NUM_COLS = {"ale_total", "summary_char_count", "photo_hero", "reshoot_count", "occupancy_forward_pct"}
_BOOL_COLS = {"applied"}
_KEY_COLS = ("listing_slug", "run_date")

# Only these funnel keys are kept — all price-free. The long prose `diagnosis`
# is deliberately dropped (it lives in result.json and uses meta-words like
# "pricing"/"revenue" that the strict guard would reject).
_FUNNEL_KEEP = (
    "source", "rankbreeze_id", "city_rank", "views_monthly",
    "booking_rate_monthly", "occupancy_monthly", "ctr_vs_similar", "lever_focus",
)


# ── Build the record ─────────────────────────────────────────────────────────
def summarize(result: dict, *, result_path: str | None = None, season: str | None = None,
              applied: bool | None = None, cadence_marked: list[str] | None = None) -> dict:
    """Extract the compact, price-free run summary from a result.json dict.

    CLI/explicit args win over fields embedded in result.json, which win over
    sensible defaults. Returns a dict keyed exactly by COLUMNS.
    """
    listing = result.get("listing", {}) or {}
    optimized = result.get("optimized", {}) or {}
    photos = result.get("photos", {}) or {}
    comps = result.get("comps", {}) or {}
    occ = result.get("occupancy", {}) or {}

    scorecard = result.get("ale_scorecard", []) or []
    ale_scores = [
        {"dimension": d.get("dimension", ""), "score": d.get("score")}
        for d in scorecard if isinstance(d, dict)
    ]
    numeric = [d["score"] for d in ale_scores if isinstance(d.get("score"), (int, float))]
    ale_total = round(sum(numeric) / len(numeric), 2) if numeric else None

    funnel = result.get("funnel") or {}
    funnel_subset = {k: funnel[k] for k in _FUNNEL_KEEP if k in funnel}

    rec = {
        "listing_slug": listing.get("slug"),
        "listing_name": listing.get("name"),
        "city": listing.get("city"),
        "run_date": result.get("run_date"),
        "season": season if season is not None else result.get("season"),
        "ale_total": ale_total,
        "ale_scores": ale_scores,
        "title": optimized.get("title"),
        "summary_char_count": (len(optimized["summary"].strip())
                               if isinstance(optimized.get("summary"), str)
                               else optimized.get("summary_char_count")),
        "applied": bool(applied) if applied is not None else bool(result.get("applied", False)),
        "photo_hero": photos.get("hero"),
        "photo_top5": photos.get("recommended_top5_order", []) or [],
        "reshoot_count": len(photos.get("reshoot", []) or []),
        "amenity_gaps": comps.get("amenity_gaps", []) or [],
        "funnel": funnel_subset,
        "occupancy_forward_pct": occ.get("forward_pct"),
        "occupancy_monthly": occ.get("monthly", {}) or {},
        "cadence_marked": (cadence_marked if cadence_marked is not None
                           else result.get("cadence_marked", []) or []),
        "result_path": result_path,
    }
    if not rec["listing_slug"] or not rec["run_date"]:
        raise SystemExit("[memory] result.json is missing listing.slug or run_date — cannot record.")
    return rec


def assert_price_free(rec: dict) -> None:
    """Refuse to persist a record that contains any price NUMBER / currency / rate.
    (Bare meta-words like 'pricing'/'revenue' are allowed — see PRICE_NUMBER_RE.)"""
    blob = json.dumps(rec, ensure_ascii=False)
    hits = [m.group(0) for m in PRICE_NUMBER_RE.finditer(blob)]
    if hits:
        sys.stderr.write("[memory] ❌ ZERO-PRICING GUARDRAIL TRIPPED — refusing to store record.\n")
        for h in hits[:20]:
            sys.stderr.write(f"   - '{h}'\n")
        raise SystemExit("[memory] price/min-stay term in the run record — fix the optimizer output and re-run.")


# ── Local history (always-on layer) ──────────────────────────────────────────
def _row_key(rec: dict) -> tuple:
    """The record's identity — read from _KEY_COLS, which is the SAME constant the SQL
    ON CONFLICT clause is built from. Hardcoding the column names here would let the local
    upsert and the Supabase upsert drift apart, which is the exact class of bug that let
    duplicates accumulate locally while Supabase stayed clean."""
    return tuple(rec.get(c) for c in _KEY_COLS)


def append_local(rec: dict, history_path: Path) -> str:
    """UPSERT the record into the local history on (listing_slug, run_date).

    This used to be a blind append, which meant the two stores disagreed about the same
    logical row: Supabase does `ON CONFLICT (listing_slug, run_date) DO UPDATE`, so a
    same-day re-run REPLACED the row there, while the local file grew a second one. Real
    consequence found on 2026-09-20: `state/history.jsonl` carried two rows each for
    boho-bliss 2026-08-05 and olde-town-ambler 2026-08-18 — same run, refined copy — and
    `prior` then served a stale sibling of today's run as if it were the previous run,
    quietly corrupting the trend comparison the memory layer exists to provide.

    Semantics now mirror the SQL: last write wins, the row keeps its position so history
    stays chronological, and any pre-existing duplicates of that key collapse to one.

    Returns "inserted" or "replaced" so the caller can say which happened.

    Robustness: operates on RAW lines and copies anything unparseable through untouched —
    rebuilding the file from parsed rows would silently discard a corrupt line, and losing
    history to a dedupe is a worse bug than the duplicate. Writes via a temp file +
    os.replace so an interrupted run cannot truncate the history.
    """
    with file_lock(history_path.with_suffix(history_path.suffix + ".lock")):
        history_path.parent.mkdir(parents=True, exist_ok=True)
        key = _row_key(rec)
        payload = json.dumps(rec, ensure_ascii=False)

        # No key (shouldn't happen — summarize() validates) → fall back to a plain append
        # rather than collapsing every keyless row into one.
        if not any(key):
            with history_path.open("a", encoding="utf-8") as fh:
                fh.write(payload + "\n")
            return "inserted"

        existing = history_path.read_text(encoding="utf-8").splitlines() if history_path.exists() else []
        out: list[str] = []
        status = "inserted"
        for line in existing:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                matches = isinstance(row, dict) and _row_key(row) == key
            except Exception:
                matches = False  # unparseable: keep verbatim, never drop
            if matches:
                if status == "inserted":  # first match becomes the new record, in place
                    out.append(payload)
                    status = "replaced"
                # any further matches are pre-existing duplicates — collapsed
            else:
                out.append(line)
        if status == "inserted":
            out.append(payload)

        _atomic_write_lines(history_path, out)
        return status


def _atomic_write_lines(path: Path, lines: list[str]) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def dedupe_local(history_path: Path, dry_run: bool = False) -> dict:
    """Collapse any pre-existing duplicate (listing_slug, run_date) rows, LAST wins.

    Repairs files written before append_local became an upsert. Last-wins matches both the
    SQL and the reality of a same-day re-run: the later row is the refined one (verified on
    the real duplicates — the second row had the reworded amenity gaps and the corrected
    summary_char_count).
    """
    with file_lock(history_path.with_suffix(history_path.suffix + ".lock")):
        if not history_path.exists():
            return {"total": 0, "kept": 0, "removed": 0, "keys": []}
        raw = [l for l in history_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        last_index: dict[tuple, int] = {}
        parsed: list[tuple] = []  # (key|None, line)
        for i, line in enumerate(raw):
            try:
                row = json.loads(line)
                key = _row_key(row) if isinstance(row, dict) else None
            except Exception:
                key = None
            if key and any(key):
                last_index[key] = i
            parsed.append((key, line))

        dupes = sorted({k for k in last_index if sum(1 for kk, _ in parsed if kk == k) > 1})
        out = [line for i, (key, line) in enumerate(parsed)
               if key is None or not any(key) or last_index[key] == i]
        if not dry_run and len(out) != len(raw):
            backup = history_path.with_suffix(history_path.suffix + ".bak")
            backup.write_text("\n".join(raw) + "\n", encoding="utf-8")
            _atomic_write_lines(history_path, out)
        return {"total": len(raw), "kept": len(out), "removed": len(raw) - len(out),
                "keys": [f"{s} {d}" for s, d in dupes]}


def coerce_row(row: dict) -> dict:
    """Turn a Supabase row back into a local-history record.

    Postgres hands numerics back as STRINGS through the MCP/REST layers ("3.14"), so a raw
    row written straight into history.jsonl would store `ale_total` as text and every
    numeric trend comparison would silently compare strings. jsonb columns already arrive
    as objects. Unknown/extra columns (id, created_at) are dropped so the local shape stays
    exactly COLUMNS.
    """
    out: dict = {}
    for col in COLUMNS:
        v = row.get(col)
        if col in _NUM_COLS and isinstance(v, str):
            try:
                v = float(v)
            except ValueError:
                v = None
            else:
                if v is not None and float(v).is_integer() and col != "ale_total":
                    v = int(v)
        elif col in _BOOL_COLS:
            v = bool(v) if v is not None else False
        elif col in _JSONB_COLS and v is None:
            v = [] if col in ("ale_scores", "photo_top5", "amenity_gaps", "cadence_marked") else {}
        out[col] = v
    return out


def merge_rows(rows: list[dict], history_path: Path) -> dict:
    """Upsert remote rows into the local history. Local and remote are the SAME record keyed
    the same way, so pulling down is just an upsert per row — a run that exists only upstream
    (e.g. one made on another machine, since state/ is gitignored and per-machine) lands
    locally without disturbing anything already there."""
    stats = {"inserted": 0, "replaced": 0, "skipped": 0}
    for row in rows:
        rec = coerce_row(row)
        if not rec.get("listing_slug") or not rec.get("run_date"):
            stats["skipped"] += 1
            continue
        assert_price_free(rec)
        stats[append_local(rec, history_path)] += 1
    return stats

def read_local(history_path: Path) -> list[dict]:
    if not history_path.exists():
        return []
    rows = []
    for line in history_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def prior_runs(listing: str, history_path: Path, limit: int = 3) -> list[dict]:
    """Most-recent-first prior runs for a listing, de-duped per run_date."""
    latest: dict[str, dict] = {}
    for r in read_local(history_path):
        if r.get("listing_slug") == listing and r.get("run_date"):
            latest[r["run_date"]] = r  # later line for same date wins
    ordered = sorted(latest.values(), key=lambda r: r["run_date"], reverse=True)
    return ordered[:limit]


# ── SQL emitters (for the Supabase MCP path) ─────────────────────────────────
def _dollar_quote(s: str) -> str:
    """Postgres dollar-quote a string with a collision-free tag (injection-safe)."""
    tag, i = "lo", 0
    while f"${tag}$" in s:
        i += 1
        tag = f"lo{i}"
    return f"${tag}${s}${tag}$"


def _sql_value(col: str, val) -> str:
    if val is None:
        return "NULL"
    if col in _BOOL_COLS:
        return "true" if val else "false"
    if col in _NUM_COLS:
        return str(val)
    if col in _JSONB_COLS:
        return _dollar_quote(json.dumps(val, ensure_ascii=False)) + "::jsonb"
    return _dollar_quote(str(val))


def upsert_sql(rec: dict, table: str = TABLE) -> str:
    cols = ", ".join(COLUMNS)
    vals = ", ".join(_sql_value(c, rec.get(c)) for c in COLUMNS)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS if c not in _KEY_COLS)
    return (
        f"INSERT INTO public.{table} ({cols})\n"
        f"VALUES ({vals})\n"
        f"ON CONFLICT (listing_slug, run_date) DO UPDATE SET\n"
        f"  {updates}, created_at = now();"
    )


def prior_sql(listing: str, limit: int = 3, table: str = TABLE) -> str:
    cols = ("run_date, ale_total, ale_scores, title, summary_char_count, applied, "
            "photo_hero, reshoot_count, funnel, occupancy_forward_pct, cadence_marked")
    return (
        f"SELECT {cols}\n"
        f"FROM public.{table}\n"
        f"WHERE listing_slug = {_dollar_quote(listing)}\n"
        f"ORDER BY run_date DESC\n"
        f"LIMIT {int(limit)};"
    )


# ── REST fallback (headless / no MCP) ────────────────────────────────────────
def _rest_env() -> tuple[str, str] | None:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    return (url, key) if url and key else None


def _rest_request(method: str, path: str, *, body: bytes | None = None, extra_headers: dict | None = None):
    env = _rest_env()
    if not env:
        raise SystemExit("[memory] REST mode needs SUPABASE_URL + SUPABASE_SERVICE_KEY in the environment.")
    url, key = env
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # A real UA — Supabase's edge sits behind Cloudflare, which 403s the
        # default Python-urllib user-agent.
        "User-Agent": "listing-optimizer/1.0",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(f"{url}/rest/v1/{path}", data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else []


def rest_upsert(rec: dict, table: str = TABLE) -> list:
    body = json.dumps(rec, ensure_ascii=False).encode("utf-8")
    return _rest_request(
        "POST",
        f"{table}?on_conflict=listing_slug,run_date",
        body=body,
        extra_headers={"Prefer": "resolution=merge-duplicates,return=representation"},
    )


def rest_prior(listing: str, limit: int = 3, table: str = TABLE) -> list:
    q = (f"{table}?listing_slug=eq.{urllib.parse.quote(listing)}"
         f"&order=run_date.desc&limit={int(limit)}")
    return _rest_request("GET", q)


# ── CLI ──────────────────────────────────────────────────────────────────────
def _load_result(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Listing Optimizer run-history memory.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_rec = sub.add_parser("record", help="summarize a result.json into a price-free record + append local history")
    p_rec.add_argument("--result", required=True)
    # REQUIRED IN PRACTICE. result.json no longer carries the machine blocks (photos,
    # occupancy, comps.top) — render_report.py merges those from the working dir at render
    # time. Without --workdir the record silently stores photo_hero=null, photo_top5=[],
    # reshoot_count=0 and occupancy=null, which quietly destroys the run-to-run trend this
    # whole memory layer exists to provide. Caught by a full end-to-end run on 2026-09-20.
    p_rec.add_argument("--workdir", default=None,
                       help="output/<DATE>/<SLUG> — merge the machine blocks from disk so the "
                            "record captures hero/top5/reshoot/occupancy. Defaults to the "
                            "directory containing --result.")
    p_rec.add_argument("--result-path", default=None, help="path to store as a pointer (defaults to --result)")
    p_rec.add_argument("--season", default=None)
    p_rec.add_argument("--applied", action="store_true")
    p_rec.add_argument("--cadence-marked", default=None, help="comma-separated cadence item keys refreshed this run")
    p_rec.add_argument("--history", default=str(DEFAULT_HISTORY))
    p_rec.add_argument("--out", default=None, help="also write the record JSON here")
    p_rec.add_argument("--no-local", action="store_true", help="don't append to local history")

    p_mr = sub.add_parser("merge-rows", help="upsert remote Supabase rows (JSON array) into the local history")
    p_mr.add_argument("--rows", required=True, help="path to a JSON array of listing_optimizer_runs rows")
    p_mr.add_argument("--history", default=str(DEFAULT_HISTORY))

    p_dd = sub.add_parser("dedupe", help="collapse duplicate (listing_slug, run_date) rows in the local history, last wins")
    p_dd.add_argument("--history", default=str(DEFAULT_HISTORY))
    p_dd.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")

    p_pri = sub.add_parser("prior", help="print most-recent prior runs for a listing")
    p_pri.add_argument("--listing", required=True)
    p_pri.add_argument("--history", default=str(DEFAULT_HISTORY))
    p_pri.add_argument("--limit", type=int, default=3)
    p_pri.add_argument("--rest", action="store_true", help="read from Supabase via REST instead of local history")

    p_up = sub.add_parser("sql-upsert", help="print the Supabase UPSERT SQL for a record")
    p_up.add_argument("--record", required=True)
    p_up.add_argument("--table", default=TABLE)

    p_sp = sub.add_parser("sql-prior", help="print the Supabase SELECT SQL for prior runs")
    p_sp.add_argument("--listing", required=True)
    p_sp.add_argument("--limit", type=int, default=3)
    p_sp.add_argument("--table", default=TABLE)

    p_ru = sub.add_parser("rest-upsert", help="UPSERT a record to Supabase via REST (needs env vars)")
    p_ru.add_argument("--record", required=True)
    p_ru.add_argument("--table", default=TABLE)

    args = ap.parse_args(argv)

    if args.cmd == "record":
        result = _load_result(args.result)
        # Rehydrate the machine blocks the model no longer retypes into result.json. The
        # merge is imported from render_report so there is exactly ONE implementation — a
        # second copy here would be free to drift, and a drifting merge is invisible.
        wd = Path(args.workdir) if args.workdir else Path(args.result).parent
        if wd.is_dir():
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from render_report import merge_machine_blocks
                filled = merge_machine_blocks(result, wd)
                if filled:
                    print(f"[memory] merged from {wd}: {', '.join(filled)}", file=sys.stderr)
            except (ValueError, OSError) as e:
                raise SystemExit(f"[memory] invalid working artifacts; refusing to record: {e}")
            except ImportError as e:
                print(f"[memory] WARNING: could not merge machine blocks ({e}) — "
                      f"photo/occupancy trend fields may be empty", file=sys.stderr)
        marked = [s.strip() for s in args.cadence_marked.split(",")] if args.cadence_marked else None
        rec = summarize(
            result,
            result_path=args.result_path or args.result,
            season=args.season,
            applied=True if args.applied else None,
            cadence_marked=marked,
        )
        assert_price_free(rec)
        if not args.no_local:
            what = append_local(rec, Path(args.history))
            if what == "replaced":
                print(f"[memory] REPLACED the existing {rec['listing_slug']} "
                      f"{rec['run_date']} row (same-day re-run — last write wins, "
                      f"matching the Supabase upsert)", file=sys.stderr)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(rec, ensure_ascii=False))
        return 0

    if args.cmd == "merge-rows":
        rows = json.loads(Path(args.rows).read_text(encoding="utf-8"))
        if isinstance(rows, dict):
            rows = rows.get("data") or rows.get("result") or [rows]
        s = merge_rows(rows, Path(args.history))
        print(f"[memory] merge-rows: {s['inserted']} inserted, {s['replaced']} replaced, "
              f"{s['skipped']} skipped (no key)")
        return 0

    if args.cmd == "dedupe":
        r = dedupe_local(Path(args.history), dry_run=args.dry_run)
        verb = "would remove" if args.dry_run else "removed"
        print(f"[memory] {r['total']} rows → {r['kept']} kept, {verb} {r['removed']} duplicate(s)")
        for k in r["keys"]:
            print(f"   duplicate key: {k}")
        if r["removed"] and not args.dry_run:
            print(f"[memory] backup written to {args.history}.bak")
        return 0

    if args.cmd == "prior":
        if args.rest:
            rows = rest_prior(args.listing, args.limit)
        else:
            rows = prior_runs(args.listing, Path(args.history), args.limit)
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "sql-upsert":
        rec = _load_result(args.record)
        assert_price_free(rec)
        print(upsert_sql(rec, args.table))
        return 0

    if args.cmd == "sql-prior":
        print(prior_sql(args.listing, args.limit, args.table))
        return 0

    if args.cmd == "rest-upsert":
        rec = _load_result(args.record)
        assert_price_free(rec)
        out = rest_upsert(rec, args.table)
        print(json.dumps(out, ensure_ascii=False))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
