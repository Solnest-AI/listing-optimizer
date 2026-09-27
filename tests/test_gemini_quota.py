"""Gemini 429s in a portfolio run.

2026-09-26: a 429 got 1s and 2s of backoff. A per-minute quota needs up to a minute, so a
big portfolio would fail its photo batches and push every listing into the Claude-vision
fallback, the most expensive path in the tool. A daily quota cannot be waited out at all
and must stop cleanly instead of burning retries. Gemini says which one it is in the
error body (RetryInfo.retryDelay, QuotaFailure quotaId).
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import analyze_photos as ap
from test_mvp_photos import photos, row


def _quota_429(retry_delay=None, quota_id="GenerateRequestsPerMinutePerProjectPerModel-FreeTier"):
    details = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{"quotaMetric": "generativelanguage.googleapis.com/generate_content_requests",
                                "quotaId": quota_id}]}]
    if retry_delay:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay})
    return httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                               "message": "quota", "details": details}})


def _ok(req):
    parts = json.loads(req.content)["contents"][0]["parts"]
    orders = [int(p["text"].split("PHOTO_ORDER=")[1].split()[0]) for p in parts if "PHOTO_ORDER=" in p.get("text", "")]
    return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"text": json.dumps([row(i) for i in orders])}]}}], "usageMetadata": {"totalTokenCount": 10}})


def _run(monkeypatch, responses, n=5, concurrency=2):
    posts, sleeps = [], []
    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, content=b"image", headers={"content-type": "image/jpeg"})
        posts.append(req)
        nxt = responses.pop(0) if responses else _ok
        return nxt(req) if callable(nxt) else nxt
    real = httpx.AsyncClient
    monkeypatch.setattr(ap.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    async def fake_sleep(s):
        sleeps.append(s)
    monkeypatch.setattr(ap.asyncio, "sleep", fake_sleep)
    stats = {"api_calls": 0}
    scored = asyncio.run(ap.score_photos(photos(n), "gemini-2.5-flash", "fake-test-key",
                                         concurrency=concurrency, stats=stats))
    return scored, posts, sleeps, stats


def test_per_minute_quota_waits_the_delay_gemini_asks_for(monkeypatch):
    scored, posts, sleeps, _ = _run(monkeypatch, [_quota_429("37s")])
    assert sleeps == [37.0]
    assert len(posts) == 2 and all(p["scored"] for p in scored)


def test_a_bare_429_waits_long_enough_for_a_minute_window(monkeypatch):
    scored, _, sleeps, _ = _run(monkeypatch, [httpx.Response(429)])
    assert sleeps and sleeps[0] >= 15, sleeps
    assert all(p["scored"] for p in scored)


def test_daily_quota_stops_without_retrying_and_says_so(monkeypatch):
    daily = _quota_429("50000s", quota_id="GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    scored, posts, sleeps, stats = _run(monkeypatch, [daily], n=30, concurrency=1)
    assert len(posts) == 1, "a daily quota must not be retried or hit again by later batches"
    assert sleeps == []
    assert not any(p["scored"] for p in scored)
    assert stats["quota_exhausted"] == "daily"
    assert "daily quota" in scored[0]["error"]


def test_a_wait_longer_than_the_cap_is_treated_as_exhausted(monkeypatch):
    scored, posts, sleeps, stats = _run(monkeypatch, [_quota_429("3600s")], n=5, concurrency=1)
    assert sleeps == [] and len(posts) == 1
    assert stats["quota_exhausted"] == "long_wait"
