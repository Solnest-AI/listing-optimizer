"""Full-codebase review (round 6): repeated paid calls, repeated reads, crash-on-bad-input."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_digest as bd
import cache
import occupancy
import run_pipeline as rp


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
