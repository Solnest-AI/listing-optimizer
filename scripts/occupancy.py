#!/usr/bin/env python3
"""
occupancy.py — compute occupancy from Hospitable (the SOURCE OF TRUTH) and
cross-check it against RankBreeze.

Hospitable is the live PMS calendar — the authoritative record of what's actually
booked. RankBreeze occupancy is a scraped estimate; we keep it only as a secondary
cross-check and flag divergence.

INPUT: a Hospitable get_property_calendar response (saved as calendar.json). The
orchestrator MUST strip price/min_stay before saving (see SKILL.md) — this script
also ignores those fields defensively. We read ONLY date + availability + reason.

ZERO-PRICING: never reads price or min_stay. Occupancy (booked vs available nights)
is a demand signal, not a price.

Classification is BOOKING-BIASED: an unavailable night counts as a guest booking
UNLESS its reason is an explicit host block. For an occupancy demand-signal, a
false host-block is worse than a false booking (it would understate demand).

Usage:
  python scripts/occupancy.py --calendar calendar.json \
      --reservations-count 0 \
      --rankbreeze "May:10,Jun:0,Jul:0" \
      --out output/<date>/<slug>/occupancy.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# availability==false reasons that mean a HOST block (excluded from occupancy).
# Everything else that's unavailable is treated as a guest booking.
_BLOCK_HINTS = ("block", "owner", "maintenance", "manual", "unavailable",
                "closed", "imported", "not available", "hold", "prep", "buffer")
_MONTH = {"01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May", "06": "Jun",
          "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec"}


def _load_days(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = (raw.get("data") or raw).get("days") or raw.get("days") or []
    return raw or []


def _norm_month(k: str) -> str:
    """Normalize a month label to the 3-letter form used here (Jun, Jul, …)."""
    k = (k or "").strip()
    if len(k) == 7 and k[4] == "-":          # 2026-06
        return _MONTH.get(k[5:7], k)
    return k[:3].title() if k else k


def _classify(day: dict) -> str:
    """open | booked | blocked — reading ONLY availability + reason (never price).
    Unavailable nights are booked UNLESS the reason is an explicit host block."""
    st = day.get("status")
    if not isinstance(st, dict):             # API shape drift: status as a string
        st = {"reason": str(st) if st is not None else ""}
    available = st.get("available")
    reason = (st.get("reason") or "").lower()
    # NB: "unavailable" contains "avail" — match the whole word, not a substring.
    if available is True or (available is None and reason.strip().startswith("available")):
        return "open"
    return "blocked" if any(h in reason for h in _BLOCK_HINTS) else "booked"


def _occ(booked: int, open_: int) -> float | None:
    denom = booked + open_       # adjusted occupancy: blocked nights excluded
    return round(100 * booked / denom, 1) if denom else None


def compute(days: list[dict]) -> dict:
    months: dict[str, dict] = {}
    totals = {"booked": 0, "open": 0, "blocked": 0}
    reason_counts: dict[str, int] = {}
    for d in days:
        date = d.get("date") or ""
        ym = date[:7]
        cls = _classify(d)
        m = months.setdefault(ym, {"booked": 0, "open": 0, "blocked": 0})
        m[cls] += 1
        totals[cls] += 1
        r = (((d.get("status") if isinstance(d.get("status"), dict) else {}) or {}).get("reason") or "?")
        reason_counts[r] = reason_counts.get(r, 0) + 1

    monthly = {}
    for ym in sorted(months):
        b, o, bl = months[ym]["booked"], months[ym]["open"], months[ym]["blocked"]
        label = _MONTH.get(ym[5:7], ym) if len(ym) == 7 else ym
        monthly[label] = {"occupancy_pct": _occ(b, o), "booked": b, "available": o, "blocked": bl}

    fwd = {"occupancy_pct": _occ(totals["booked"], totals["open"]),
           "booked": totals["booked"], "available": totals["open"], "blocked": totals["blocked"],
           "days": len(days)}
    return {"monthly": monthly, "forward_window": fwd, "reason_counts": reason_counts}


def crosscheck(hosp_monthly: dict, rb_arg: str | None, threshold: float = 15.0) -> dict | None:
    if not rb_arg:
        return None
    rb = {}
    for part in rb_arg.split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            try:
                rb[_norm_month(k)] = float(v.strip().rstrip("%"))
            except ValueError:
                pass
    rows, gaps = [], []
    for month, rb_val in rb.items():
        h = (hosp_monthly.get(month) or {}).get("occupancy_pct")
        gap = abs(h - rb_val) if h is not None else None
        if gap is not None:
            gaps.append(gap)
        rows.append({"month": month, "hospitable_pct": h, "rankbreeze_pct": rb_val,
                     "gap_pts": round(gap, 1) if gap is not None else None})
    if not gaps:                              # no month overlapped — don't fake "agree"
        verdict = "no overlap — RankBreeze labels didn't match Hospitable months; cross-check skipped"
        max_gap = None
    else:
        max_gap = max(gaps)
        verdict = "agree" if max_gap <= threshold else f"DIVERGE (>{threshold:g}pts — trust Hospitable)"
    return {"rows": rows, "max_gap_pts": round(max_gap, 1) if max_gap is not None else None,
            "verdict": verdict,
            "note": "Hospitable is the source of truth; RankBreeze is a scraped estimate."}


def report_block(result: dict, upcoming: int | None, cc: dict | None) -> dict:
    """Flat block ready to drop into result.json['occupancy'] (templates consume this)."""
    fwd = result["forward_window"]
    monthly_flat = {m: (f"{int(round(v['occupancy_pct']))}%" if v.get("occupancy_pct") is not None else "n/a")
                    for m, v in result["monthly"].items()}
    cc_str = "n/a"
    if cc:
        cc_str = cc["verdict"] if cc["max_gap_pts"] is None else f"{cc['verdict']} (max gap {cc['max_gap_pts']}pts)"
    return {"source": "Hospitable", "forward_pct": fwd["occupancy_pct"], "forward_days": fwd["days"],
            "upcoming_reservations": upcoming if upcoming is not None else "n/a",
            "monthly": monthly_flat, "rankbreeze_crosscheck": cc_str}


def main():
    ap = argparse.ArgumentParser(description="Occupancy from Hospitable (source of truth) + RankBreeze cross-check.")
    ap.add_argument("--calendar", required=True, help="Hospitable get_property_calendar JSON (price-stripped)")
    ap.add_argument("--reservations-count", type=int, default=None, help="upcoming reservations (list_reservations meta.total)")
    ap.add_argument("--rankbreeze", default=None, help='e.g. "May:10,Jun:0,Jul:0" for cross-check')
    ap.add_argument("--divergence-threshold", type=float, default=15.0,
                    help="pts gap above which Hospitable vs RankBreeze is flagged DIVERGE (default 15)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    days = _load_days(Path(args.calendar))
    if not days:
        sys.exit("[occupancy] no calendar days found in input")
    result = {"source": "Hospitable get_property_calendar (live PMS — source of truth)"}
    result.update(compute(days))
    if args.reservations_count is not None:
        result["upcoming_reservations"] = args.reservations_count
    cc = crosscheck(result["monthly"], args.rankbreeze, args.divergence_threshold)
    if cc:
        result["rankbreeze_crosscheck"] = cc
    # Ready-to-use block for result.json['occupancy'] — no hand-transform needed.
    result["report_block"] = report_block(result, args.reservations_count, cc)

    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        fwd = result["forward_window"]
        print(f"[occupancy] forward {fwd['days']}d: {fwd['occupancy_pct']}% occ "
              f"({fwd['booked']} booked / {fwd['available']} open / {fwd['blocked']} blocked) → {args.out}")
        # Surface classification so the operator notices if reasons are being guessed.
        nonstd = {r: c for r, c in result["reason_counts"].items() if r not in ("AVAILABLE", "?")}
        if nonstd:
            print(f"[occupancy] non-AVAILABLE reason breakdown (verify classification): {nonstd}")
        if cc:
            print(f"[occupancy] vs RankBreeze: {cc['verdict']}"
                  + (f" (max gap {cc['max_gap_pts']}pts)" if cc['max_gap_pts'] is not None else ""))
    else:
        print(text)


if __name__ == "__main__":
    main()
