#!/usr/bin/env python3
"""Tests for the TTL cache that keeps AirROI and Gemini calls from being re-bought.

A cache bug here shows up as either money (a miss that should have hit) or a stale
report (a hit that should have expired), so the TTL boundary and the fail-open
behaviour are both locked down.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def _fresh_cache(tmp):
    """Import cache.py bound to a throwaway dir (never touches the real state/)."""
    os.environ["LO_CACHE_DIR"] = str(tmp)
    os.environ.pop("LO_NO_CACHE", None)
    for m in ("cache",):
        sys.modules.pop(m, None)
    import cache
    return cache


def test_roundtrip_and_miss():
    with tempfile.TemporaryDirectory() as tmp:
        c = _fresh_cache(Path(tmp))
        assert c.get("ns", "nope", 14) is None, "unknown key must miss"
        c.put("ns", "k1", {"comps": [1, 2, 3]})
        assert c.get("ns", "k1", 14) == {"comps": [1, 2, 3]}


def test_ttl_expiry_boundary():
    with tempfile.TemporaryDirectory() as tmp:
        c = _fresh_cache(Path(tmp))
        c.put("ns", "k", "v")
        # Backdate the entry 15 days — a 14-day TTL must treat it as a miss.
        p = c._path("ns")
        data = json.loads(p.read_text())
        data["k"]["saved"] = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
        p.write_text(json.dumps(data))
        assert c.get("ns", "k", 14) is None, "15d-old entry must expire under a 14d TTL"
        assert c.get("ns", "k", 30) == "v", "same entry must still hit under a 30d TTL"
        assert c.age_days("ns", "k") >= 15


def test_ttl_zero_disables():
    with tempfile.TemporaryDirectory() as tmp:
        c = _fresh_cache(Path(tmp))
        c.put("ns", "k", "v")
        assert c.get("ns", "k", 0) is None, "ttl 0 must always miss (--no-cache path)"


def test_env_kill_switch():
    with tempfile.TemporaryDirectory() as tmp:
        c = _fresh_cache(Path(tmp))
        c.put("ns", "k", "v")
        os.environ["LO_NO_CACHE"] = "1"
        assert c.disabled()
        assert c.get("ns", "k", 14) is None, "LO_NO_CACHE must bypass reads"
        c.put("ns", "k2", "v2")
        os.environ.pop("LO_NO_CACHE")
        assert c.get("ns", "k2", 14) is None, "LO_NO_CACHE must bypass writes too"


def test_corrupt_file_fails_open():
    with tempfile.TemporaryDirectory() as tmp:
        c = _fresh_cache(Path(tmp))
        c.put("ns", "k", "v")
        c._path("ns").write_text("{not json at all")
        assert c.get("ns", "k", 14) is None, "corrupt cache must read as a miss, not raise"
        c.put("ns", "k", "v2")  # and must recover on the next write
        assert c.get("ns", "k", 14) == "v2"


def test_key_is_stable_and_order_independent():
    with tempfile.TemporaryDirectory() as tmp:
        c = _fresh_cache(Path(tmp))
        assert c.key_for(a=1, b=2) == c.key_for(b=2, a=1), "key must not depend on kwarg order"
        assert c.key_for(a=1) != c.key_for(a=2)


def test_get_many_put_many():
    with tempfile.TemporaryDirectory() as tmp:
        c = _fresh_cache(Path(tmp))
        c.put_many("photos", {"a": {"avg": 4}, "b": {"avg": 3}})
        got = c.get_many("photos", ["a", "b", "missing"], 30)
        assert got == {"a": {"avg": 4}, "b": {"avg": 3}}, "get_many returns only the hits"


def test_eviction_keeps_newest():
    with tempfile.TemporaryDirectory() as tmp:
        c = _fresh_cache(Path(tmp))
        for i in range(5):
            c.put("ns", f"k{i}", i, max_entries=3)
        remaining = json.loads(c._path("ns").read_text())
        assert len(remaining) <= 3
        assert "k4" in remaining, "the newest entry must survive eviction"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("✅ cache tests passed")
