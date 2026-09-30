"""The Gemini scoring model and its request config.

2026-09-30: gemini-2.5-flash answered every scoring request with HTTP 404 ("no longer
available to new users") while check_keys.py, which only read the model's metadata, said
"ok". All 94 photos of a listing went to the Claude-vision fallback. Gemini 3 also needs a
different config: it thinks at medium by default (thought tokens eat maxOutputTokens) and
Google warns against a temperature below 1.0.
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import analyze_photos as ap
import check_keys
from test_gemini_quota import _ok
from test_mvp_photos import photos


def _bodies(monkeypatch, model, responses=()):
    responses, posts = list(responses), []
    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, content=b"image", headers={"content-type": "image/jpeg"})
        posts.append(req)
        return responses.pop(0) if responses else _ok(req)
    real = httpx.AsyncClient
    monkeypatch.setattr(ap.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    scored = asyncio.run(ap.score_photos(photos(10), model, "fake-test-key", concurrency=1))
    return scored, posts


def test_the_default_model_is_not_the_retired_one():
    assert not ap.BUILTIN_MODEL.startswith("gemini-2.")
    assert check_keys.BUILTIN_MODEL == ap.BUILTIN_MODEL, "check_keys probes the model that scores"


def test_gemini_3_uses_minimal_thinking_and_the_default_temperature(monkeypatch):
    scored, posts = _bodies(monkeypatch, "gemini-3.8-flash")
    assert all(p["scored"] for p in scored)
    for req in posts:
        config = json.loads(req.content)["generationConfig"]
        assert config["thinkingConfig"] == {"thinkingLevel": "minimal"}
        assert "temperature" not in config
        assert config["maxOutputTokens"] == 512 * 5 + 256


def test_gemini_2_5_flash_keeps_its_zero_budget_config():
    config = ap._generation_config("gemini-2.5-flash", {}, 5)
    assert config["thinkingConfig"] == {"thinkingBudget": 0} and config["temperature"] == 0


def test_a_404_names_the_model_and_is_not_retried(monkeypatch):
    scored, posts = _bodies(monkeypatch, "gemini-2.5-flash", [httpx.Response(404)])
    assert len(posts) == 1, "later batches are cancelled, not sent"
    assert not any(p["scored"] for p in scored)
    assert "gemini-2.5-flash" in scored[0]["error"] and "GEMINI_MODEL" in scored[0]["error"]


def _check_gemini(monkeypatch, status):
    calls = []
    def fake_post(url, **kw):
        calls.append(url)
        return httpx.Response(status, json={})
    monkeypatch.setattr(check_keys, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(check_keys.httpx, "post", fake_post)
    monkeypatch.setattr(check_keys.httpx, "get", lambda *a, **k: httpx.Response(200))
    for var in ("HOSPITABLE_TOKEN", "HOSPITABLE_API_KEY", "GEMINI_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key")
    row = next(r for r in check_keys.check() if r[0] == "Gemini")
    return row, calls


def test_check_keys_probes_the_scoring_model_with_count_tokens(monkeypatch):
    (_, msg, ok), calls = _check_gemini(monkeypatch, 200)
    assert ok and msg == "ok"
    assert calls == [f"https://generativelanguage.googleapis.com/v1beta/models/{ap.BUILTIN_MODEL}:countTokens"]


def test_check_keys_fails_a_retired_model(monkeypatch):
    (_, msg, ok), _ = _check_gemini(monkeypatch, 404)
    assert not ok
    assert ap.BUILTIN_MODEL in msg and "GEMINI_MODEL" in msg
