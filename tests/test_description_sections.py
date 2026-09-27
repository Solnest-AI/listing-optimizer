#!/usr/bin/env python3
"""The lower description sections (references/description-sections.md, researched 2026-09-26).

Guest access, Other things to note, Neighborhood and Getting around are optional in
result.json but expected: a missing one is a warning, not a failed render, so older results
still render. Guest access invites door codes and addresses, which never belong in public copy.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_digest as bd
import render_report as rr

SECTIONS = {"guest_access": "The whole suite is yours, with its own entrance at the back.",
            "other_notes": "• Basement suite, 5 steps down\n• Check-in 4 PM, check-out 10 AM",
            "neighborhood": "A 2-min walk to the hospital on a quiet crescent.",
            "getting_around": "Private parking for 2. Downtown is a 5-min drive."}


def _result(**opt):
    base = {"title": "Walk to UHNBC · Boho suite", "summary": "Calm.", "the_space": "One bedroom.",
            "captions": []}
    base.update(opt)
    return {"listing": {"name": "Boho", "slug": "boho"}, "run_date": "2026-09-26", "optimized": base}


def test_sections_render_in_editor_order_with_their_airbnb_field():
    data = _result(**SECTIONS, host_to_confirm=["Who lives upstairs?"])
    rr.validate_result(data)
    paste = rr.build_paste_block(data)
    heads = ["--- THE SPACE ---", "--- GUEST ACCESS ---",
             "--- OTHER THINGS TO NOTE (Airbnb editor: Other details to note) ---",
             "--- NEIGHBORHOOD (Airbnb editor: Location > Neighborhood description) ---",
             "--- GETTING AROUND (Airbnb editor: Location > Getting around) ---",
             "--- CONFIRM WITH THE HOST (not for pasting) ---"]
    assert [paste.index(h) for h in heads] == sorted(paste.index(h) for h in heads)
    assert "- Who lives upstairs?" in paste
    assert "INTERACTION" not in paste


def test_missing_sections_are_a_warning_not_a_failure():
    data = _result(guest_access=SECTIONS["guest_access"])
    rr.validate_result(data)
    assert rr.missing_lower_sections(data["optimized"]) == ["Other things to note", "Neighborhood",
                                                            "Getting around"]
    assert "--- NEIGHBORHOOD" not in rr.build_paste_block(data)


@pytest.mark.parametrize("key,bad,match", [
    ("guest_access", "", "nonempty"),
    ("neighborhood", 42, "nonempty"),
    ("guest_access", "x" * 601, "600 characters"),
    ("other_notes", "x" * 901, "900 characters"),
])
def test_section_shape_and_caps(key, bad, match):
    with pytest.raises(ValueError, match=match):
        rr.validate_result(_result(**{key: bad}))


def test_host_to_confirm_must_be_a_list_of_questions():
    with pytest.raises(ValueError, match="host_to_confirm"):
        rr.validate_result(_result(host_to_confirm="Who lives upstairs?"))


@pytest.mark.parametrize("key,text", [
    ("guest_access", "Self check-in. Door code 4821#."),
    ("guest_access", "The lockbox code is 0912."),
    ("guest_access", "Keypad code: 55120"),
    ("other_notes", "• Wi-Fi password: PineCone2024"),
    ("the_space", "Fast Wi-Fi (password is maple123)."),
])
def test_access_codes_and_passwords_are_rejected(key, text):
    with pytest.raises(ValueError, match="access code or password"):
        rr.validate_result(_result(**{key: text}))


@pytest.mark.parametrize("key,text", [
    ("neighborhood", "Tucked away at 118 Maple Crescent, steps from the hospital."),
    ("guest_access", "Park in front of 45 Main St and walk around back."),
    ("summary", "Our home at 118 Pine Ridge Road sleeps 6."),
])
def test_street_addresses_are_rejected(key, text):
    with pytest.raises(ValueError, match="street address"):
        rr.validate_result(_result(**{key: text}))


@pytest.mark.parametrize("text", [
    "The Wi-Fi password is in your check-in message.",
    "We send the door code before you arrive.",
    "A 15 Minute Drive to the ski hill. A 5 Star Place to land after a shift.",
    "Highway 97 is 3 blocks away. Sleeps 3 across 2 beds. 5 steps down to your door.",
    "Downtown is a 5-min drive; UNBC is 15 min.",
])
def test_ordinary_copy_is_not_mistaken_for_codes_or_addresses(text):
    rr.validate_result(_result(guest_access=text, the_space=text))


@pytest.mark.parametrize("text,empty", [
    ("Municipal registration number: 1234\nProvincial registration number: H000000000", True),
    ("Business licence #22-0045. STR permit 7781", True),
    ("Main house only. Tenant suite below.", False),
    ("Private entrance at the back. Provincial registration number: H000000000", False),
])
def test_registration_numbers_are_not_a_guest_access_section(text, empty):
    """apres-arcade 2026-09-26: RankBreeze returned the registration numbers as Guest access;
    the live listing shows no Guest access section at all."""
    assert bd._registration_only(" ".join(text.split())) is empty


def test_digest_says_guest_access_is_empty_when_it_holds_only_registration(tmp_path):
    from test_live_gallery import GOOD, _airroi_subject
    (tmp_path / "subject.json").write_text(json.dumps({"data": {"name": "x", "public_name": "T", "summary": "S"}}))
    (tmp_path / "comps.json").write_text(json.dumps({"comp_count": 0, "top_comps": [], "market_amenity_frequency": [],
        "comp_title_samples": [], "subject_listing": _airroi_subject(5)}))
    (tmp_path / "live_gallery.json").write_text(json.dumps({**GOOD, "listing": {
        "title": "T", "summary": "S", "description": "", "amenities": [],
        "guest_access": "Provincial registration number: H000000000"}}))
    out = bd.build(tmp_path)
    assert "guest_access (live Airbnb): EMPTY" in out and "H000000000" not in out


def test_templates_show_the_sections_and_questions(tmp_path):
    data = _result(**SECTIONS, host_to_confirm=["Minutes to the airport?"])
    rr.validate_result(data)
    env = rr.Environment(loader=rr.FileSystemLoader(str(rr.TEMPLATES_DIR)),
                         autoescape=lambda n: bool(n) and n.endswith(".html.j2"),
                         trim_blocks=True, lstrip_blocks=True)
    data["branding"] = {}
    for name in ("report.md.j2", "report.html.j2"):
        out = env.get_template(name).render(data=data, lower_sections=rr.LOWER_SECTIONS)
        assert "Getting around" in out and "Private parking for 2" in out
        assert "Minutes to the airport?" in out


@pytest.mark.parametrize("text", [
    "Transient Occupancy Tax Certificate #T-2024-0183",
    "TMK: 3-9-012-045-0000 GE-123-456-7890-01 TA-123-456-7890-01",
    "City of Arvada STR License 2023-0045",
])
def test_registration_wording_from_other_markets_is_not_a_section(text):
    assert bd._registration_only(text)
