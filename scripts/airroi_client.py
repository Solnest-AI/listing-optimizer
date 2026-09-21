"""
Self-contained AirROI client — the ONLY external data dependency in this system.

Vendored into the project so the Listing Optimizer needs NOTHING outside this
folder except an API key (no external "AIRROI Comping Agent" repo, no sys.path
injection). Reads AIRROI_API_KEY from the environment or the project .env.

Used by scripts/pull_comps.py for competitor comps only. The caller strips all
pricing — this client merely fetches the comparable-listings endpoint.

API: GET https://api.airroi.com/listings/comparables  (header: X-API-KEY)
Get a key: https://www.airroi.com/api/developer/activate
"""
from __future__ import annotations

import os
import math
from pathlib import Path

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass  # dotenv optional; env vars still work

BASE = os.environ.get("AIRROI_BASE_URL", "https://api.airroi.com")
try:
    TIMEOUT = int(os.environ.get("AIRROI_TIMEOUT", "30"))
except ValueError:  # a bad AIRROI_TIMEOUT in .env shouldn't crash the import
    TIMEOUT = 30


class AirROIError(RuntimeError):
    """Raised on a missing key or a non-2xx AirROI response."""


def _key() -> str:
    # Defensive: strip inline "# comment" remnants some .env editors leave in values.
    k = os.environ.get("AIRROI_API_KEY", "").split("#")[0].strip()
    if not k:
        raise AirROIError(
            "AIRROI_API_KEY not set. Put it in the project .env (see .env.example). "
            "Get a key at https://www.airroi.com/api/developer/activate"
        )
    return k


async def _get(endpoint: str, params: dict, client: httpx.AsyncClient) -> dict:
    params = {k: v for k, v in params.items() if v is not None}
    r = await client.get(BASE.rstrip("/") + endpoint, params=params,
                         headers={"X-API-KEY": _key()}, timeout=TIMEOUT)
    try:
        data = r.json()
    except Exception as e:
        raise AirROIError(f"AirROI non-JSON response ({r.status_code}): {e}")
    if r.status_code >= 400:
        raise AirROIError(f"AirROI HTTP {r.status_code}; check access or retry later")
    if not isinstance(data, dict):
        raise AirROIError("AirROI response is not an object")
    return data


async def get_comparables(*, latitude=None, longitude=None, address=None,
                          bedrooms: int, baths: float, guests: int,
                          currency: str = "usd", radius=None,
                          client: httpx.AsyncClient) -> list[dict]:
    """Up to 25 comparable listings ranked by relevance. Coords OR address."""
    params: dict = {"bedrooms": bedrooms, "baths": baths, "guests": guests, "currency": currency}
    if radius is not None:
        params["radius"] = radius
    # NB: "is not None", not truthiness — 0.0 is a valid coordinate.
    has_coords = latitude is not None and longitude is not None
    if address and not has_coords:
        params["address"] = address
    else:
        params["latitude"] = latitude
        params["longitude"] = longitude
    data = await _get("/listings/comparables", params, client)
    listings = data.get("listings")
    if not isinstance(listings, list) or any(not isinstance(x, dict) for x in listings):
        raise AirROIError("AirROI response has no valid listings array; upstream schema changed")
    return listings


async def fetch_comps(*, latitude=None, longitude=None, address=None,
                      bedrooms: int, baths: float, guests: int,
                      currency: str = "usd", radius=None) -> tuple[list[dict], dict]:
    """ONE paid call. Coords are preferred; address is a FALLBACK, not a second lookup.

    Returns (listings, meta) where meta records which calls were actually made, so the
    caller can report the true paid-call count instead of guessing.

    WHY ONE CALL (measured 2026-09-20 against the live API, boho-bliss / Prince George BC):
      coords-only        -> 25 listings
      street-address     -> 25 listings, 25 overlap, **0 unique**
      city-address       -> 25 listings, 25 overlap, **0 unique**
      Ranking both pools through pull_comps._demand_key gave a byte-identical top-10 in
      the same order; market amenity frequency moved 1.7pts. Firing both (the old
      behaviour, which SKILL.md triggered on every run by passing --lat/--lng AND
      --address) doubled the bill for nothing the report prints.

    WHY THE FALLBACK IS ON AN EMPTY POOL, NOT A THIN ONE: probed six markets incl. a
    6BR and an 8BR in a small ski village and a 5BR in a rural town — every real market
    saturated at or just under the 25 cap (the endpoint relaxes the match to fill it).
    Treat 25 as a CEILING, not a guarantee: a 2026-09-20 live run reported 24, because the
    subject listing is excluded from its own comp pool (see pull_comps --exclude-listing-id).
    AirROI happily returns the subject as one of its own comparables, which made the
    demand ranking circular and fed the subject's own title into "learn from comp titles".
    Remote
    coordinates with nothing nearby returned 0. So the failure mode is 0, never 5-15,
    and the address retry only earns its money when the coord lookup comes back empty.

    Raises AirROIError if the only attempt(s) failed — no more silent half-failures
    (the old two-call merge swallowed one call's error whenever the other succeeded).
    """
    has_coords = latitude is not None and longitude is not None
    for value, low, high, name in ((latitude, -90, 90, "latitude"),
                                   (longitude, -180, 180, "longitude")):
        if value is not None and (not isinstance(value, (int, float))
                                  or not math.isfinite(value) or not low <= value <= high):
            raise AirROIError(f"invalid {name}")
    if (type(bedrooms) is not int or bedrooms < 0 or type(guests) is not int or guests < 1
            or not isinstance(baths, (int, float)) or not math.isfinite(baths) or baths < 0):
        raise AirROIError("invalid property capacity; studios use bedrooms=0, guests must be positive")
    if not has_coords and not address:
        raise AirROIError("provide coords (latitude+longitude) or address")

    meta = {"calls": 0, "path": None, "fallback_used": False}
    async with httpx.AsyncClient() as client:
        if has_coords:
            meta["calls"] += 1
            meta["path"] = "coords"
            listings = await get_comparables(
                latitude=latitude, longitude=longitude, bedrooms=bedrooms, baths=baths,
                guests=guests, currency=currency, radius=radius, client=client)
            # Empty pool = the coordinate has nothing near it. An address may geocode
            # to a market centroid that does. This is the ONLY second call we ever make.
            if not listings and address:
                meta["calls"] += 1
                meta["fallback_used"] = True
                meta["path"] = "coords->address(empty-pool fallback)"
                listings = await get_comparables(
                    address=address, bedrooms=bedrooms, baths=baths, guests=guests,
                    currency=currency, radius=radius, client=client)
        else:
            meta["calls"] += 1
            meta["path"] = "address"
            listings = await get_comparables(
                address=address, bedrooms=bedrooms, baths=baths, guests=guests,
                currency=currency, radius=radius, client=client)

    # Dedupe by listing_id (the API can repeat a listing across relaxed match tiers).
    merged: dict = {}
    for listing in listings:
        lid = (listing.get("listing_info") or {}).get("listing_id")
        if lid and str(lid) not in merged:
            merged[str(lid)] = listing
    return list(merged.values()), meta
