"""Bugs found by the 2026-09-26 portfolio run (8 listings, one report agent each)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_digest as bd
import occupancy
import render_report as rr


def test_booking_rate_that_implies_impossible_bookings_is_flagged():
    """boho-bliss and sunburst-chalet: 7.12% of 687 views would be 49 bookings in one month
    for one suite. RankBreeze's booking rate is not bookings per view."""
    funnel = {"months": {"Aug (pull 2026-08-31)": {"click_through_rate_pct": 17.78, "views": 687,
                                                    "booking_rate_pct": 7.12}}}
    note = bd._booking_rate_scale(funnel)
    assert note and "up to 49 bookings" in note and "similar listings" in note
    assert bd._funnel_checks(funnel) == [], "a scale note, not a month to distrust"


def test_a_plausible_booking_rate_gets_no_scale_note():
    funnel = {"months": {"Jul": {"click_through_rate_pct": 12.0, "views": 400, "booking_rate_pct": 2.0}}}
    assert bd._booking_rate_scale(funnel) is None


def _result(funnel):
    return {"listing": {"name": "X"}, "funnel": funnel,
            "optimized": {"title": "Quiet suite near the hospital", "summary": "A calm stay.",
                          "the_space": "One bedroom.", "captions": []}}


def test_funnel_months_as_a_list_is_a_clear_validation_error():
    """sunburst-chalet: views_monthly written as a list crashed report.html.j2 with a raw
    traceback instead of telling the writer what shape to use."""
    with pytest.raises(ValueError, match="funnel.views_monthly must map month to value"):
        rr.validate_result(_result({"views_monthly": [687, 486]}))


def test_funnel_months_as_a_dict_is_accepted():
    rr.validate_result(_result({"views_monthly": {"Aug": 687}, "booking_rate_monthly": {"Aug": "7.12%"}}))


def test_monthly_occupancy_keeps_the_half_point():
    """apres-arcade: December 12.5% was shown as 12% (Python rounds halves to even)."""
    result = {"forward_window": {"occupancy_pct": 12.5, "days": 90},
              "monthly": {"Dec": {"occupancy_pct": 12.5}, "Jan": {"occupancy_pct": 40.0},
                          "Feb": {"occupancy_pct": None}}}
    block = occupancy.report_block(result, None, None)
    assert block["monthly"] == {"Dec": "12.5%", "Jan": "40%", "Feb": "n/a"}


def test_owner_facts_from_config_reach_the_digest(tmp_path, monkeypatch):
    """2026-09-26: owner-confirmed facts (electric fireplace, private hot tub) were pasted into
    each report agent's prompt by hand. A portfolio run needs them in the digest itself."""
    import json
    cfg = tmp_path / "properties.json"
    cfg.write_text(json.dumps({"sunburst-chalet": {
        "season": "Ski season in Sun Peaks (Dec to Apr)",
        "owner_facts": ["The hot tub is private to guests", "Never call the cabin detached"],
        "notes": "tenant suite downstairs"}}))
    monkeypatch.setattr(bd, "CONFIG", cfg)
    wd = tmp_path / "sunburst-chalet"
    wd.mkdir()
    (wd / "subject.json").write_text(json.dumps({"data": {"name": "x"}}))
    out = bd.build(wd)
    block = out.split("# OWNER NOTES")[1].split("\n# ")[0]
    assert "Ski season in Sun Peaks" in block and "hot tub is private" in block
    assert "tenant suite downstairs" in block
    assert out.index("# OWNER NOTES") < out.index("# SUBJECT"), "the writer must read them first"


def test_no_owner_notes_section_without_config(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(bd, "CONFIG", tmp_path / "missing.json")
    (tmp_path / "subject.json").write_text(json.dumps({"data": {"name": "x"}}))
    assert "# OWNER NOTES" not in bd.build(tmp_path)
