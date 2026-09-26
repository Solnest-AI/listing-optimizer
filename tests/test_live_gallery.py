#!/usr/bin/env python3
"""Tests for live_gallery.py — the photo set guests actually see on Airbnb.

Measured 2026-09-25 on a real listing: the PMS (Hospitable) held 54 photos with a collage
cover and 16 duplicate pairs; the live Airbnb listing had 32 photos, a different cover and
different captions. A photo plan built on the PMS copy was wrong for Airbnb. Payload shapes
below are the real RankBreeze / IntelliHost MCP shapes (values synthetic).
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import live_gallery as lg

MUS = "https://a0.muscache.com/im/pictures/hosting/Hosting-1/original/"


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def call(self, tool, args):
        self.calls.append((tool, args))
        r = self.responses[tool]
        if isinstance(r, Exception):
            raise r
        return r(args) if callable(r) else r


def _rb(images, room="111"):
    return FakeSession({
        "get_user_listings": {"listings": [{"id": 9, "room_id": room}], "nextCursor": None},
        "get_listing_content": {"room_id": room, "fetched_at": "2026-09-26T07:00:00Z",
                                "base_price": 250, "min_nights": 2, "images": images}})


def test_rankbreeze_gallery_keeps_order_and_captions_and_drops_pricing():
    imgs = [{"position": 1, "url": MUS + "a.png", "caption": "Dining room"},
            {"position": 2, "url": MUS + "b.png", "caption": None}]
    g = lg.from_rankbreeze(_rb(imgs), "111")
    assert [p["position"] for p in g["photos"]] == [1, 2]
    assert g["photos"][0]["caption"] == "Dining room" and g["photos"][1]["caption"] == ""
    assert g["provider"] == "rankbreeze" and g["complete"] is True
    assert "250" not in json.dumps(g), "a price field leaked into the gallery"


def test_rankbreeze_follows_the_listing_cursor():
    pages = {None: {"listings": [{"id": 1, "room_id": "x"}], "nextCursor": "c2"},
             "c2": {"listings": [{"id": 9, "room_id": "111"}], "nextCursor": None}}
    s = FakeSession({"get_user_listings": lambda a: pages[a.get("cursor")],
                     "get_listing_content": {"images": [{"position": 1, "url": MUS + "a.png"}]}})
    assert lg.from_rankbreeze(s, "111")["photos"]
    assert ("get_listing_content", {"listing_id": "9", "include_images": True}) in s.calls


def test_listing_not_in_account_is_a_named_miss():
    s = FakeSession({"get_user_listings": {"listings": [], "nextCursor": None}})
    with pytest.raises(lg.GalleryUnavailable, match="not in this RankBreeze account"):
        lg.from_rankbreeze(s, "111")


def _ih(photos, count, room="111", locked=False):
    details = (lg.MCPError("get-listing-details-tool: An IntelliHost Premium subscription is "
                           "required to read that property through the API.")
               if locked else {"listing_id": room, "photo_count": count, "price": 300,
                               "cleaning_fee": 90, "minimum_nights": 3, "photos": photos})
    return FakeSession({"list-properties-tool": {"count": 1, "properties": [{"id": 5, "listing_id": room}]},
                        "get-listing-details-tool": details})


def test_intellihost_short_list_is_marked_incomplete():
    """IntelliHost returned 29 of 42 on a real listing whatever photo_limit was set."""
    photos = [{"url": f"{MUS}{i}.jpg"} for i in range(29)]
    g = lg.from_intellihost(_ih(photos, 42), "111")
    assert g["complete"] is False and g["returned"] == 29 and g["reported"] == 42
    assert [p["position"] for p in g["photos"]] == list(range(1, 30))
    for word in ("300", "cleaning", "minimum"):
        assert word not in json.dumps(g), f"{word} leaked from the IntelliHost payload"


def test_intellihost_premium_lock_is_explained():
    with pytest.raises(lg.GalleryUnavailable, match="Premium"):
        lg.from_intellihost(_ih([], 0, locked=True), "111")


def test_images_contract_uses_airbnb_positions():
    g = {"provider": "rankbreeze", "fetched_at": "t", "room_id": "111", "complete": True,
         "returned": 1, "reported": 1,
         "photos": [{"position": 7, "url": MUS + "a.png", "caption": "Yard"}]}
    im = lg.to_images(g)
    assert im["data"][0]["order"] == 7 and im["data"][0]["caption"] == "Yard"
    assert im["data"][0]["url"].endswith("?im_w=1200") and im["data"][0]["thumbnail_url"].endswith("?im_w=480")
    assert im["_source"]["kind"] == "live_airbnb" and im["_source"]["provider"] == "rankbreeze"


@pytest.mark.parametrize("bad", [
    {"photos": []},
    {"photos": [{"position": 1, "url": "http://evil.example/x.jpg"}]},
    {"photos": [{"position": 1, "url": MUS + "a.png"}, {"position": 1, "url": MUS + "b.png"}]},
])
def test_agent_staged_gallery_is_validated(bad):
    """A connector user's agent writes live_gallery.json by hand; a bad file must not
    silently become the gallery."""
    with pytest.raises(ValueError):
        lg.validate({"provider": "rankbreeze", **bad})


def test_source_order_prefers_the_complete_provider(monkeypatch):
    monkeypatch.setenv("RANKBREEZE_MCP_URL", "https://app.rankbreeze.com/api/mcp/x")
    monkeypatch.setenv("INTELLIHOST_MCP_TOKEN", "t")
    assert [name for name, _ in lg.configured_sources()] == ["rankbreeze", "intellihost"]
    monkeypatch.delenv("RANKBREEZE_MCP_URL")
    assert [name for name, _ in lg.configured_sources()] == ["intellihost"]


# ── Pipeline wiring: which gallery the photo plan is built on ─────────────────
import run_pipeline as rp  # noqa: E402

GOOD = {"provider": "rankbreeze", "room_id": "111", "fetched_at": "2026-09-26T07:00:00Z",
        "complete": True, "returned": 2, "reported": 2,
        "photos": [{"position": 1, "url": MUS + "a.png", "caption": "Dining"},
                   {"position": 2, "url": MUS + "b.png", "caption": ""}]}


def test_agent_staged_live_gallery_becomes_the_images(tmp_path, monkeypatch):
    """Connector users: the agent saves live_gallery.json from the RankBreeze/IntelliHost
    tools it has in Claude; the pipeline builds images.json from it."""
    monkeypatch.setattr(lg, "configured_sources", lambda: [])
    (tmp_path / "live_gallery.json").write_text(json.dumps(GOOD))
    status, detail = rp.live_gallery_step(tmp_path, "111")
    assert status == "ok" and "agent-staged" in detail
    im = json.loads((tmp_path / "images.json").read_text())
    assert im["_source"]["kind"] == "live_airbnb" and [d["order"] for d in im["data"]] == [1, 2]


def test_no_connection_falls_back_to_the_pms_gallery_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(lg, "configured_sources", lambda: [])
    status, detail = rp.live_gallery_step(tmp_path, "111")
    assert status == "skipped" and "PMS gallery" in detail
    assert not (tmp_path / "images.json").exists()


def test_failed_fetch_falls_back_without_touching_images(tmp_path, monkeypatch):
    monkeypatch.setattr(lg, "configured_sources", lambda: [("rankbreeze", {"url": "u"})])
    (tmp_path / "images.json").write_text('{"data": []}')
    status, detail = rp.live_gallery_step(tmp_path, "111",
                                          runner=lambda cmd, label: (False, "live gallery: exit 1 - nope"))
    assert status == "skipped" and "PMS gallery" in detail
    assert json.loads((tmp_path / "images.json").read_text()) == {"data": []}


def test_invalid_staged_gallery_is_not_used(tmp_path, monkeypatch):
    monkeypatch.setattr(lg, "configured_sources", lambda: [])
    (tmp_path / "live_gallery.json").write_text(json.dumps({**GOOD, "photos": []}))
    status, _ = rp.live_gallery_step(tmp_path, "111")
    assert status == "skipped" and not (tmp_path / "images.json").exists()


def test_digest_names_the_gallery_source(tmp_path):
    import build_digest as bd
    (tmp_path / "subject.json").write_text(json.dumps({"data": {"name": "x"}}))
    base = {"hero": 1, "recommended_top5_order": [1], "photos": [], "gaps": []}
    (tmp_path / "photo_scores.json").write_text(json.dumps({**base, "gallery_source": {
        "kind": "live_airbnb", "provider": "intellihost", "fetched_at": "t", "complete": False,
        "returned": 29, "reported": 42}}))
    out = bd.build(tmp_path)
    assert "LIVE Airbnb gallery via intellihost" in out and "29 of 42" in out
    (tmp_path / "photo_scores.json").write_text(json.dumps({**base, "gallery_source": {
        "kind": "pms", "provider": "Hospitable"}}))
    out = bd.build(tmp_path)
    assert "PMS copy (Hospitable)" in out and "not verified against the live Airbnb" in out


def test_paste_block_labels_airbnb_positions_only_for_a_live_gallery():
    import render_report as rr
    data = {"listing": {"name": "x"}, "run_date": "d",
            "optimized": {"title": "t", "summary": "s", "the_space": "sp",
                          "captions": [{"order": 32, "subject": "living room", "caption": "c"}]},
            "photos": {"gallery_source": {"kind": "live_airbnb", "provider": "rankbreeze"}}}
    assert "[Airbnb photo 32: living room]" in rr.build_paste_block(data)
    data["photos"]["gallery_source"] = {"kind": "pms", "provider": "Hospitable"}
    assert "[#32 living room]" in rr.build_paste_block(data)


def test_scorer_reports_an_incomplete_live_gallery(tmp_path):
    import analyze_photos as ap
    src = {"kind": "live_airbnb", "provider": "intellihost", "complete": False, "returned": 29, "reported": 42}
    (tmp_path / "images.json").write_text(json.dumps({"data": [], "_source": src}))
    assert ap.load_source(tmp_path / "images.json") == src
    note = ap.source_note(src)
    assert "29 of 42" in note and "13" in note


@pytest.mark.parametrize("tpl", ["report.md.j2", "report.html.j2"])
def test_reports_say_which_gallery_the_photo_numbers_refer_to(tpl):
    from jinja2 import Environment, FileSystemLoader
    root = Path(__file__).resolve().parent.parent
    env = Environment(loader=FileSystemLoader(str(root / ".claude/skills/listing-optimizer/output-templates")),
                      trim_blocks=True, lstrip_blocks=True, autoescape=tpl.endswith("html.j2"))
    base = {"listing": {"name": "x"}, "optimized": {"title": "t", "summary": "s", "the_space": "sp"}, "branding": {}}
    live = env.get_template(tpl).render(data={**base, "photos": {"hero": 32, "gallery_source": {
        "kind": "live_airbnb", "provider": "rankbreeze"}}})
    pms = env.get_template(tpl).render(data={**base, "photos": {"hero": 3, "gallery_source": {
        "kind": "pms", "provider": "Hospitable"}}})
    assert "live Airbnb listing via rankbreeze" in live and "Airbnb photo positions" in live
    assert "not checked against the live Airbnb listing" in pms
