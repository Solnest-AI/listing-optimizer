"""Regressions for the 2026-09-28 Codex bug/efficiency review (round 5)."""
import asyncio
import sys
from pathlib import Path

import httpx
import pytest
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import airroi_client  # noqa: E402
import analyze_photos  # noqa: E402
import hospitable_api  # noqa: E402
import render_report as rr  # noqa: E402


@pytest.mark.parametrize("text", ["Lower prices this winter", "Get a 20% discount", "Only 150 EUR a night",
                                  "20% off your stay", "Weekly discounts available", "From 120 CAD"])
def test_paste_guard_blocks_more_pricing(text):
    assert rr.PRICING_RE.search(text), text


@pytest.mark.parametrize("text", ["Only 150 EUR", "20% off", "a 15% discount for a week"])
def test_report_guard_blocks_price_data(text):
    assert rr.PRICE_NUMBER_RE.search(text), text


@pytest.mark.parametrize("text", ["Booking rate 3%", "100% of top comps have a hot tub", "Walk to UHNBC",
                                  "Pricing is a lever to review with your revenue tool"])
def test_report_guard_still_allows_diagnostics(text):
    assert not rr.PRICE_NUMBER_RE.search(text), text


def test_duplicate_gap_counts_repeats_not_pairs():
    gap = analyze_photos.duplicate_gap([(1, 2), (1, 3), (2, 3)])
    assert gap.startswith("2 duplicate photo(s)") and "#2 repeats #1" in gap and "#3 repeats #1" in gap


def test_markdown_funnel_aligns_booking_rate_by_month():
    env = Environment(loader=FileSystemLoader(str(ROOT / ".claude/skills/listing-optimizer/output-templates")))
    src = env.loader.get_source(env, "report.md.j2")[0]
    line = next(ln for ln in src.splitlines() if ln.startswith("| Booking rate"))
    out = env.from_string(line).render(data={"funnel": {"views_monthly": {"Aug": 10, "Sep": 20},
                                                        "booking_rate_monthly": {"Sep": "2%", "Aug": "1%"}}})
    assert out.strip() == "| Booking rate | 1% | 2% |"


def test_capped_all_reviews_is_not_labelled_complete(monkeypatch):
    def fake_get(path, params=None):
        return {"data": [{"id": params["page"]}], "meta": {"last_page": 99, "total": 99}}
    monkeypatch.setattr(hospitable_api, "_get", fake_get)
    out = hospitable_api._fetch_reviews("pid", 20, all_reviews=True)
    assert out["_pull"]["complete_history"] is False and out["_pull"]["total_available"] == 99


def test_airroi_retries_a_429_then_succeeds(monkeypatch):
    monkeypatch.setenv("AIRROI_API_KEY", "k")
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "0"}, json={}) if len(calls) == 1 else \
            httpx.Response(200, json={"ok": True})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await airroi_client._get("/x", {}, c)
    assert asyncio.run(go()) == {"ok": True} and len(calls) == 2


def test_airroi_read_timeout_is_not_retried_and_is_an_airroi_error(monkeypatch):
    monkeypatch.setenv("AIRROI_API_KEY", "k")
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await airroi_client._get("/x", {}, c)
    with pytest.raises(airroi_client.AirROIError):
        asyncio.run(go())
    assert len(calls) == 1, "a read timeout may have been billed: never retried"


def test_signature_cache_entry_is_compact_and_round_trips():
    import base64
    import json
    import random

    from PIL import Image

    import photo_dupes as d
    im = Image.new("RGB", (200, 150))
    im.putdata([(random.randint(0, 255),) * 3 for _ in range(200 * 150)])
    bits, pixels = d.raw_signature(im)
    entry = [str(bits), base64.b64encode(pixels).decode("ascii")]
    assert len(json.dumps(entry)) < 7000, "cache entries must stay small"
    again = d.from_raw(int(entry[0]), base64.b64decode(entry[1]))
    assert again == d.signature(im) and d.is_duplicate(again, d.signature(im))
