"""Exercise actual Gemini request construction against an in-process HTTP transport."""
import asyncio
import json
import re
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import analyze_photos as ap


def row(order=0, **overrides):
    return {"order": order, "subject_kind": "living_room", "subject": "Living room",
            "season": "interior", "has_people": False, "is_map": False,
            "technical": 4, "lighting": 4, "staging": 4, "composition": 4,
            "emotion": 4, "ale_fit": 4, "flags": [], "caption_note": "Relax together",
            **overrides}


def photos(n):
    return [{"order": i, "url": f"https://images.test/{i}.jpg", "caption": ""} for i in range(n)]


def transport(monkeypatch, status=200, transform=lambda x: x):
    requests = []
    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, content=b"image", headers={"content-type": "image/jpeg"})
        requests.append(req)
        body = json.loads(req.content)
        parts = body["contents"][0]["parts"]
        orders = [int(m) for p in parts for m in re.findall(r"PHOTO_ORDER=(\d+)", p.get("text", ""))]
        payload = [transform(row(i)) for i in orders] if orders else transform(row())
        return httpx.Response(status, json={"candidates": [{"finishReason": "STOP",
            "content": {"parts": [{"text": json.dumps(payload)}]}}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 200,
                              "totalTokenCount": 300}})
    client = httpx.AsyncClient
    monkeypatch.setattr(ap.httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handler)))
    return requests


def test_thirty_uncached_photos_need_six_gemini_requests(monkeypatch):
    requests = transport(monkeypatch)
    scored = asyncio.run(ap.score_photos(photos(30), "gemini-2.5-flash", "fake-test-key"))
    assert len(scored) == 30 and all(p["scored"] for p in scored)
    assert len(requests) == 6
    assert all("key=" not in str(r.url) for r in requests)
    assert all(r.headers.get("x-goog-api-key") == "fake-test-key" for r in requests)


def test_duplicate_photo_urls_are_scored_once(monkeypatch):
    requests = transport(monkeypatch)
    gallery = photos(2)
    gallery[1]["url"] = gallery[0]["url"]
    scored = asyncio.run(ap.score_photos(gallery, "gemini-2.5-flash", "fake-test-key"))
    assert [p["order"] for p in scored] == [0, 1]
    assert all(p["scored"] for p in scored)
    assert len(requests) == 1
    parts = json.loads(requests[0].content)["contents"][0]["parts"]
    assert sum("inline_data" in p for p in parts) == 1


@pytest.mark.parametrize("override", [{"technical": 999}, {"emotion": -1},
                                     {"subject_kind": "invented"}, {"lighting": True}])
def test_invalid_model_scores_are_never_ranked(monkeypatch, override):
    transport(monkeypatch, transform=lambda r: {**r, **override})
    scored = asyncio.run(ap.score_photos(photos(1), "gemini-2.5-flash", "fake-test-key"))
    assert not scored[0]["scored"]
    assert ap.aggregate(scored)["hero"] is None


def test_auth_failure_stops_later_batches(monkeypatch):
    requests = transport(monkeypatch, status=401)
    scored = asyncio.run(ap.score_photos(photos(30), "gemini-2.5-flash", "fake-test-key", concurrency=1))
    assert len(requests) == 1
    assert not any(p["scored"] for p in scored)
    assert "fake-test-key" not in json.dumps(scored)


def test_query_selected_images_never_share_cached_scores():
    assert ap._photo_cache_key("https://img.test/photo?id=1", "m") != ap._photo_cache_key("https://img.test/photo?id=2", "m")


def test_map_only_gallery_has_no_hero():
    scored = [{**row(0, subject_kind="location_map", is_map=True), "scored": True, "avg": 5}]
    assert ap.aggregate(scored)["hero"] is None
    assert ap.aggregate(scored)["recommended_top5_order"] == []


def test_protocol_failure_is_bounded_and_does_not_lose_other_photos(monkeypatch):
    requests = []
    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, content=b"image", headers={"content-type": "image/jpeg"})
        requests.append(req)
        raise httpx.RemoteProtocolError("Server disconnected without sending a response")
    real_client = httpx.AsyncClient
    monkeypatch.setattr(ap.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    async def no_delay(*a):
        pass
    monkeypatch.setattr(ap.asyncio, "sleep", no_delay)
    scores = asyncio.run(ap.score_photos(photos(5), "gemini-2.5-flash", "fake-test-key"))
    assert len(requests) == 3
    assert len(scores) == 5 and not any(p["scored"] for p in scores)


def test_people_swap_preserves_distinct_beats():
    kinds = ["living_room", "hot_tub", "bedroom", "bathroom", "exterior", "hot_tub"]
    scored = [{**row(i, subject_kind=k, has_people=(i == 5)), "scored": True, "avg": 5-i/2}
              for i, k in enumerate(kinds)]
    result = ap.aggregate(scored)
    assert len(result["top5_beats"]) == len(set(result["top5_beats"]))
    assert 5 in result["recommended_top5_order"]
    assert result["recommended_top5_order"][0] == result["hero"]


def test_thin_gallery_does_not_fill_slots_with_duplicate_beats():
    scored = [{**row(i), "scored": True, "avg": 4} for i in range(3)]
    assert ap.aggregate(scored)["recommended_top5_order"] == [0]


def test_duplicate_photo_orders_fail_before_spending(tmp_path):
    gallery = photos(2)
    gallery[1]["order"] = 0
    p = tmp_path / "images.json"
    p.write_text(json.dumps(gallery))
    with pytest.raises(ValueError, match="order"):
        ap.load_photos(p)


def test_total_scoring_failure_preserves_actual_usage(monkeypatch, tmp_path):
    transport(monkeypatch, transform=lambda r: {**r, "technical": 999})
    monkeypatch.setattr(ap, "load_gemini_key", lambda: "fake-test-key")
    (tmp_path / "images.json").write_text(json.dumps(photos(1)))
    monkeypatch.setattr(sys, "argv", ["analyze_photos.py", "--photos", str(tmp_path / "images.json"),
        "--out", str(tmp_path / "scores.json"), "--no-cache"])
    with pytest.raises(SystemExit):
        ap.main()
    result = json.loads((tmp_path / "scores.json").read_text())
    assert result["usage"]["api_calls"] == 1
    assert result["usage"]["totalTokenCount"] == 300


def test_photo_limit_discloses_unassessed_gallery(monkeypatch, tmp_path):
    transport(monkeypatch)
    monkeypatch.setattr(ap, "load_gemini_key", lambda: "fake-test-key")
    (tmp_path / "images.json").write_text(json.dumps(photos(10)))
    monkeypatch.setattr(sys, "argv", ["analyze_photos.py", "--photos", str(tmp_path / "images.json"),
        "--out", str(tmp_path / "scores.json"), "--limit", "5", "--no-cache"])
    ap.main()
    result = json.loads((tmp_path / "scores.json").read_text())
    assert result["gallery_count"] == 10 and result["not_submitted_count"] == 5
    assert "5 not assessed" in result["coverage_note"]
