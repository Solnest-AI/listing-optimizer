#!/usr/bin/env python3
"""The live listing guests see: read from the public Airbnb page every run, nothing else.

Measured 2026-09-25: the PMS held 54 photos with a collage cover while the live Airbnb
listing had 32, a different cover and different captions. Measured 2026-09-28: provider
copies (RankBreeze, AirROI) served last summer's listing and mixed amenity boxes between two
houses, so they are never used for the listing being optimized.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import airbnb_live as al
import run_pipeline as rp

MUS = "https://a0.muscache.com/im/pictures/hosting/Hosting-1/original/"

GOOD = {"provider": "airbnb", "room_id": "111", "fetched_at": "2026-09-28T20:00:00Z",
        "complete": True, "returned": 2, "reported": 2,
        "photos": [{"position": 1, "url": MUS + "a.png", "caption": "Dining"},
                   {"position": 2, "url": MUS + "b.png", "caption": ""}],
        "listing": {"title": "T", "summary": "S", "description": "", "guest_access": "",
                    "other_notes": "", "amenities": [], "not_included": []}}


def test_images_contract_uses_airbnb_positions():
    g = {**GOOD, "photos": [{"position": 7, "url": MUS + "a.png", "caption": "Yard"}]}
    im = al.to_images(g)
    assert im["data"][0]["order"] == 7 and im["data"][0]["caption"] == "Yard"
    assert im["data"][0]["url"].endswith("?im_w=1200") and im["data"][0]["thumbnail_url"].endswith("?im_w=480")
    assert im["_source"]["kind"] == "live_airbnb" and im["_source"]["provider"] == "airbnb"


@pytest.mark.parametrize("bad", [
    {"photos": []},
    {"photos": [{"position": 1, "url": "http://evil.example/x.jpg"}]},
    {"photos": [{"position": 1, "url": MUS + "a.png"}, {"position": 1, "url": MUS + "b.png"}]},
    {"listing": None},
])
def test_a_malformed_live_read_is_rejected(bad):
    with pytest.raises(ValueError):
        al.validate({**GOOD, **bad})


@pytest.mark.parametrize("provider", ["rankbreeze", "intellihost", "airroi", None])
def test_a_provider_snapshot_is_never_accepted_as_the_live_listing(provider):
    with pytest.raises(ValueError, match="live Airbnb page"):
        al.validate({**GOOD, "provider": provider})


def _runner_writing(gallery):
    def runner(cmd, label):
        Path(cmd[cmd.index("--out") + 1]).write_text(json.dumps(gallery), encoding="utf-8")
        return True, "[airbnb_live] live page read"
    return runner


def test_a_live_page_read_becomes_the_images(tmp_path):
    status, _ = rp.live_gallery_step(tmp_path, "111", runner=_runner_writing(GOOD))
    im = json.loads((tmp_path / "images.json").read_text(encoding="utf-8"))
    assert status == "ok" and im["_source"]["kind"] == "live_airbnb" and [d["order"] for d in im["data"]] == [1, 2]


def test_an_earlier_read_is_never_reused(tmp_path):
    """A live read is only valid on the run that made it."""
    (tmp_path / "live_gallery.json").write_text(json.dumps(GOOD))
    status, detail = rp.live_gallery_step(tmp_path, "111", runner=lambda cmd, label: (False, "page: HTTP 403"))
    assert status == "skipped" and "NOT verified" in detail
    assert not (tmp_path / "live_gallery.json").exists()


def test_no_airbnb_id_falls_back_to_the_pms_and_says_so(tmp_path):
    status, detail = rp.live_gallery_step(tmp_path, None)
    assert status == "skipped" and "PMS" in detail and not (tmp_path / "images.json").exists()


def test_failed_read_falls_back_without_touching_images(tmp_path):
    (tmp_path / "images.json").write_text('{"data": []}')
    status, _ = rp.live_gallery_step(tmp_path, "111", runner=lambda cmd, label: (False, "live page: exit 1"))
    assert status == "skipped"
    assert json.loads((tmp_path / "images.json").read_text(encoding="utf-8")) == {"data": []}


def test_a_snapshot_written_in_place_of_a_live_read_is_not_used(tmp_path):
    status, _ = rp.live_gallery_step(tmp_path, "111", runner=_runner_writing({**GOOD, "provider": "rankbreeze"}))
    assert status == "skipped" and not (tmp_path / "images.json").exists()
    assert not (tmp_path / "live_gallery.json").exists()


def test_digest_names_the_gallery_source(tmp_path):
    import build_digest as bd
    (tmp_path / "subject.json").write_text(json.dumps({"data": {"name": "x"}}))
    base = {"hero": 1, "recommended_top5_order": [1], "photos": [], "gaps": []}
    (tmp_path / "photo_scores.json").write_text(json.dumps({**base, "gallery_source": {
        "kind": "live_airbnb", "provider": "airbnb", "fetched_at": "2026-09-28T20:00:00Z", "complete": True}}))
    assert "LIVE Airbnb gallery, read from the public page 2026-09-28T20:00:00Z" in bd.build(tmp_path)
    (tmp_path / "photo_scores.json").write_text(json.dumps({**base, "gallery_source": {
        "kind": "pms", "provider": "Hospitable"}}))
    out = bd.build(tmp_path)
    assert "PMS copy (Hospitable)" in out and "not verified against the live Airbnb" in out


def test_paste_block_labels_airbnb_positions_only_for_a_live_gallery():
    import render_report as rr
    data = {"listing": {"name": "x"}, "run_date": "d",
            "optimized": {"title": "t", "summary": "s", "the_space": "sp",
                          "captions": [{"order": 32, "subject": "living room", "caption": "c"}]},
            "photos": {"gallery_source": {"kind": "live_airbnb", "provider": "airbnb"}}}
    assert "[Airbnb photo 32: living room]" in rr.build_paste_block(data)
    data["photos"]["gallery_source"] = {"kind": "pms", "provider": "Hospitable"}
    assert "[#32 living room]" in rr.build_paste_block(data)


def test_scorer_reports_an_incomplete_gallery(tmp_path):
    import analyze_photos as ap
    src = {"kind": "live_airbnb", "provider": "airbnb", "complete": False, "returned": 29, "reported": 42}
    (tmp_path / "images.json").write_text(json.dumps({"data": [], "_source": src}))
    assert ap.load_source(tmp_path / "images.json") == src
    note = ap.source_note(src)
    assert "29 of 42" in note and "13" in note


def _env():
    from jinja2 import Environment, FileSystemLoader
    root = Path(__file__).resolve().parent.parent
    return root / ".claude/skills/listing-optimizer/output-templates", Environment, FileSystemLoader


@pytest.mark.parametrize("tpl", ["report.md.j2", "report.html.j2"])
def test_reports_say_which_gallery_the_photo_numbers_refer_to(tpl):
    folder, Environment, FileSystemLoader = _env()
    env = Environment(loader=FileSystemLoader(str(folder)), trim_blocks=True, lstrip_blocks=True,
                      autoescape=tpl.endswith("html.j2"))
    base = {"listing": {"name": "x"}, "optimized": {"title": "t", "summary": "s", "the_space": "sp"}, "branding": {}}
    live = env.get_template(tpl).render(data={**base, "photos": {"hero": 32, "gallery_source": {
        "kind": "live_airbnb", "provider": "airbnb", "fetched_at": "2026-09-28T20:00:00Z"}}})
    pms = env.get_template(tpl).render(data={**base, "photos": {"hero": 3, "gallery_source": {
        "kind": "pms", "provider": "Hospitable"}}})
    assert "live Airbnb page, read 2026-09-28T20:00:00Z" in live and "Airbnb photo positions" in live
    assert "not checked against the live Airbnb listing" in pms


def _digest(tmp_path, subject, listing):
    import build_digest as bd
    (tmp_path / "subject.json").write_text(json.dumps({"data": subject}))
    (tmp_path / "live_gallery.json").write_text(json.dumps({**GOOD, "listing": {**GOOD["listing"], **listing}}))
    return bd.build(tmp_path)


def test_digest_uses_live_copy_and_live_amenities_and_flags_pms_drift(tmp_path):
    (tmp_path / "comps.json").write_text(json.dumps({"comp_count": 2, "top_comps": [],
        "market_amenity_frequency": [{"amenity": "Self check-in", "pct": 83}, {"amenity": "Wifi", "pct": 100}],
        "comp_title_samples": []}))
    out = _digest(tmp_path, {"name": "Boho", "public_name": "Walk to UHNBC", "summary": "Same summary.",
                             "description": "Keurig + French press", "amenities": ["wifi"], "house_rules": {}},
                  {"title": "Walk to UHNBC", "summary": "Same summary.",
                   "description": "Keurig only. Explore Cottonwood Island Park and the farmers market",
                   "amenities": ["Self check-in", "Wifi"]})
    assert "Cottonwood Island Park" in out and "Keurig + French press" not in out, "digest critiqued the PMS copy"
    assert "PMS copy differs from live Airbnb: description" in out
    miss = next(line for line in out.splitlines() if line.startswith("missing_on_live_airbnb"))
    assert "Self check-in" not in miss, "told the host to tick a box that is already ticked on Airbnb"


def test_punctuation_only_title_difference_is_not_drift(tmp_path):
    out = _digest(tmp_path, {"name": "Boho", "public_name": "Walk to UHNBC | Boho Suite · Firepit · Pets OK",
                             "summary": "S"},
                  {"title": "Walk to UHNBC | Boho Suite • Firepit • Pets OK", "summary": "S"})
    assert "PMS copy matches" in out, [line for line in out.splitlines() if line.startswith("copy_source")]


def test_drift_means_airbnb_says_something_the_pms_does_not(tmp_path):
    """Olde Town 2026-09-26: Airbnb's The Space sat entirely inside the PMS description (the PMS
    just carries extra sections), yet an exact-match check flagged drift. Boho's Airbnb text
    had content the PMS lacks (Cottonwood Island Park, farmers' market): real drift."""
    pms = {"name": "x", "public_name": "T", "summary": "S",
           "description": "WHY GUESTS BOOK IT. Walk to Olde Town. LOCAL ATTRACTIONS: Red Rocks 15 miles."}

    def copy_line(desc):
        out = _digest(tmp_path, pms, {"title": "T", "summary": "S", "description": desc})
        return next(x for x in out.splitlines() if x.startswith("copy_source"))

    assert "PMS copy matches" in copy_line("WHY GUESTS BOOK IT. Walk to Olde Town.")
    assert "differs from live Airbnb: description" in copy_line(
        "WHY GUESTS BOOK IT. Walk to Olde Town, Cottonwood Island Park and the farmers market.")


def test_digest_shows_guest_access_other_notes_and_what_is_not_included(tmp_path):
    out = _digest(tmp_path, {"name": "x", "public_name": "T", "summary": "S"},
                  {"guest_access": "A private suite on the lower level is occupied by a long-term tenant.",
                   "other_notes": "Stairs to the upper bedrooms.", "not_included": ["Air conditioning"]})
    assert "guest_access (live Airbnb): A private suite on the lower level" in out
    assert "other_notes (live Airbnb): Stairs to the upper bedrooms." in out
    assert "not_included (live Airbnb, shown to guests as not offered): Air conditioning" in out


def test_a_live_listing_with_no_guest_access_is_called_out(tmp_path):
    out = _digest(tmp_path, {"name": "x", "public_name": "T", "summary": "S"}, {"guest_access": ""})
    assert "guest_access (live Airbnb): EMPTY. The live listing has no Guest access section" in out


def test_without_a_live_read_the_digest_claims_nothing_about_airbnb(tmp_path):
    import build_digest as bd
    (tmp_path / "subject.json").write_text(json.dumps({"data": {"name": "x", "public_name": "T", "summary": "S"}}))
    (tmp_path / "comps.json").write_text(json.dumps({"comp_count": 0, "top_comps": [], "market_amenity_frequency": [],
        "comp_title_samples": [], "subject_listing": {"title": "Old summer title", "amenities": ["Shared hot tub"]}}))
    out = bd.build(tmp_path)
    line = next(x for x in out.splitlines() if x.startswith("copy_source"))
    assert "PMS copy ONLY" in line and "could not be read" in line
    assert "Old summer title" not in out and "Shared hot tub" not in out, "a provider snapshot leaked into the digest"


def test_paste_instructions_follow_where_the_copy_actually_lives():
    """boho-bliss 2026-09-26: the paste block said 'paste into your PMS; it syncs to your
    channels' on a listing whose PMS description had provably not reached Airbnb."""
    import render_report as rr
    base = {"listing": {"name": "x"}, "run_date": "d",
            "optimized": {"title": "t", "summary": "s", "the_space": "sp", "captions": []}}
    live = rr.build_paste_block({**base, "photos": {"gallery_source": {"kind": "live_airbnb"}}})
    assert "it syncs to your channels" not in live and "directly on Airbnb" in live
    pms = rr.build_paste_block({**base, "photos": {"gallery_source": {"kind": "pms"}}})
    assert "paste into your PMS" in pms


def test_occupancy_row_is_labelled_as_on_the_books():
    folder, Environment, FileSystemLoader = _env()
    env = Environment(loader=FileSystemLoader(str(folder)), trim_blocks=True, lstrip_blocks=True)
    md = env.get_template("report.md.j2").render(data={
        "listing": {"name": "x"}, "optimized": {"title": "t", "summary": "s", "the_space": "sp"}, "branding": {},
        "occupancy": {"source": "Hospitable", "forward_pct": 53.3, "forward_days": 90,
                      "upcoming_reservations": 10, "monthly": {"2026-09": "100%"}}})
    assert "| Booked from today |" in md and "| Occupancy |" not in md


@pytest.mark.parametrize("tpl", ["report.md.j2", "report.html.j2"])
def test_hero_is_named_as_an_airbnb_photo_on_a_live_gallery(tpl):
    folder, Environment, FileSystemLoader = _env()
    env = Environment(loader=FileSystemLoader(str(folder)), trim_blocks=True, lstrip_blocks=True,
                      autoescape=tpl.endswith("html.j2"))
    base = {"listing": {"name": "x"}, "optimized": {"title": "t", "summary": "s", "the_space": "sp"}, "branding": {}}
    live = env.get_template(tpl).render(data={**base, "photos": {"hero": 45, "recommended_top5_order": [45, 1],
                                                                  "gallery_source": {"kind": "live_airbnb", "provider": "airbnb"}}})
    pms = env.get_template(tpl).render(data={**base, "photos": {"hero": 3, "recommended_top5_order": [3],
                                                                 "gallery_source": {"kind": "pms", "provider": "Hospitable"}}})
    assert "Airbnb photo 45" in live and "photo #3" in pms
