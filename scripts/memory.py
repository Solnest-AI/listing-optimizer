#!/usr/bin/env python3
"""
memory.py — local run history for the Listing Optimizer.

Every run produces a compact, PRICE-FREE summary (ALE scores, the title it wrote, hero +
top-5 photos, comp amenity gaps, funnel snapshot, forward occupancy, cadence items
refreshed) and upserts it into state/history.jsonl on (listing_slug, run_date). The next
run reads it back as the trend baseline.

ZERO-PRICING: every record is scanned with render_report.PRICE_NUMBER_RE before it is
written. A price-like term refuses the write.

Subcommands:
  record  --result result.json [--workdir D] [--result-path P] [--season S] [--applied]
          [--cadence-marked a,b] [--history H] [--out record.json] [--no-local]
  prior   --listing SLUG [--history H] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from ale import canonical_dimension
from artifacts import file_lock
from render_report import PRICE_NUMBER_RE, merge_machine_blocks

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HISTORY = ROOT / "state" / "history.jsonl"

# Record fields, in order. listing_slug + run_date are the upsert key.
COLUMNS = [
    "listing_slug", "listing_name", "city", "run_date", "season",
    "ale_total", "ale_scores", "title", "summary_char_count", "applied",
    "photo_hero", "photo_top5", "reshoot_count", "amenity_gaps", "funnel",
    "occupancy_forward_pct", "occupancy_monthly", "cadence_marked", "result_path",
]
_KEY_COLS = ("listing_slug", "run_date")

# Price-free funnel keys. The prose `diagnosis` is dropped: it lives in result.json and
# uses meta-words like "pricing" that belong in the report, not the history.
_FUNNEL_KEEP = (
    "source", "rankbreeze_id", "city_rank", "views_monthly",
    "booking_rate_monthly", "occupancy_monthly", "ctr_vs_similar", "lever_focus",
)


def summarize(result: dict, *, result_path: str | None = None, season: str | None = None,
              applied: bool | None = None, cadence_marked: list[str] | None = None) -> dict:
    """Extract the compact, price-free run summary from a result.json dict.

    CLI/explicit args win over fields embedded in result.json, which win over defaults.
    """
    listing = result.get("listing") or {}
    optimized = result.get("optimized") or {}
    photos = result.get("photos") or {}
    comps = result.get("comps") or {}
    occ = result.get("occupancy") or {}
    funnel = result.get("funnel") or {}

    ale_scores = [{"dimension": canonical_dimension(d.get("dimension", "")), "score": d.get("score")}
                  for d in (result.get("ale_scorecard") or []) if isinstance(d, dict)]
    numeric = [d["score"] for d in ale_scores if isinstance(d.get("score"), (int, float))]

    rec = {
        "listing_slug": listing.get("slug"),
        "listing_name": listing.get("name"),
        "city": listing.get("city"),
        "run_date": result.get("run_date"),
        "season": season if season is not None else result.get("season"),
        "ale_total": round(sum(numeric) / len(numeric), 2) if numeric else None,
        "ale_scores": ale_scores,
        "title": optimized.get("title"),
        "summary_char_count": (len(optimized["summary"].strip())
                               if isinstance(optimized.get("summary"), str)
                               else optimized.get("summary_char_count")),
        "applied": bool(applied) if applied is not None else bool(result.get("applied", False)),
        "photo_hero": photos.get("hero"),
        "photo_top5": photos.get("recommended_top5_order") or [],
        "reshoot_count": len(photos.get("reshoot") or []),
        "amenity_gaps": comps.get("amenity_gaps") or [],
        "funnel": {k: funnel[k] for k in _FUNNEL_KEEP if k in funnel},
        "occupancy_forward_pct": occ.get("forward_pct"),
        "occupancy_monthly": occ.get("monthly") or {},
        "cadence_marked": (cadence_marked if cadence_marked is not None
                           else result.get("cadence_marked") or []),
        "result_path": result_path,
    }
    if rec["cadence_marked"] and not rec["applied"]:
        raise SystemExit("[memory] cadence marks require --applied: a draft report did not change the "
                         "live listing, so nothing was refreshed.")
    if not rec["listing_slug"] or not rec["run_date"]:
        raise SystemExit("[memory] result.json is missing listing.slug or run_date — cannot record.")
    return rec


def assert_price_free(rec: dict) -> None:
    """Refuse to persist a record containing any price NUMBER / currency / rate."""
    hits = [m.group(0) for m in PRICE_NUMBER_RE.finditer(json.dumps(rec, ensure_ascii=False))]
    if hits:
        sys.stderr.write("[memory] ❌ ZERO-PRICING GUARDRAIL TRIPPED — refusing to store record.\n")
        for h in hits[:20]:
            sys.stderr.write(f"   - '{h}'\n")
        raise SystemExit("[memory] price/min-stay term in the run record — fix the optimizer output and re-run.")


def _row_key(rec: dict) -> tuple:
    return tuple(rec.get(c) for c in _KEY_COLS)


def _atomic_write_lines(path: Path, lines: list[str]) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def append_local(rec: dict, history_path: Path) -> str:
    """UPSERT the record into the local history on (listing_slug, run_date).

    Last write wins and the row keeps its position, so a same-day re-run replaces the
    earlier row instead of leaving a stale sibling that `prior` would then serve as the
    previous run. Unparseable lines are copied through verbatim: losing history to a
    repair is a worse bug than a duplicate. Returns "inserted" or "replaced".
    """
    with file_lock(history_path.with_suffix(history_path.suffix + ".lock")):
        history_path.parent.mkdir(parents=True, exist_ok=True)
        key = _row_key(rec)
        payload = json.dumps(rec, ensure_ascii=False)
        if not any(key):  # summarize() prevents this; never collapse keyless rows together
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
            except ValueError:
                matches = False
            if not matches:
                out.append(line)
            elif status == "inserted":  # first match becomes the new record, in place
                out.append(payload)
                status = "replaced"
            # further matches are pre-existing duplicates: collapsed
        if status == "inserted":
            out.append(payload)
        _atomic_write_lines(history_path, out)
        return status


def read_local(history_path: Path) -> list[dict]:
    if not history_path.exists():
        return []
    rows = []
    for line in history_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def prior_runs(listing: str, history_path: Path, limit: int = 3) -> list[dict]:
    """Most-recent-first prior runs for a listing, one per run_date."""
    latest: dict[str, dict] = {}
    for r in read_local(history_path):
        if isinstance(r, dict) and r.get("listing_slug") == listing and r.get("run_date"):
            latest[r["run_date"]] = r
    return sorted(latest.values(), key=lambda r: r["run_date"], reverse=True)[:limit]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Listing Optimizer local run history.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_rec = sub.add_parser("record", help="summarize a result.json into a price-free record and upsert it")
    p_rec.add_argument("--result", required=True)
    p_rec.add_argument("--workdir", default=None,
                       help="output/<DATE>/<SLUG> — merge the machine blocks (photos, occupancy, "
                            "comps) from disk so the record captures hero/top5/reshoot/occupancy. "
                            "Defaults to the directory containing --result.")
    p_rec.add_argument("--result-path", default=None, help="path to store as a pointer (defaults to --result)")
    p_rec.add_argument("--season", default=None)
    p_rec.add_argument("--applied", action="store_true")
    p_rec.add_argument("--cadence-marked", default=None, help="comma-separated cadence item keys refreshed this run")
    p_rec.add_argument("--history", default=str(DEFAULT_HISTORY))
    p_rec.add_argument("--out", default=None, help="also write the record JSON here")
    p_rec.add_argument("--no-local", action="store_true", help="don't write to local history")

    p_pri = sub.add_parser("prior", help="print most-recent prior runs for a listing")
    p_pri.add_argument("--listing", required=True)
    p_pri.add_argument("--history", default=str(DEFAULT_HISTORY))
    p_pri.add_argument("--limit", type=int, default=3)

    args = ap.parse_args(argv)

    if args.cmd == "record":
        result = json.loads(Path(args.result).read_text(encoding="utf-8"))
        wd = Path(args.workdir) if args.workdir else Path(args.result).parent
        if wd.is_dir():
            try:
                filled = merge_machine_blocks(result, wd)
            except (ValueError, OSError) as e:
                raise SystemExit(f"[memory] invalid working artifacts; refusing to record: {e}") from e
            if filled:
                print(f"[memory] merged from {wd}: {', '.join(filled)}", file=sys.stderr)
        marked = [s.strip() for s in args.cadence_marked.split(",")] if args.cadence_marked else None
        rec = summarize(result, result_path=args.result_path or args.result, season=args.season,
                        applied=True if args.applied else None, cadence_marked=marked)
        assert_price_free(rec)
        if not args.no_local and append_local(rec, Path(args.history)) == "replaced":
            print(f"[memory] REPLACED the existing {rec['listing_slug']} {rec['run_date']} row "
                  f"(same-day re-run, last write wins)", file=sys.stderr)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(rec, ensure_ascii=False))
        return 0

    if args.cmd == "prior":
        print(json.dumps(prior_runs(args.listing, Path(args.history), args.limit), indent=2, ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
