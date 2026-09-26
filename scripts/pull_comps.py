#!/usr/bin/env python3
"""
pull_comps.py — fetch competitor comps for a subject listing via AirROI.

This is the ONLY paid AirROI call in the optimizer. AIRROI_API_KEY comes from the
project .env. The subject itself comes free from the user's PMS.

ZERO-PRICING GUARDRAIL (non-negotiable):
  - We never output price / ADR / min-stay / revenue / RevPAR.
  - "Top performers" are ranked by DEMAND signals (nights booked, occupancy,
    review count), never by revenue.

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

import airroi_client
import cache

# Comp pools move slowly (measured: 7 days = 9/10 top comps + 1.5pt amenity drift).
# 14 days stays inside the noise the ALE rubric can resolve.
CACHE_TTL_DAYS = 14
CACHE_NS = "airroi_comps"


# ── Pricing strip: whitelist only NON-PRICE fields ────────────────────
# min-nights fields are deliberately EXCLUDED (min-stay is in the zero-pricing rule).
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
    # Kept for the listing being optimized, which AirROI returns inside its own comps pool:
    # the live Airbnb title, summary, cover and photo order. Never prices or stay rules.
    return {
        "listing_id": lid,
        "name": li.get("listing_name"),
        "description": li.get("description"),
        "cover_photo_url": li.get("cover_photo_url"),
        "photos_count": li.get("photos_count"),
        "photo_urls": [u for u in li.get("photo_urls") or [] if isinstance(u, str)],
        "guest_favorite": li.get("guest_favorite"),
        "superhost": (raw.get("host_info") or {}).get("superhost"),
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


def _subject_listing(c: dict | None) -> dict | None:
    """The listing being optimized, as AirROI last saw it on Airbnb. Price-free by construction
    (built from the cleaned record)."""
    if not c:
        return None
    r = c.get("ratings") or {}
    return {"listing_id": c.get("listing_id"), "title": c.get("name"), "description": c.get("description"),
            "cover_photo_url": c.get("cover_photo_url"), "photos_count": c.get("photos_count"),
            "photo_urls": c.get("photo_urls") or [], "amenities": c.get("amenities") or [],
            "rating_overall": r.get("rating_overall"), "num_reviews": r.get("num_reviews"),
            "guest_favorite": c.get("guest_favorite"), "superhost": c.get("superhost")}


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


def _cache_key(args, radius) -> str:
    """Key on WHAT WE ASK FOR, not on the listing.

    Coordinates snap to a ~110m cell (3dp): measured live, a query 110m away returned an
    identical 25-listing pool while 1.1km away shared only 21/25. Two units in the same
    building share one paid call; two listings a kilometre apart do not.

    The address is NOT in the key: it is only a fallback for an empty coord pool.
    """
    return cache.key_for(
        lat=round(args.lat, 3) if args.lat is not None else None,
        lng=round(args.lng, 3) if args.lng is not None else None,
        addr=(args.address or "").strip().casefold() if args.lat is None or args.lng is None else None,
        bedrooms=args.bedrooms, baths=args.baths, guests=args.guests, radius=radius,
    )


async def _run(args) -> dict:
    # Widen the search radius for large properties (thin comp pools), like the
    # original pipeline did — overridable with --radius.
    radius = args.radius if args.radius is not None else (5 if args.bedrooms >= 5 else None)
    if radius:
        print(f"[pull_comps] using {radius}-mile radius for {args.bedrooms}BR property", file=sys.stderr)

    ck = _cache_key(args, radius)
    # Empty-coordinate fallbacks depend on address. They must not poison the
    # shared coordinate pool for another address in the same grid cell.
    fallback_ck = cache.key_for(query=ck, fallback=(args.address or "").strip().casefold())
    ttl = 0 if args.no_cache else args.cache_ttl_days
    cached = cache.get(CACHE_NS, ck, ttl)
    hit_key = ck
    if cached is None and args.address:
        cached = cache.get(CACHE_NS, fallback_ck, ttl)
        hit_key = fallback_ck
    if cached is not None:
        age = cache.age_days(CACHE_NS, hit_key)
        print(f"[pull_comps] CACHE HIT ({age}d old, ttl {ttl}d) — 0 AirROI calls. "
              f"--no-cache to force a fresh pull.", file=sys.stderr)
        comps_raw, meta = cached, {"calls": 0, "path": "cache", "cache_age_days": age}
    else:
        comps_raw, meta = await airroi_client.fetch_comps(
            latitude=args.lat, longitude=args.lng, address=args.address,
            bedrooms=args.bedrooms, baths=args.baths, guests=args.guests, radius=radius,
        )
        print(f"[pull_comps] {meta['calls']} AirROI call(s) [{meta['path']}]", file=sys.stderr)
    # Re-key by listing_id after cleaning; this also covers the cache path, where
    # comps_raw came off disk rather than from the client.
    cleaned: dict = {}
    for c in comps_raw:
        comp = _clean_comp(c) if "listing_info" in c else c
        lid = comp.get("listing_id")
        if lid and str(lid) not in cleaned:
            cleaned[str(lid)] = comp
    # Cache only the whitelist; older raw cache entries are cleaned on reuse.
    if cleaned and not args.no_cache and cached is None:
        save_key = fallback_ck if meta.get("fallback_used") else ck
        cache.put(CACHE_NS, save_key, list(cleaned.values()))
    excluded_id = str(getattr(args, "exclude_listing_id", None) or "")
    comps = sorted((c for lid, c in cleaned.items() if lid != excluded_id), key=_demand_key, reverse=True)

    # The subject's own AirROI record: free when it is in its comps pool (the usual case),
    # one listing call when it is not.
    subject = cleaned.get(excluded_id) if excluded_id else None
    if excluded_id and (subject is None or "photo_urls" not in subject):
        try:
            subject = _clean_comp(await airroi_client.get_listing(excluded_id))
            meta = {**meta, "calls": meta.get("calls", 0) + 1, "subject_path": "listing endpoint"}
        except airroi_client.AirROIError as e:
            print(f"[pull_comps] subject listing unavailable from AirROI ({e})", file=sys.stderr)
            subject = None
    elif subject is not None:
        meta = {**meta, "subject_path": "comps pool (no extra call)"}

    top = comps[: args.top]
    return {
        "subject_query": {
            "lat": args.lat, "lng": args.lng, "address": args.address,
            "market": args.market, "bedrooms": args.bedrooms,
            "baths": args.baths, "guests": args.guests, "radius": radius,
        },
        "fetch": meta,  # paid-call count + path (or cache hit) — honest cost reporting
        "comp_count": len(comps),
        "ranking_basis": "demand only: nights booked, then occupancy, then review count (no monetary signals).",
        "top_comps": top,
        "market_amenity_frequency": _amenity_frequency(comps),
        "comp_title_samples": [c["name"] for c in top if c.get("name")],
        "subject_listing": _subject_listing(subject),
        "_guardrail_note": "Monetary and stay-length fields intentionally omitted per the zero-pricing rule.",
    }


def main():
    ap = argparse.ArgumentParser(description="Pull AirROI comps (price-free) for a subject listing.")
    ap.add_argument("--lat", type=float, default=None)
    ap.add_argument("--lng", type=float, default=None)
    ap.add_argument("--address", type=str, default=None)
    ap.add_argument("--market", type=str, default=None)
    ap.add_argument("--bedrooms", type=int, required=True)
    ap.add_argument("--baths", type=float, required=True)
    ap.add_argument("--guests", type=int, required=True)
    ap.add_argument("--radius", type=int, default=None, help="miles; widen for thin markets")
    ap.add_argument("--top", type=int, default=10, help="how many top comps to keep")
    ap.add_argument("--exclude-listing-id", default=None, help="subject Airbnb ID, excluded after cache lookup")
    ap.add_argument("--out", type=str, default=None, help="write JSON here (else stdout)")
    ap.add_argument("--no-cache", action="store_true",
                    help="force a fresh paid pull, ignoring (and not writing) the cache")
    ap.add_argument("--cache-ttl-days", type=float, default=CACHE_TTL_DAYS,
                    help=f"reuse a cached comp pool this recent (default {CACHE_TTL_DAYS})")
    args = ap.parse_args()

    if not ((args.lat is not None and args.lng is not None) or args.address):
        ap.error("provide --lat/--lng or --address")
    if args.top < 1 or (args.radius is not None and args.radius < 1):
        ap.error("--top and --radius must be positive")

    try:
        result = asyncio.run(_run(args))
    except (airroi_client.AirROIError, OSError, ValueError) as e:
        sys.exit(f"[pull_comps] pipeline failed: {e}")

    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        f = result["fetch"]
        print(f"[pull_comps] {result['comp_count']} comps → {out}  "
              f"(top {len(result['top_comps'])} kept, {f['calls']} paid call(s) [{f['path']}])")
    else:
        print(text)


if __name__ == "__main__":
    main()
