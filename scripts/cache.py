#!/usr/bin/env python3
"""
cache.py — tiny local TTL cache shared by the paid/slow steps of the pipeline.

Why this exists: nothing in the optimizer used to be cached, so every run re-bought
the same AirROI comp pool and re-scored the same unchanged photos with Gemini.
Measured churn on a real listing (boho-bliss, 4 runs): 1 day apart = byte-identical
comp pool (10/10 top comps, 0.0pt amenity drift); 7 days = 9/10 + 1.5pt; 8 weeks =
8/10 + 2.3pt. A two-week TTL is well inside the noise the ALE rubric can resolve.

Store: state/cache/<namespace>.json (state/ is gitignored — this never leaves the box).
Shape: {"<key>": {"saved": "<iso8601 UTC>", "value": <any JSON>}}

Deliberately dependency-free, single-process, and fail-open: a corrupt or unreadable
cache file is treated as a miss, never as an error. A cache must never be able to
break a run — the worst it may do is cost a call we would have made anyway.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from artifacts import file_lock

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("LO_CACHE_DIR") or (ROOT / "state" / "cache"))

# Env kill switch — set LO_NO_CACHE=1 to bypass reads AND writes for a whole run.
def disabled() -> bool:
    return (os.environ.get("LO_NO_CACHE") or "").strip().lower() in ("1", "true", "yes")


def key_for(**parts) -> str:
    """Stable short key from keyword parts. Floats are rounded by the CALLER."""
    blob = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:20]


def _path(namespace: str) -> Path:
    safe = "".join(c for c in namespace if c.isalnum() or c in "-_") or "default"
    return CACHE_DIR / f"{safe}.json"


def _load(namespace: str) -> dict:
    p = _path(namespace)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}  # missing or corrupt == miss, never an error


def _save(namespace: str, data: dict) -> None:
    p = _path(namespace)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace: a crash mid-write must not leave a half-file that the next
        # run reads as a miss for every key it already paid for.
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass  # a cache that cannot write is still a working pipeline


def get(namespace: str, key: str, ttl_days: float):
    """Return the cached value, or None on miss / expiry / disabled."""
    if disabled() or ttl_days <= 0:
        return None
    entry = _load(namespace).get(key)
    if not isinstance(entry, dict) or "value" not in entry:
        return None
    try:
        saved = datetime.fromisoformat(str(entry.get("saved")))
        if saved.tzinfo is None:
            saved = saved.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    if datetime.now(timezone.utc) - saved > timedelta(days=ttl_days):
        return None
    return entry["value"]


def age_days(namespace: str, key: str) -> float | None:
    """How old the cached entry is, for honest reporting ('cache hit, 3d old')."""
    entry = _load(namespace).get(key)
    if not isinstance(entry, dict):
        return None
    try:
        saved = datetime.fromisoformat(str(entry.get("saved")))
        if saved.tzinfo is None:
            saved = saved.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    return round((datetime.now(timezone.utc) - saved).total_seconds() / 86400, 2)


def put(namespace: str, key: str, value, max_entries: int = 500) -> None:
    """Store a value. Evicts oldest entries past max_entries so the file stays small."""
    put_many(namespace, {key: value}, max_entries)


def get_many(namespace: str, keys: list[str], ttl_days: float) -> dict:
    """One file read for N keys — the photo cache asks about 30 at a time."""
    if disabled() or ttl_days <= 0:
        return {}
    data = _load(namespace)
    now = datetime.now(timezone.utc)
    out = {}
    for k in keys:
        entry = data.get(k)
        if not isinstance(entry, dict) or "value" not in entry:
            continue
        try:
            saved = datetime.fromisoformat(str(entry.get("saved")))
            if saved.tzinfo is None:
                saved = saved.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if now - saved <= timedelta(days=ttl_days):
            out[k] = entry["value"]
    return out


def put_many(namespace: str, items: dict, max_entries: int = 2000) -> None:
    """One file write for N keys."""
    if disabled() or not items:
        return
    try:
        with file_lock(_path(namespace).with_suffix(".lock")):
            data = {k: v for k, v in _load(namespace).items() if isinstance(v, dict)}
            stamp = datetime.now(timezone.utc).isoformat()
            for k, v in items.items():
                data[k] = {"saved": stamp, "value": v}
            if len(data) > max_entries:
                for k in sorted(data, key=lambda k: str(data[k].get("saved")))[: len(data) - max_entries]:
                    data.pop(k, None)
            _save(namespace, data)
    except (OSError, TimeoutError):
        pass  # cache persistence is optional; the paid result still reaches the report


def clear(namespace: str | None = None) -> None:
    """Drop one namespace, or the whole cache dir."""
    try:
        if namespace:
            _path(namespace).unlink(missing_ok=True)
        elif CACHE_DIR.exists():
            for f in CACHE_DIR.glob("*.json"):
                f.unlink(missing_ok=True)
    except Exception:
        pass
