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

import asyncio
import os
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
        raise AirROIError(f"AirROI {r.status_code}: {data.get('message') or r.text[:160]}")
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
    return data.get("listings") or []


async def fetch_comps(*, latitude=None, longitude=None, address=None,
                      bedrooms: int, baths: float, guests: int,
                      currency: str = "usd", radius=None) -> list[dict]:
    """Fetch comps by coords AND address (when both given), merge + dedupe by listing_id.
    Raises AirROIError only if every attempt failed."""
    has_coords = latitude is not None and longitude is not None
    async with httpx.AsyncClient() as client:
        coros = []
        if has_coords:
            coros.append(get_comparables(latitude=latitude, longitude=longitude,
                                         bedrooms=bedrooms, baths=baths, guests=guests,
                                         currency=currency, radius=radius, client=client))
        if address:
            coros.append(get_comparables(address=address, bedrooms=bedrooms, baths=baths,
                                         guests=guests, currency=currency, radius=radius, client=client))
        if not coros:
            raise AirROIError("provide coords (latitude+longitude) or address")
        results = await asyncio.gather(*coros, return_exceptions=True)

    merged: dict = {}
    errors = []
    for r in results:
        if isinstance(r, Exception):
            errors.append(r)
            continue
        for listing in r:
            lid = (listing.get("listing_info") or {}).get("listing_id")
            if lid and lid not in merged:
                merged[lid] = listing
    if not merged and errors:
        e = errors[0]  # surface a consistent error type (wrap raw network errors)
        raise e if isinstance(e, AirROIError) else AirROIError(f"AirROI request failed: {e}")
    return list(merged.values())
