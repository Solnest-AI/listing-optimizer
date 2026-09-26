import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import airroi_client as ac
import pull_comps as pc


def args(**values):
    return SimpleNamespace(lat=50.1, lng=-119.2, address="Test town", bedrooms=2,
        baths=1, guests=4, radius=None, top=10, market="Test town", cache_ttl_days=14,
        no_cache=False, exclude_listing_id=values.get("exclude_listing_id"))


def raw(lid, **extra):
    return {"listing_info": {"listing_id": lid, "listing_name": f"c{lid}"},
        "property_details": {"amenities": ["sauna"]}, "ratings": {"num_reviews": 12},
        "performance_metrics": {"ttm_days_reserved": 100, "ttm_occupancy": .5,
                                "ttm_revenue": 98765, "ttm_adr": 123}, **extra}


def test_subject_is_excluded_from_its_own_comp_pool(monkeypatch, tmp_path):
    monkeypatch.setattr(pc.cache, "CACHE_DIR", tmp_path)
    async def fetch(**kw):
        return [raw(123), raw(456)], {"calls": 1, "path": "coords", "fallback_used": False}
    monkeypatch.setattr(ac, "fetch_comps", fetch)
    result = asyncio.run(pc._run(args(exclude_listing_id="123")))
    assert [c["listing_id"] for c in result["top_comps"]] == [456]
    assert result["comp_count"] == 1


def test_shared_cache_contains_only_price_free_comp_data(monkeypatch, tmp_path):
    monkeypatch.setattr(pc.cache, "CACHE_DIR", tmp_path)
    async def fetch(**kw):
        return [raw(123)], {"calls": 1, "path": "coords", "fallback_used": False}
    monkeypatch.setattr(ac, "fetch_comps", fetch)
    asyncio.run(pc._run(args()))
    serialized = "".join(p.read_text() for p in tmp_path.glob("*.json"))
    assert "98765" not in serialized and "ttm_adr" not in serialized
    result = asyncio.run(pc._run(args()))
    assert result["fetch"]["calls"] == 0


def test_missing_listings_field_is_not_a_paid_empty_pool_retry(monkeypatch):
    monkeypatch.setattr(ac, "_key", lambda: "fake-test-key")
    async def call():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"unexpected": []}))) as client:
            return await ac.get_comparables(latitude=1, longitude=2, bedrooms=2, baths=1, guests=4, client=client)
    with pytest.raises(ac.AirROIError, match="listings"):
        asyncio.run(call())


def test_bad_location_and_capacity_are_rejected_before_paid_call(monkeypatch):
    async def should_not_call(**kw):
        pytest.fail("invalid input reached the paid endpoint")
    monkeypatch.setattr(ac, "get_comparables", should_not_call)
    with pytest.raises(ac.AirROIError):
        asyncio.run(ac.fetch_comps(latitude=float("nan"), longitude=2, bedrooms=-1, baths=1, guests=0))


def test_numeric_and_string_ids_dedupe(monkeypatch):
    async def fetch(**kw):
        return [raw(123), raw("123")]
    monkeypatch.setattr(ac, "get_comparables", fetch)
    rows, _ = asyncio.run(ac.fetch_comps(latitude=1, longitude=2, bedrooms=2, baths=1, guests=4))
    assert len(rows) == 1


# ── The listing being optimized, from the comps call we already pay for ──────
MUS = "https://a0.muscache.com/im/pictures/hosting/Hosting-1/original/"


def subject_raw(lid, n_photos=32, count=None):
    return {"listing_info": {"listing_id": lid, "listing_name": "Walk to Olde Town",
                             "description": "Summary text", "cover_photo_url": MUS + "p0.jpeg",
                             "photos_count": count if count is not None else n_photos,
                             "photo_urls": [f"{MUS}p{i}.jpeg" for i in range(n_photos)],
                             "guest_favorite": True},
            "host_info": {"superhost": True},
            "property_details": {"amenities": ["Wifi", "Dryer"]},
            "ratings": {"num_reviews": 155, "rating_overall": 4.98},
            "pricing_info": {"cleaning_fee": 175}, "booking_settings": {"min_nights": 2},
            "performance_metrics": {"ttm_days_reserved": 200, "ttm_revenue": 55555}}


def test_subject_listing_is_kept_from_the_comps_call_without_pricing(monkeypatch, tmp_path):
    """Ryan 2026-09-26: AirROI returns the listing being optimized inside the comps pool we
    already pay for; the code excluded it and threw its photos, copy and amenities away."""
    monkeypatch.setattr(pc.cache, "CACHE_DIR", tmp_path)
    async def fetch(**kw):
        return [subject_raw(123), raw(456)], {"calls": 1, "path": "coords", "fallback_used": False}
    monkeypatch.setattr(ac, "fetch_comps", fetch)
    r = asyncio.run(pc._run(args(exclude_listing_id="123")))
    s = r["subject_listing"]
    assert s["title"] == "Walk to Olde Town" and len(s["photo_urls"]) == 32 and s["photos_count"] == 32
    assert s["amenities"] == ["Wifi", "Dryer"] and s["num_reviews"] == 155 and s["guest_favorite"] is True
    assert s["superhost"] is True
    assert [c["listing_id"] for c in r["top_comps"]] == [456], "subject must still be excluded from comps"
    blob = str(r) + "".join(p.read_text() for p in tmp_path.glob("*.json"))
    assert "55555" not in blob and "cleaning_fee" not in blob and "min_nights" not in blob
    # A cached rerun still carries the subject listing, with zero paid calls.
    r2 = asyncio.run(pc._run(args(exclude_listing_id="123")))
    assert r2["fetch"]["calls"] == 0 and len(r2["subject_listing"]["photo_urls"]) == 32


def test_subject_missing_from_pool_costs_one_listing_call(monkeypatch, tmp_path):
    monkeypatch.setattr(pc.cache, "CACHE_DIR", tmp_path)
    async def fetch(**kw):
        return [raw(456)], {"calls": 1, "path": "coords", "fallback_used": False}
    async def get_listing(lid, **kw):
        return subject_raw(int(lid), n_photos=10)
    monkeypatch.setattr(ac, "fetch_comps", fetch)
    monkeypatch.setattr(ac, "get_listing", get_listing)
    r = asyncio.run(pc._run(args(exclude_listing_id="123")))
    assert len(r["subject_listing"]["photo_urls"]) == 10
    assert r["fetch"]["calls"] == 2 and r["fetch"]["subject_path"] == "listing endpoint"
