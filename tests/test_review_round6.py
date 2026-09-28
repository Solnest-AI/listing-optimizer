"""Full-codebase review (round 6): repeated paid calls, repeated reads, crash-on-bad-input."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import airroi_client as ac
import build_digest as bd
import cache
import occupancy
import pull_comps as pc
import run_pipeline as rp
from test_mvp_comps import args, raw, subject_raw


def test_subject_missing_from_pool_is_billed_once_then_cached(monkeypatch, tmp_path):
    """The subject's listing call ran on EVERY run when AirROI left it out of its own pool,
    even when the pool itself came from cache."""
    monkeypatch.setattr(pc.cache, "CACHE_DIR", tmp_path)
    listing_calls = []

    async def fetch(**kw):
        return [raw(456)], {"calls": 1, "path": "coords", "fallback_used": False}

    async def get_listing(lid, **kw):
        listing_calls.append(lid)
        return subject_raw(int(lid), n_photos=10)

    monkeypatch.setattr(ac, "fetch_comps", fetch)
    monkeypatch.setattr(ac, "get_listing", get_listing)
    first = asyncio.run(pc._run(args(exclude_listing_id="123")))
    second = asyncio.run(pc._run(args(exclude_listing_id="123")))
    assert first["fetch"]["calls"] == 2
    assert second["fetch"]["calls"] == 0, "a warm rerun must make no paid call at all"
    assert len(listing_calls) == 1
    assert len(second["subject_listing"]["photo_urls"]) == 10


def test_a_failed_subject_listing_call_is_still_counted(monkeypatch, tmp_path):
    """AirROI bills the attempt; the reported call count must include it."""
    monkeypatch.setattr(pc.cache, "CACHE_DIR", tmp_path)

    async def fetch(**kw):
        return [raw(456)], {"calls": 1, "path": "coords", "fallback_used": False}

    async def get_listing(lid, **kw):
        raise ac.AirROIError("HTTP 500")

    monkeypatch.setattr(ac, "fetch_comps", fetch)
    monkeypatch.setattr(ac, "get_listing", get_listing)
    r = asyncio.run(pc._run(args(exclude_listing_id="123")))
    assert r["fetch"]["calls"] == 2 and r["subject_listing"] is None


def test_get_with_age_returns_first_fresh_key(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    cache.put("ns", "b", [2])
    key, value, age = cache.get_with_age("ns", ["a", "b"], 14)
    assert (key, value) == ("b", [2]) and 0 <= age < 1
    assert cache.get_with_age("ns", ["a"], 14) is None
    assert cache.get_with_age("ns", ["b"], 0) is None


def test_calendar_with_a_data_list_loads_instead_of_crashing(tmp_path):
    p = tmp_path / "calendar.json"
    days = [{"date": "2026-10-01", "status": {"available": True}}]
    p.write_text(json.dumps({"data": days}), encoding="utf-8")
    assert occupancy._load_days(p) == days
    p.write_text(json.dumps({"data": {"days": days}}), encoding="utf-8")
    assert occupancy._load_days(p) == days
    p.write_text(json.dumps({"data": "nope"}), encoding="utf-8")
    assert occupancy._load_days(p) == []


@pytest.mark.parametrize("total", ["lots", 12.5, None, -1])
def test_digest_survives_a_non_count_review_total(tmp_path, total):
    (tmp_path / "reviews.json").write_text(json.dumps({
        "data": [{"reviewed_at": "2026-09-01", "public": {"rating": 5, "review": "Great"}}],
        "_pull": {"total_available": total}}), encoding="utf-8")
    assert "1 pulled of 1 lifetime" in bd.build(tmp_path)


def test_account_channels_copy_is_shared_and_rejects_bad_json(tmp_path):
    src, dst = tmp_path / "a" / "channels.json", tmp_path / "b" / "channels.json"
    assert rp._copy_atomic(src, dst) is False, "a missing shared copy means: pull it"
    src.parent.mkdir()
    src.write_text("{broken", encoding="utf-8")
    assert rp._copy_atomic(src, dst) is False and not dst.exists()
    src.write_text('{"connected_platforms": ["airbnb"]}', encoding="utf-8")
    assert rp._copy_atomic(src, dst) is True
    assert json.loads(dst.read_text(encoding="utf-8"))["connected_platforms"] == ["airbnb"]
    assert [f.name for f in dst.parent.iterdir()] == ["channels.json"], "no temp file left behind"


def test_a_cached_subject_is_never_labelled_as_pulled_today(monkeypatch, tmp_path):
    """A fresh pool with a cached subject record: the gallery must carry the subject's age."""
    monkeypatch.setattr(pc.cache, "CACHE_DIR", tmp_path / "cache")
    calls = {"n": 0}

    async def fetch(**kw):
        calls["n"] += 1
        return [raw(456 + calls["n"])], {"calls": 1, "path": "coords", "fallback_used": False}

    async def get_listing(lid, **kw):
        return subject_raw(int(lid), n_photos=10)

    monkeypatch.setattr(ac, "fetch_comps", fetch)
    monkeypatch.setattr(ac, "get_listing", get_listing)
    asyncio.run(pc._run(args(exclude_listing_id="123")))
    # Force a fresh pool on the second run while the subject stays cached.
    pc.cache.clear(pc.CACHE_NS)
    fresh_pool = asyncio.run(pc._run(args(exclude_listing_id="123")))
    assert fresh_pool["fetch"]["path"] == "coords"
    assert "subject_cache_age_days" in fresh_pool["fetch"]

    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "comps.json").write_text(json.dumps(fresh_pool), encoding="utf-8")
    (wd / "images.json").write_text(json.dumps({"data": [{"url": f"https://x/{i}.jpg", "order": i}
                                                         for i in range(10)]}), encoding="utf-8")
    status, _ = rp.airroi_gallery_step(wd)
    assert status == "ok"
    gallery = json.loads((wd / "live_gallery.json").read_text(encoding="utf-8"))
    assert gallery["fetched_at"].startswith("AirROI data cached"), gallery["fetched_at"]
