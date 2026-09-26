#!/usr/bin/env python3
"""Paste-ready copy rules the skill states but the renderer never enforced.

Phone numbers, emails and URLs break Airbnb sync / policy (references/airbnb-field-limits.md).
Airbnb's title guidelines ban emojis, ALL CAPS and repeated special characters. A caption
for a photo order that does not exist is a caption the host cannot place.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import render_report as rr


def _result(**opt):
    base = {"title": "Walk to Olde Town + Light Rail", "summary": "Sleeps 8.",
            "the_space": "Fenced yard. STR License #123456.", "captions": []}
    base.update(opt)
    return {"listing": {"name": "x", "slug": "x"}, "run_date": "2026-09-25", "optimized": base}


@pytest.mark.parametrize("field,text", [
    ("summary", "Text us at 303-555-0142 for early check-in."),
    ("summary", "Call (303) 555 0142 anytime."),
    ("the_space", "Email hello@example.com with questions."),
    ("the_space", "See our guidebook at https://example.com/guide."),
    ("the_space", "Full guide on www.example.com."),
    ("summary", "Book direct at oldetownambler.com."),
])
def test_contact_details_are_rejected(field, text):
    with pytest.raises(ValueError, match="phone|email|URL"):
        rr.validate_result(_result(**{field: text}))


def test_contact_details_in_captions_are_rejected():
    with pytest.raises(ValueError, match="phone|email|URL"):
        rr.validate_result(_result(captions=[{"order": 1, "caption": "Call 303.555.0142"}]))


@pytest.mark.parametrize("title", ["Walk to Olde Town 🏡 Light Rail", "WALK TO OLDE TOWN LIGHT RAIL",
                                   "Walk to Olde Town!! Light Rail", "Olde Town ★ Light Rail",
                                   "HOT TUB CABIN near the LAKE"])
def test_title_style_violations_are_rejected(title):
    with pytest.raises(ValueError, match="title"):
        rr.validate_result(_result(title=title))


@pytest.mark.parametrize("title", ["Walk to Olde Town + Light Rail · Dogs welcome",
                                   "Near UBC, YVR and the Light Rail", "Ski-in/out chalet, hot tub",
                                   "Walk to UHNBC | Boho Suite · Firepit · Pets OK"])
def test_real_titles_pass(title):
    rr.validate_result(_result(title=title))


def test_license_numbers_and_distances_are_not_phone_numbers():
    rr.validate_result(_result(the_space="STR License #123456. Boulder: about 30 minutes. "
                                          "Sleeps 8 across 3 bedrooms, 2024 renovation."))


def test_captions_for_photos_not_in_the_gallery_are_labelled_new(tmp_path):
    """olde-town-ambler 2026-08-18 captioned #99, the map photo the report told the host to
    create. Legitimate, so it is labelled a new photo rather than rejected; a mistyped
    order shows up the same way, in plain sight of whoever pastes it."""
    (tmp_path / "images.json").write_text(json.dumps({"data": [{"order": 0}, {"order": 1}]}))
    data = _result(captions=[{"order": 1, "caption": "Living room", "subject": "living room"},
                             {"order": 99, "caption": "Map", "subject": "map"}])
    assert rr.check_caption_orders(data, tmp_path) == [99]
    paste = rr.build_paste_block(data)
    assert "[NEW PHOTO to create: map]" in paste and "[#1 living room]" in paste


@pytest.mark.parametrize("before,after", [
    ("Sleeps 6 — hot tub on the deck", "Sleeps 6. Hot tub on the deck"),
    ("Relax—unwind.", "Relax. Unwind."),
    ("Quiet street. — Walk everywhere", "Quiet street. Walk everywhere"),
    ("Ends with a dash —", "Ends with a dash."),
    ("https://a.com/x—y", "https://a.com/x—y"),
])
def test_em_dash_rewrite_leaves_real_sentences(before, after):
    key = "airbnb_url" if before.startswith("http") else "summary"
    assert rr.normalize_prose({key: before})[key] == after


def test_caption_order_that_contradicts_the_photo_plan_is_flagged():
    """boho-bliss 2026-09-26 pass 5: the Photo Plan said 45 -> 30 -> 1 -> 8 -> 12 while the
    captions list led 45, 31, 1, 8, 12. Overrides are allowed but must be visible."""
    data = {"optimized": {"captions": [{"order": o} for o in (45, 31, 1, 8, 12, 11)]},
            "photos": {"recommended_top5_order": [45, 30, 1, 8, 12]}}
    msg = rr.caption_order_note(data)
    assert msg and "31" in msg and "30" in msg
    data["optimized"]["captions"][1]["order"] = 30
    assert rr.caption_order_note(data) is None
