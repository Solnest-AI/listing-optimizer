#!/usr/bin/env python3
"""
pull_comps.py — fetch competitor comps for a subject listing via AirROI.

SELF-CONTAINED: uses the vendored scripts/airroi_client.py (no external repo,
no sys.path injection). AIRROI_API_KEY comes from the project .env / env.
This is the ONLY paid (AirROI) call in the optimizer. The subject listing is
pulled FREE from the user's PMS (AirROI supplies the subject only in
no-PMS / Airbnb-only external mode).

ZERO-PRICING GUARDRAIL (non-negotiable):
  - We never output price / ADR / min-stay / revenue / RevPAR.
  - "Top performers" are ranked by DEMAND signals (nights booked, occupancy,
    review count) — NOT by revenue — so no pricing logic enters the pipeline.

Usage:
  python scripts/pull_comps.py \
      --lat 12.3456 --lng -65.4321 \
      --address "123 Main St, Your City, Country" \
      --market "Your City" --bedrooms 3 --baths 2 --guests 7 \
      --out output/2026-06-06/my-listing/comps.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Vendored client lives next to this script — plain same-dir import (no repo path).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import airroi_client  # noqa: E402


# ── Pricing strip: whitelist only NON-PRICE fields ────────────────────
# NB: min-nights fields are deliberately EXCLUDED — min-stay is named in the
# zero-pricing hard rule. We keep occupancy + nights-booked + length-of-stay
# (demand signals), nothing monetary and nothing about stay-length *requirements*.
_PERF_KEEP = (
    "ttm_occupancy", "ttm_adjusted_occupancy", "ttm_days_reserved",
    "ttm_available_days", "ttm_total_days", "ttm_avg_length_of_stay",
    "l90d_occupancy", "l90d_adjusted_occupancy", "l90d_days_reserved",
    "l90d_avg_length_of_stay",
)
_RATING_KEEP = (
    "num_reviews", "rating_overall", "rating_accuracy", "rating_checkin",
    "rating_cleanliness", "rating_communication", "rating_location", "rating_value",
)


def _clean_perf(pm: dict) -> dict:
    return {k: pm.get(k) for k in _PERF_KEEP if pm.get(k) is not None}


def _clean_comp(raw: dict) -> dict:
    """Reduce a raw AirROI listing dict to ALE-relevant, price-free fields."""
    li = raw.get("listing_info") or {}
    pd = raw.get("property_details") or {}
    r = raw.get("ratings") or {}
    pm = raw.get("performance_metrics") or {}
    lid = li.get("listing_id")
    return {
        "listing_id": lid,
        "name": li.get("listing_name"),
        "room_type": li.get("room_type"),
        "airbnb_url": f"https://www.airbnb.com/rooms/{lid}" if lid else None,
        "bedrooms": pd.get("bedrooms"),
        "beds": pd.get("beds"),
        "baths": pd.get("baths"),
        "guests": pd.get("guests"),
        "amenities": pd.get("amenities") or [],
        "ratings": {k: r.get(k) for k in _RATING_KEEP if r.get(k) is not None},
        "performance": _clean_perf(pm),  # demand/occupancy only — no $
    }


def _demand_key(comp: dict) -> tuple:
    """Rank winners by DEMAND, never by revenue. nights booked → occupancy → reviews."""
    perf = comp.get("performance") or {}
    ratings = comp.get("ratings") or {}
    return (
        perf.get("ttm_days_reserved") or perf.get("l90d_days_reserved") or 0,
        perf.get("ttm_occupancy") or perf.get("l90d_occupancy") or 0,
        ratings.get("num_reviews") or 0,
        ratings.get("rating_overall") or 0,
    )


def _amenity_frequency(comps: list[dict]) -> list[dict]:
    """How common each amenity is across the comp pool — feeds ALE gap analysis.
    Counts each amenity at most once per comp (set) and case-folds to merge variants."""
    n = len(comps) or 1
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for c in comps:
        seen = set()
        for a in c.get("amenities") or []:
            key = " ".join(str(a).split()).casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            counts[key] = counts.get(key, 0) + 1
            labels.setdefault(key, str(a).strip())
    rows = [{"amenity": labels[k], "count": ct, "pct": int(100 * ct / n + 0.5)}
            for k, ct in counts.items()]
    rows.sort(key=lambda x: (-x["count"], x["amenity"]))
    return rows


async def _run(args) -> dict:
    # Widen the search radius for large properties (thin comp pools), like the
    # original pipeline did — overridable with --radius.
    radius = args.radius if args.radius is not None else (5 if args.bedrooms >= 5 else None)
    if radius:
        print(f"[pull_comps] using {radius}-mile radius for {args.bedrooms}BR property", file=sys.stderr)

    comps_raw = await airroi_client.fetch_comps(
        latitude=args.lat, longitude=args.lng, address=args.address,
        bedrooms=args.bedrooms, baths=args.baths, guests=args.guests, radius=radius,
    )
    # airroi_client.fetch_comps() already owns the uniqueness invariant (it merges
    # the coords + address result sets by listing_id). Re-keying here is a cheap
    # belt-and-suspenders after _clean_comp() — not a second source of truth.
    cleaned: dict = {}
    for c in comps_raw:
        lid = (c.get("listing_info") or {}).get("listing_id")
        if lid and lid not in cleaned:
            cleaned[lid] = _clean_comp(c)
    comps = sorted(cleaned.values(), key=_demand_key, reverse=True)

    top = comps[: args.top]
    return {
        "subject_query": {
            "lat": args.lat, "lng": args.lng, "address": args.address,
            "market": args.market, "bedrooms": args.bedrooms,
            "baths": args.baths, "guests": args.guests, "radius": radius,
        },
        "comp_count": len(comps),
        "ranking_basis": "demand only: nights booked, then occupancy, then review count (no monetary signals).",
        "top_comps": top,
        "market_amenity_frequency": _amenity_frequency(comps),
        "comp_title_samples": [c["name"] for c in top if c.get("name")],
        "_guardrail_note": "Monetary and stay-length fields intentionally omitted per the zero-pricing rule.",
    }


def main():
    ap = argparse.ArgumentParser(description="Pull AirROI comps (price-free) for a subject listing.")
    ap.add_argument("--lat", type=float, default=None)
    ap.add_argument("--lng", type=float, default=None)
    ap.add_argument("--address", type=str, default=None)
    ap.add_argument("--short-address", dest="short_address", type=str, default=None)
    ap.add_argument("--market", type=str, default=None)
    ap.add_argument("--bedrooms", type=int, required=True)
    ap.add_argument("--baths", type=float, required=True)
    ap.add_argument("--guests", type=int, required=True)
    ap.add_argument("--radius", type=int, default=None, help="miles; widen for thin markets")
    ap.add_argument("--top", type=int, default=10, help="how many top comps to keep")
    ap.add_argument("--out", type=str, default=None, help="write JSON here (else stdout)")
    args = ap.parse_args()

    if not ((args.lat is not None and args.lng is not None) or args.address):
        ap.error("provide --lat/--lng or --address")

    try:
        result = asyncio.run(_run(args))
    except Exception as e:
        sys.exit(f"[pull_comps] pipeline failed: {e}")

    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"[pull_comps] {result['comp_count']} comps → {out}  (top {len(result['top_comps'])} kept)")
    else:
        print(text)


if __name__ == "__main__":
    main()
