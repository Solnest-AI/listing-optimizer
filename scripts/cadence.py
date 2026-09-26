#!/usr/bin/env python3
"""
cadence.py — track the ALE refresh cadence per listing.

State: state/refresh_state.json — { "<listing-slug>": { "<item>": "YYYY-MM-DD", ... } }

Cadence (from references/ale-rubric.md):
  title 3-4wk · photo rotation 2-3wk · captions 1-2mo · seasonal swap 3-4mo ·
  the_space rewrite ~4mo · full reshoot 2.5-3yr · conversion audit monthly.

Usage:
  python scripts/cadence.py --listing my-listing --check --out cadence.json
  python scripts/cadence.py --listing my-listing --mark title,photo_rotation,captions
  python scripts/cadence.py --listing my-listing --mark-all
  (optional --today YYYY-MM-DD to override 'now' for testing)
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "refresh_state.json"
HISTORY_PATH = ROOT / "state" / "history.jsonl"

# item key -> (label, interval_days)
CADENCE = {
    "title":           ("Title",                28),
    "photo_rotation":  ("Photo rotation",       18),
    "captions":        ("Photo captions",       45),
    "the_space":       ("“The Space” rewrite", 120),
    "seasonal_swap":   ("Seasonal swap",       105),
    "full_reshoot":    ("Full reshoot",       1000),
    "conversion_audit": ("Conversion audit",    30),
}


def _load() -> dict:
    """Fail-open: a corrupt state file means 'never refreshed', not a failed run."""
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _today(arg: str | None) -> date:
    return datetime.strptime(arg, "%Y-%m-%d").date() if arg else date.today()


def _applied_dates(listing: str) -> set[str] | None:
    """Run dates confirmed applied for this listing, or None when it has no history to check.

    A mark made on a draft run is not a refresh: boho-bliss showed 0/7 items due for marks
    made on 2026-09-20, a draft whose title never reached Airbnb."""
    try:
        lines = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    seen, applied = False, set()
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("listing_slug") == listing:
            seen = True
            if row.get("applied") is True and row.get("run_date"):
                applied.add(str(row["run_date"]))
    return applied if seen else None


def check(listing: str, today: date) -> list[dict]:
    state = _load().get(listing, {})
    confirmed = _applied_dates(listing)
    rows = []
    for key, (label, interval) in CADENCE.items():
        last_s = state.get(key)
        try:
            last = datetime.strptime(str(last_s), "%Y-%m-%d").date() if last_s else None
        except ValueError:
            last = None
        if last and confirmed is not None and last_s not in confirmed:
            rows.append({"item": label, "last": f"{last_s} (draft, not confirmed applied)",
                         "due": today.isoformat(), "status": "DUE"})
        elif last:
            due = last + timedelta(days=interval)
            status = "DUE" if today >= due else "ok"
            rows.append({"item": label, "last": last_s, "due": due.isoformat(), "status": status})
        else:
            # No record is not the same as overdue: the tool cannot know when photos were
            # shot or copy was last written outside it, so it does not claim a due date.
            rows.append({"item": label, "last": "no record", "due": "unknown", "status": "no record"})
    return rows


def mark(listing: str, items: list[str], today: date) -> list[str]:
    state = _load()
    rec = state.setdefault(listing, {})
    marked = []
    for it in items:
        if it in CADENCE:
            rec[it] = today.isoformat()
            marked.append(it)
    _save(state)
    return marked


def main():
    ap = argparse.ArgumentParser(description="ALE refresh cadence tracker.")
    ap.add_argument("--listing", required=True)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--mark", default=None, help="comma-separated item keys to mark done today")
    ap.add_argument("--mark-all", action="store_true", help="mark every cadence item done today")
    ap.add_argument("--today", default=None, help="override today (YYYY-MM-DD)")
    ap.add_argument("--out", default=None, help="write the --check result JSON here")
    args = ap.parse_args()
    today = _today(args.today)

    if args.mark_all:
        m = mark(args.listing, list(CADENCE.keys()), today)
        print(f"[cadence] marked all done {today}: {', '.join(m)}")
    elif args.mark:
        m = mark(args.listing, [s.strip() for s in args.mark.split(",")], today)
        print(f"[cadence] marked {today}: {', '.join(m) or '(none valid)'}")

    if args.check or args.out:
        rows = check(args.listing, today)
        payload = {"listing": args.listing, "as_of": today.isoformat(), "due": rows}
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            n_due = sum(1 for r in rows if r["status"] == "DUE")
            print(f"[cadence] {n_due}/{len(rows)} items DUE → {args.out}")
        else:
            for r in rows:
                flag = "⚠️ DUE" if r["status"] == "DUE" else r["status"]
                print(f"  {r['item']:<22} last={r['last']:<12} due={r['due']:<12} {flag}")


if __name__ == "__main__":
    main()
