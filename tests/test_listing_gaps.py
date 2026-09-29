#!/usr/bin/env python3
"""listing_gaps.py: problems on the live listing, flagged in the report as gaps.

Found on boho-bliss 2026-09-26: the live listing ticks exterior security cameras, which
Airbnb requires the description to disclose, yet no copy mentioned them and the digest never
showed them to the writer. Apres Arcade's Guest access held only registration numbers.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_digest as bd
import listing_gaps as lg
import render_report as rr


def _workdir(tmp_path, amenities=("Wifi",), **listing):
    base = {"title": "T", "summary": "S", "description": "", "amenities": list(amenities)}
    base.update(listing)
    (tmp_path / "live_gallery.json").write_text(json.dumps({"provider": "airbnb", "listing": base}))
    return tmp_path


def _issues(tmp_path, optimized, **kw):
    return [g["issue"] for g in lg.detect(_workdir(tmp_path, **kw), optimized)]


LONG_ACCESS = "The whole suite is yours, with a private entrance at the back and parking for two cars."


@pytest.mark.parametrize("access,expect", [
    ("", "no Guest access section."),
    ("Provincial registration number: H000000000", "holds only a registration number"),
    ("All space", "one short line (9 characters)"),
    (LONG_ACCESS, None),
])
def test_live_guest_access(tmp_path, access, expect):
    issues = _issues(tmp_path, {}, guest_access=access)
    assert (expect is None and not issues) or any(expect in i for i in issues)


def test_no_guest_access_claim_without_the_field(tmp_path):
    """AirROI and IntelliHost never return Guest access: absence of data is not a gap."""
    assert _issues(tmp_path, {}) == []


def test_no_live_listing_means_no_gaps(tmp_path):
    assert lg.detect(tmp_path, {"the_space": "Hot tub"}) == []


def test_ticked_cameras_must_be_disclosed(tmp_path):
    cams = ("Exterior security cameras on property",)
    assert any("Exterior security cameras" in i for i in _issues(tmp_path, {"the_space": "Cozy."}, amenities=cams))
    told = {"other_notes": "• One exterior camera covers the driveway only"}
    assert _issues(tmp_path, told, amenities=cams) == []


def test_ticked_noise_monitor_must_be_disclosed(tmp_path):
    ticked = ("Noise decibel monitors on property",)
    assert any("Noise monitors" in i for i in _issues(tmp_path, {"the_space": "Quiet."}, amenities=ticked))
    assert _issues(tmp_path, {"other_notes": "• A noise monitor (no recording)"}, amenities=ticked) == []


def test_plural_disclosures_count(tmp_path):
    """boho-bliss 2026-09-29: other_notes said "Exterior security cameras on the property", in
    Airbnb's own plural, and the report still said the copy never mentions them."""
    cams = ("Exterior security cameras on property",)
    assert _issues(tmp_path, {"other_notes": "• Exterior security cameras on the property"}, amenities=cams) == []
    monitors = ("Noise decibel monitors on property",)
    assert _issues(tmp_path, {"other_notes": "• Noise monitors in the living room"}, amenities=monitors) == []
    assert _issues(tmp_path, {"other_notes": "• Noise sensors, no recording"}, amenities=monitors) == []


@pytest.mark.parametrize("copy,ticked,flagged", [
    ("Soak in the private hot tub.", (), "hot tub"),
    ("Self check-in with a keypad.", ("Self check-in",), "keypad"),
    ("Self check-in with a smart lock.", ("Self check-in", "Smart lock"), None),
    ("Dogs welcome, bowls provided.", (), "pets allowed"),
    ("Dogs welcome, bowls provided.", ("Pets allowed",), None),
    ("No hot tub, but a great fire pit.", ("Fire pit",), None),
    ("Rec room with a pool table.", (), None),
    ("Hair dryer in the bath.", (), None),
    ("Walk to the community pool.", (), None),
    ("Cozy up by the fireplace.", ("Indoor fireplace: wood-burning",), None),
    ("Free washer and dryer in the suite.", ("Free washer – In building", "Free dryer – In building"), None),
    ("Window A/C in the bedroom.", (), "air conditioning"),
    ("A two-storey gym downstairs.", ("Shared gym in building",), None),
    ("Heated pool all year.", ("Private outdoor pool – heated",), None),
    ("Heated pool all year.", ("Pool table",), "pool"),
])
def test_copy_claims_against_ticked_amenities(tmp_path, copy, ticked, flagged):
    issues = _issues(tmp_path, {"the_space": copy}, amenities=ticked)
    assert (flagged is None and not issues) or any(f"mentions {flagged}" in i for i in issues)


def test_off_property_sections_do_not_count_as_amenity_claims(tmp_path):
    opt = {"the_space": "Cozy.", "neighborhood": "The aquatic centre pool and a gym are 5 min away.",
           "captions": [{"order": 1, "caption": "Hot tub at the ski lodge"}]}
    assert _issues(tmp_path, opt) == []


def test_writer_gaps_are_validated():
    base = {"listing": {"name": "x"}, "optimized": {"title": "t", "summary": "s", "the_space": "sp"}}
    rr.validate_result({**base, "listing_gaps": [{"issue": "Fireplace box says wood-burning", "fix": "Change it"}]})
    for bad in ({"issue": "x"}, [{"issue": "", "fix": "y"}], [{"issue": "x" * 301, "fix": "y"}], ["x"]):
        with pytest.raises(ValueError, match="listing_gaps"):
            rr.validate_result({**base, "listing_gaps": bad})


def test_report_shows_detected_and_writer_gaps(tmp_path):
    data = {"listing": {"name": "x", "slug": "x"}, "run_date": "2026-09-26", "branding": {},
            "optimized": {"title": "t", "summary": "s", "the_space": "sp"},
            "detected_gaps": [{"issue": "Cameras undisclosed.", "fix": "Disclose them."}],
            "listing_gaps": [{"issue": "Fireplace box says wood-burning.", "fix": "Change it to electric."}]}
    env = rr.Environment(loader=rr.FileSystemLoader(str(rr.TEMPLATES_DIR)),
                         autoescape=lambda n: bool(n) and n.endswith(".html.j2"),
                         trim_blocks=True, lstrip_blocks=True)
    for name in ("report.md.j2", "report.html.j2"):
        out = env.get_template(name).render(data=data, lower_sections=rr.LOWER_SECTIONS)
        assert "Listing gaps" in out and "Cameras undisclosed." in out and "Change it to electric." in out
        assert out.index("Listing gaps") < out.index("Optimized Copy")


def test_digest_shows_ticked_amenities_and_required_disclosures(tmp_path):
    from test_live_gallery import GOOD
    (tmp_path / "subject.json").write_text(json.dumps({"data": {"name": "x", "public_name": "T", "summary": "S"}}))
    (tmp_path / "comps.json").write_text(json.dumps({"comp_count": 1, "top_comps": [], "comp_title_samples": [],
        "market_amenity_frequency": [{"amenity": "Wifi", "pct": 100}]}))
    (tmp_path / "live_gallery.json").write_text(json.dumps({**GOOD, "listing": {
        "title": "T", "summary": "S", "description": "",
        "amenities": ["Wifi", "Smart lock", "Exterior security cameras on property"]}}))
    out = bd.build(tmp_path)
    assert "ticked_on_live_airbnb" in out and "Smart lock" in out
    assert "DISCLOSURE REQUIRED: Exterior security cameras" in out


def test_a_provider_snapshot_never_produces_a_live_gap(tmp_path):
    """RankBreeze's stale "Shared hot tub" box must never become a claim about the live listing."""
    (tmp_path / "live_gallery.json").write_text(json.dumps({"provider": "rankbreeze", "listing": {
        "title": "T", "amenities": ["Shared hot tub"], "guest_access": ""}}))
    assert lg.detect(tmp_path, {"the_space": "Private hot tub on the deck."}) == []
