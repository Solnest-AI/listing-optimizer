#!/usr/bin/env python3
"""Hospitable reads retry a rate limit or server blip instead of dying on the first one.

_get used to sys.exit on the first 429/5xx, failing the whole subject step (and so the run)
on a transient error. Bounded: three attempts, Retry-After honoured but capped.
"""
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import hospitable_api as h


class _Resp:
    def __init__(self, code, headers=None):
        self.status_code, self.headers, self.text = code, headers or {}, "x"

    def json(self):
        return {"data": {"ok": True}}


def _patch(monkeypatch, responses):
    calls, sleeps = [], []

    def fake_get(*a, **k):
        calls.append(1)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(h.httpx, "get", fake_get)
    monkeypatch.setattr(h.time, "sleep", sleeps.append)
    monkeypatch.setenv("HOSPITABLE_TOKEN", "t")
    return calls, sleeps


def test_rate_limit_then_success(monkeypatch):
    calls, sleeps = _patch(monkeypatch, [_Resp(429, {"Retry-After": "2"}), _Resp(200)])
    assert h._get("/x") == {"data": {"ok": True}}
    assert len(calls) == 2 and sleeps == [2.0]


def test_transport_error_then_success(monkeypatch):
    calls, _ = _patch(monkeypatch, [httpx.ConnectError("down"), _Resp(503), _Resp(200)])
    assert h._get("/x")["data"]["ok"] is True
    assert len(calls) == 3


def test_gives_up_after_three_attempts(monkeypatch):
    calls, _ = _patch(monkeypatch, [_Resp(500), _Resp(500), _Resp(500)])
    with pytest.raises(SystemExit, match="HTTP 500"):
        h._get("/x")
    assert len(calls) == 3


def test_auth_failure_is_not_retried(monkeypatch):
    calls, _ = _patch(monkeypatch, [_Resp(401)])
    with pytest.raises(SystemExit, match="401"):
        h._get("/x")
    assert len(calls) == 1


def test_retry_after_is_capped(monkeypatch):
    _, sleeps = _patch(monkeypatch, [_Resp(429, {"Retry-After": "3600"}), _Resp(200)])
    h._get("/x")
    assert sleeps == [h.MAX_WAIT]
