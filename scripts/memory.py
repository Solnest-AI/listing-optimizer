#!/usr/bin/env python3
"""
memory.py — run-history memory for the Listing Optimizer.

Every run produces a compact, PRICE-FREE summary of what the optimizer did
(ALE scores, the title/summary it wrote, hero + top-5 photos, comp amenity gaps,
the price-free funnel snapshot, forward occupancy, what cadence items refreshed).
That summary is:

  • ALWAYS appended to a local history file   → state/history.jsonl   (zero setup)
  • OPTIONALLY mirrored to Supabase           → table listing_optimizer_runs

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
import urllib.parse
import urllib.request
from pathlib import Path

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
        "summary_char_count": optimized.get("summary_char_count"),
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
def append_local(rec: dict, history_path: Path) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


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
    p_rec.add_argument("--result-path", default=None, help="path to store as a pointer (defaults to --result)")
    p_rec.add_argument("--season", default=None)
    p_rec.add_argument("--applied", action="store_true")
    p_rec.add_argument("--cadence-marked", default=None, help="comma-separated cadence item keys refreshed this run")
    p_rec.add_argument("--history", default=str(DEFAULT_HISTORY))
    p_rec.add_argument("--out", default=None, help="also write the record JSON here")
    p_rec.add_argument("--no-local", action="store_true", help="don't append to local history")

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
            append_local(rec, Path(args.history))
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(rec, ensure_ascii=False))
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
