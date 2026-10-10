"""Photo scoring on a Gemini key made after the summit (S2-004, S2-006, S2-019).

2026-09-29: Google closed gemini-2.5-flash to new keys. Every scoring request answered HTTP 404
("no longer available to new users ... use models/gemini-3.8-flash"), every photo fell to the
Claude-vision fallback, and check_keys.py still said OK because it only read the model's
metadata (which still answers 200). Students who switched to gemini-3.8-flash by hand got a
model that thinks by default (~17k thought tokens on one run) inside a 512-token-per-photo
output budget.

Measured live 2026-10-10 with one real scoring request each: gemini-3.8-flash REJECTS
thinkingLevel "minimal" (HTTP 400 INVALID_ARGUMENT, the setting open PR #2 sends) and ACCEPTS
thinkingBudget 0 with temperature 0 (HTTP 200, zero thought tokens, a valid score).
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import analyze_photos as ap
import cache
import check_keys
from test_gemini_quota import _ok
from test_mvp_photos import photos

# Google's exact answers. The 400 was captured live on 2026-10-10; the 404 is the text students
# got on 2026-09-29/30 (quoted in PR #2).
MINIMAL_400 = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message":
    "Thinking level MINIMAL is not supported for this model. Please retry with other thinking level."}}
RETIRED_404 = {"error": {"code": 404, "status": "NOT_FOUND", "message":
    "This model models/gemini-2.5-flash is no longer available to new users. Please update your "
    "code to use models/gemini-3.8-flash"}}
# Google's 429 for a model with no free tier on the key (Bert, gemini-pro-latest on a free key).
# Its violations name PerDay quota ids, which is what made it read as "daily quota exhausted".
NO_FREE_TIER_429 = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message":
    "You exceeded your current quota, please check your plan and billing details.\n"
    "* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, "
    "limit: 0, model: gemini-pro\n"
    "* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_input_token_count, "
    "limit: 0, model: gemini-pro",
    "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
        {"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
         "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
         "quotaDimensions": {"location": "global", "model": "gemini-pro"}},
        {"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
         "quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
         "quotaDimensions": {"location": "global", "model": "gemini-pro"}}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "21s"}]}}


def _score(monkeypatch, model, responses=(), n=10):
    responses, posts = list(responses), []
    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, content=b"image", headers={"content-type": "image/jpeg"})
        posts.append(req)
        return responses.pop(0) if responses else _ok(req)
    real = httpx.AsyncClient
    monkeypatch.setattr(ap.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    async def no_sleep(_):
        return None
    monkeypatch.setattr(ap.asyncio, "sleep", no_sleep)
    stats = {"api_calls": 0}
    scored = asyncio.run(ap.score_photos(photos(n), model, "fake-test-key", concurrency=1, stats=stats))
    return scored, posts, stats


# ── The model and its settings ─────────────────────────────────────────
def test_the_default_model_is_the_one_google_points_new_keys_to(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert ap.BUILTIN_MODEL == "gemini-3.8-flash"
    assert ap.configured_model() == "gemini-3.8-flash"


@pytest.mark.parametrize("value, expected", [("gemini-3.5-flash", "gemini-3.5-flash"),
                                             ("", "gemini-3.8-flash"),
                                             ("  # pick one later", "gemini-3.8-flash")])
def test_gemini_model_still_overrides_and_a_blank_line_does_not(monkeypatch, value, expected):
    monkeypatch.setenv("GEMINI_MODEL", value)
    assert ap.configured_model() == expected


@pytest.mark.parametrize("model", ["gemini-3.8-flash", "gemini-flash-latest", "gemini-2.5-flash",
                                   "gemini-3.8-flash-lite"])
def test_every_flash_model_turns_thinking_off_with_a_zero_budget(model):
    config = ap.generation_config(model, {"type": "string"}, 768)
    assert config["thinkingConfig"] == {"thinkingBudget": 0}
    assert "thinkingLevel" not in json.dumps(config), "3.8-flash rejects thinkingLevel minimal (HTTP 400)"
    assert config["temperature"] == 0 and config["maxOutputTokens"] == 768


def test_the_scoring_request_for_3_8_flash_turns_thinking_off(monkeypatch):
    """Anna, 2026-09-29: 3.8-flash spent ~17k thought tokens because only gemini-2.5-flash*
    names got the zero budget."""
    scored, posts, _ = _score(monkeypatch, "gemini-3.8-flash")
    assert all(p["scored"] for p in scored)
    for req in posts:
        config = json.loads(req.content)["generationConfig"]
        assert config["thinkingConfig"] == {"thinkingBudget": 0}
        assert config["maxOutputTokens"] == 512 * 5 + 256


def test_scores_cached_before_thinking_was_turned_off_are_not_reused(tmp_path, monkeypatch):
    """Students who set GEMINI_MODEL=gemini-3.8-flash as the workaround cached scores made with
    thinking on. The rubric version is in the cache key, so those must miss."""
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    url = "https://images.test/0.jpg"
    old = cache.key_for(url=url, model="gemini-3.8-flash", rubric_v=3)
    cache.put_many(ap.CACHE_NS, {old: {k: v for k, v in _valid_row().items() if k != "order"}})
    cached, todo = ap.split_cached([{"order": 0, "url": url, "caption": ""}], "gemini-3.8-flash", 120)
    assert cached == [] and len(todo) == 1


def _valid_row():
    from test_mvp_photos import row
    r = row(0)
    r["avg"] = 4.0
    return r


# ── A rejected setting stops loudly instead of hiding in the fallback ───
def test_a_rejected_setting_cancels_the_rest_and_quotes_google(monkeypatch):
    scored, posts, stats = _score(monkeypatch, "gemini-3.8-flash", [httpx.Response(400, json=MINIMAL_400)])
    assert len(posts) == 1, "the same setting fails every batch; later batches must not be sent"
    assert not any(p["scored"] for p in scored)
    assert "Thinking level MINIMAL is not supported" in stats["config_error"]
    assert "gemini-3.8-flash" in stats["config_error"]


def test_a_retired_model_names_itself_and_the_fix(monkeypatch):
    scored, posts, stats = _score(monkeypatch, "gemini-2.5-flash", [httpx.Response(404, json=RETIRED_404)])
    assert len(posts) == 1
    assert "gemini-2.5-flash" in stats["config_error"] and "GEMINI_MODEL" in stats["config_error"]
    assert "no longer available to new users" in stats["config_error"]


def _run_main(monkeypatch, tmp_path, response):
    (tmp_path / "images.json").write_text(json.dumps({"data": photos(6)}))
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key")
    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, content=b"image", headers={"content-type": "image/jpeg"})
        return response
    real = httpx.AsyncClient
    monkeypatch.setattr(ap.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(sys, "argv", ["analyze_photos.py", "--photos", str(tmp_path / "images.json"),
                                      "--out", str(tmp_path / "photo_scores.json"), "--no-cache"])
    with pytest.raises(SystemExit) as e:
        ap.main()
    return e.value


@pytest.mark.parametrize("response", [httpx.Response(400, json=MINIMAL_400),
                                      httpx.Response(404, json=RETIRED_404)])
def test_a_settings_error_is_not_sent_to_the_claude_vision_fallback(monkeypatch, tmp_path, response):
    """Before: every batch failed with a bare "Gemini HTTP 400", photo_fallback.json was written
    and Claude quietly scored all the photos itself, on every run, forever."""
    exit_ = _run_main(monkeypatch, tmp_path, response)
    assert not (tmp_path / ap.FALLBACK_MANIFEST).exists()
    msg = str(exit_.code)
    assert exit_.code != 0 and "GEMINI_MODEL" in msg and "not scored" in msg
    assert "—" not in msg, "student-facing text has no em-dashes"
    out = json.loads((tmp_path / "photo_scores.json").read_text(encoding="utf-8"))
    assert out["scored_count"] == 0 and out["config_error"]


# ── 429 from a model with no free tier (S2-019) ─────────────────────────
def test_a_model_with_no_free_tier_is_not_called_a_daily_quota(monkeypatch):
    scored, posts, stats = _score(monkeypatch, "gemini-pro-latest", [httpx.Response(429, json=NO_FREE_TIER_429)])
    assert len(posts) == 1
    assert stats["quota_exhausted"] == "no_free_tier"
    assert "free tier" in scored[0]["error"] and "Flash" in scored[0]["error"]
    assert "daily quota" not in scored[0]["error"]


def test_a_real_daily_quota_still_reads_as_daily():
    daily = {"error": {"code": 429, "message": "Quota exceeded for metric: x, limit: 250, model: gemini-3.8-flash",
                       "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
                           {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}
    assert ap._rate_limit(httpx.Response(429, json=daily)) == (None, "daily")


# ── check_keys: a real request, not the model's metadata ────────────────
def _check(monkeypatch, response, model=None):
    calls = []
    def fake_post(url, **kw):
        calls.append((url, kw.get("json")))
        return response
    def no_get(url, **kw):
        raise AssertionError(f"Gemini must not be checked with a metadata GET ({url})")
    monkeypatch.setattr(check_keys, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(check_keys.httpx, "post", fake_post)
    monkeypatch.setattr(check_keys.httpx, "get", no_get)
    for var in ("HOSPITABLE_TOKEN", "HOSPITABLE_API_KEY", "GEMINI_MODEL"):
        monkeypatch.delenv(var, raising=False)
    if model:
        monkeypatch.setenv("GEMINI_MODEL", model)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key")
    row = next(r for r in check_keys.check() if r[0] == "Gemini")
    return row, calls


def test_check_keys_sends_a_tiny_real_request_with_the_scoring_settings(monkeypatch):
    ok = httpx.Response(200, json={"candidates": [{"finishReason": "STOP",
                                                   "content": {"parts": [{"text": '{"ok": true}'}]}}]})
    (_, msg, good), calls = _check(monkeypatch, ok)
    assert good and msg.startswith("ok")
    url, body = calls[0]
    assert url.endswith(f"/models/{ap.BUILTIN_MODEL}:generateContent")
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert body["generationConfig"]["maxOutputTokens"] <= 32, "the check must stay a few tokens"


def test_check_keys_fails_a_retired_model(monkeypatch):
    (_, msg, good), _ = _check(monkeypatch, httpx.Response(404, json=RETIRED_404), model="gemini-2.5-flash")
    assert not good and "gemini-2.5-flash" in msg and "GEMINI_MODEL" in msg


def test_check_keys_fails_a_rejected_setting(monkeypatch):
    (_, msg, good), _ = _check(monkeypatch, httpx.Response(400, json=MINIMAL_400))
    assert not good and "Thinking level MINIMAL" in msg
    assert "key is wrong" not in msg, "a settings problem must not send the student to replace a good key"


def test_check_keys_says_when_a_model_has_no_free_tier(monkeypatch):
    (_, msg, good), _ = _check(monkeypatch, httpx.Response(429, json=NO_FREE_TIER_429), model="gemini-pro-latest")
    assert not good and "free tier" in msg


def test_check_keys_passes_a_key_that_is_only_busy(monkeypatch):
    """A per-minute 429 means the key works; do not send the student to paste a new one."""
    busy = {"error": {"code": 429, "message": "quota", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "30s"}]}}
    (_, msg, good), _ = _check(monkeypatch, httpx.Response(429, json=busy))
    assert good and "busy" in msg


def test_check_keys_still_reports_a_bad_key(monkeypatch):
    bad = httpx.Response(400, json={"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                                              "details": [{"reason": "API_KEY_INVALID"}]}})
    (_, msg, good), _ = _check(monkeypatch, bad)
    assert not good and "key is wrong or expired" in msg
