#!/usr/bin/env python3
"""Tests that lock the AirROI call count at ONE per fresh pull.

This is the money test. Before 2026-09-20, passing coords AND address (which SKILL.md did
on every single run) fired two paid calls. Measured against the live API, the second call
returned 0 unique comps and an identical top-10. If this regresses, the bill silently
doubles and nothing looks broken.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import airroi_client as ac


class _Spy:
    """Stands in for get_comparables and records how it was called."""

    def __init__(self, results):
        self.results, self.calls = list(results), []

    async def __call__(self, **kw):
        self.calls.append({k: kw.get(k) for k in ("latitude", "longitude", "address")})
        return self.results.pop(0) if self.results else []

    @property
    def n(self):
        return len(self.calls)


def _listing(i):
    return {"listing_info": {"listing_id": i, "listing_name": f"comp {i}"}}


def _run(spy, **kw):
    orig = ac.get_comparables
    ac.get_comparables = spy
    try:
        return asyncio.run(ac.fetch_comps(bedrooms=2, baths=1.0, guests=4, **kw))
    finally:
        ac.get_comparables = orig


def test_coords_and_address_makes_exactly_one_call():
    """The regression that mattered: both given, ONE call, coords wins."""
    spy = _Spy([[_listing(1), _listing(2)]])
    listings, meta = _run(spy, latitude=50.1, longitude=-119.2, address="Sun Peaks, BC")
    assert spy.n == 1, f"expected 1 paid call, made {spy.n}"
    assert meta["calls"] == 1 and meta["path"] == "coords"
    assert spy.calls[0]["address"] is None, "the address must not be sent when coords exist"
    assert len(listings) == 2


def test_address_only_makes_one_call():
    spy = _Spy([[_listing(1)]])
    listings, meta = _run(spy, address="Sun Peaks, BC")
    assert spy.n == 1 and meta["path"] == "address"
    assert spy.calls[0]["address"] == "Sun Peaks, BC"


def test_empty_coord_pool_falls_back_to_address():
    """Probed live: a real market always returns 25, a dead coordinate returns 0. The
    fallback exists for 0, and only for 0."""
    spy = _Spy([[], [_listing(9)]])
    listings, meta = _run(spy, latitude=63.7, longitude=-135.0, address="Whitehorse, YT")
    assert spy.n == 2 and meta["calls"] == 2 and meta["fallback_used"] is True
    assert [line["listing_info"]["listing_id"] for line in listings] == [9]


def test_non_empty_pool_never_triggers_the_fallback():
    spy = _Spy([[_listing(1)], [_listing(2)]])
    listings, meta = _run(spy, latitude=50.1, longitude=-119.2, address="Sun Peaks, BC")
    assert spy.n == 1, "a non-empty pool must NOT make a second call"
    assert meta["fallback_used"] is False


def test_zero_coordinates_are_valid():
    """lat/lng of 0.0 is the Gulf of Guinea, not 'missing' — truthiness would break it."""
    spy = _Spy([[_listing(1)]])
    _run(spy, latitude=0.0, longitude=0.0, address="somewhere")
    assert spy.calls[0]["latitude"] == 0.0, "0.0 coords must be used, not treated as absent"


def test_duplicate_listing_ids_are_deduped():
    spy = _Spy([[_listing(1), _listing(1), _listing(2)]])
    listings, _ = _run(spy, latitude=1.0, longitude=1.0)
    assert len(listings) == 2


def test_no_coords_and_no_address_raises():
    try:
        asyncio.run(ac.fetch_comps(bedrooms=2, baths=1.0, guests=4))
    except ac.AirROIError:
        return
    raise AssertionError("must raise AirROIError when given neither coords nor address")


def test_errors_are_not_swallowed():
    """The old two-call merge hid one call's failure whenever the other succeeded, so a
    dead lookup looked exactly like a success. A single call must propagate."""
    async def boom(**kw):
        raise ac.AirROIError("AirROI 401: bad key")
    orig = ac.get_comparables
    ac.get_comparables = boom
    try:
        asyncio.run(ac.fetch_comps(latitude=1.0, longitude=1.0, bedrooms=2, baths=1.0, guests=4))
    except ac.AirROIError as e:
        assert "401" in str(e)
        return
    finally:
        ac.get_comparables = orig
    raise AssertionError("a failed AirROI call must raise, not return silently empty")


def test_cache_key_groups_by_110m_cell_not_by_address():
    """The key is the QUERY, snapped to a ~110m cell (3dp).

    Why 3dp and not coarser: measured against the live API from one point, a query 110m
    away returned an IDENTICAL 25-listing pool, 1.1km away shared only 21/25, and 2.2km
    away 19/25. So a 3dp cell is the largest grid over which the pool is still the same
    thing. Coarser would quietly serve a 16%-different comp pool from cache.

    Two units in the same building share a key (same cell). Two listings a kilometre
    apart do NOT, and should not — that is a different pool, and a miss only costs the
    call we would have made anyway.
    """
    import types

    import pull_comps as pc

    def args(lat, lng, addr, br=2, g=4):
        return types.SimpleNamespace(lat=lat, lng=lng, address=addr, bedrooms=br,
                                     baths=1.0, guests=g)
    # Same 110m cell, different street address and unit → one paid call for both.
    a = pc._cache_key(args(50.877940, -119.908530, "unit A, Sun Peaks"), None)
    b = pc._cache_key(args(50.877989, -119.908512, "unit B, Sun Peaks"), None)
    assert a == b, "same 110m cell + same size must share a key regardless of address"
    # The address is not in the key at all: it is only ever an empty-pool fallback.
    assert a == pc._cache_key(args(50.877940, -119.908530, "totally different text"), None)
    # Different cell, different radius, different size, different market → separate keys.
    assert a != pc._cache_key(args(50.887940, -119.908530, "1km away"), None)
    assert a != pc._cache_key(args(50.877940, -119.908530, "unit A"), 5)
    assert a != pc._cache_key(args(50.877940, -119.908530, "unit A", br=4, g=8), None)
    assert a != pc._cache_key(args(51.500000, -119.900000, "elsewhere"), None)


def test_address_only_query_keys_on_the_address():
    """With no coords there is no cell, so the normalised address carries the key."""
    import types

    import pull_comps as pc

    def args(addr):
        return types.SimpleNamespace(lat=None, lng=None, address=addr, bedrooms=2,
                                     baths=1.0, guests=4)
    assert pc._cache_key(args("Sun Peaks, BC"), None) == pc._cache_key(args("  sun peaks, bc  "), None)
    assert pc._cache_key(args("Sun Peaks, BC"), None) != pc._cache_key(args("Whistler, BC"), None)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("✅ AirROI call-count tests passed")
