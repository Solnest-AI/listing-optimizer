#!/usr/bin/env python3
"""Tests for merging the machine-generated blocks into result.json from disk.

Measured on two real runs, 46% and 49% of result.json was the model retyping data that
already sat in photo_scores.json / comps.json / occupancy.json / cadence.json. Worse than
the token cost: on the 2026-08-05 boho-bliss run the hand-copy silently DROPPED the #5
comp ("Walk to UHNBC | Boho Suite • Firepit • Pets OK") — the single comp whose title
pattern most resembled the subject never reached the client-facing report.

These tests lock in: fill what's missing, never clobber the model's own reasoning, and
never let a bad file take the render down.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import render_report as rr


def _workdir(tmp, **files):
    d = Path(tmp)
    for name, payload in files.items():
        (d / name).write_text(json.dumps(payload), encoding="utf-8")
    return d


PHOTO_SCORES = {
    "hero": 3, "recommended_top5_order": [3, 1, 2], "top5_beats": ["hot_tub", "kitchen_dining", "living_room"],
    "reshoot": [7], "restage": [8], "gaps": ["No map photo"],
    "coverage_note": "ranked 2 of 3 photos — 1 FAILED and were excluded from ranking",
    "failed": [{"order": 9, "error": "gemini 429"}],
    "photos": [
        {"order": 3, "url": "u3", "subject": "hot tub", "subject_kind": "hot_tub", "avg": 4.5, "scored": True},
        {"order": 1, "url": "u1", "subject": "kitchen", "subject_kind": "kitchen_dining", "avg": 4.0, "scored": True},
        {"order": 9, "url": "u9", "scored": False, "error": "gemini 429"},
    ],
}
COMPS = {
    "comp_count": 25, "ranking_basis": "demand only",
    "comp_title_samples": ["Cabin A", "Suite B"],
    "top_comps": [
        {"name": "Cabin A", "airbnb_url": "https://a", "bedrooms": 2, "baths": 1, "guests": 4,
         "ratings": {"num_reviews": 80, "rating_overall": 4.9}, "performance": {"ttm_occupancy": 0.62}},
        {"name": "Suite B", "airbnb_url": "https://b", "bedrooms": 2, "baths": 1, "guests": 4,
         "ratings": {"num_reviews": 25, "rating_overall": 4.96}, "performance": {"ttm_occupancy": 0.59}},
    ],
}
OCCUPANCY = {"source": "Hospitable", "monthly": {"Sep": "13%"},
             "report_block": {"source": "Hospitable", "forward_pct": 12.1, "forward_days": 91,
                              "upcoming_reservations": 2, "monthly": {"Sep": "13%"},
                              "rankbreeze_crosscheck": "agree"}}
CADENCE = {"listing": "x", "as_of": "2026-09-20",
           "due": [{"item": "Title", "last": "2026-06-08", "due": "2026-07-06", "status": "DUE"}]}


def test_missing_blocks_are_filled_from_disk():
    with tempfile.TemporaryDirectory() as tmp:
        d = _workdir(tmp, **{"photo_scores.json": PHOTO_SCORES, "comps.json": COMPS,
                             "occupancy.json": OCCUPANCY, "cadence.json": CADENCE})
        data = {"listing": {"slug": "x"}, "optimized": {"title": "t"}}
        filled = rr.merge_machine_blocks(data, d)
        assert {k.split("(")[0] for k in filled} == {"photos", "comps", "occupancy", "cadence"}
        assert data["photos"]["hero"] == 3
        assert data["cadence"]["due"][0]["item"] == "Title"
        # occupancy takes report_block, exactly as the skill told the model to do by hand
        assert data["occupancy"] == OCCUPANCY["report_block"]


def test_every_top_comp_survives_the_merge():
    """The real defect: a hand-copy dropped one of the ten top comps."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _workdir(tmp, **{"comps.json": COMPS})
        data = {}
        rr.merge_machine_blocks(data, d)
        assert [c["name"] for c in data["comps"]["top"]] == ["Cabin A", "Suite B"]
        assert len(data["comps"]["top"]) == len(COMPS["top_comps"]), "a comp went missing"


def test_model_authored_values_are_never_clobbered():
    """amenity_gaps needs the subject's amenity list, so it stays the model's call and
    must survive a merge that fills the rest of the same block."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _workdir(tmp, **{"comps.json": COMPS, "photo_scores.json": PHOTO_SCORES})
        data = {"comps": {"amenity_gaps": ["hot tub", "fast wifi"]},
                "photos": {"hero": 99}}  # model deliberately overrode the hero
        rr.merge_machine_blocks(data, d)
        assert data["comps"]["amenity_gaps"] == ["hot tub", "fast wifi"], "model value overwritten"
        assert data["comps"]["comp_count"] == 25, "rest of the block still filled"
        assert data["photos"]["hero"] == 99, "an explicit model override must win"


def test_unranked_photos_are_carried_into_the_report_data():
    with tempfile.TemporaryDirectory() as tmp:
        d = _workdir(tmp, **{"photo_scores.json": PHOTO_SCORES})
        data = {}
        rr.merge_machine_blocks(data, d)
        assert data["photos"]["unranked"] == [9]
        assert "FAILED" in data["photos"]["coverage_note"]
        # only scored photos reach the report table
        assert [p["order"] for p in data["photos"]["scored"]] == [3, 1]


def test_corrupt_file_is_skipped_not_fatal():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "comps.json").write_text("{ broken json")
        (d / "cadence.json").write_text(json.dumps(CADENCE))
        data = {}
        filled = rr.merge_machine_blocks(data, d)  # must not raise
        assert "comps" not in data, "a corrupt file must not produce a half-block"
        assert any(f.startswith("cadence") for f in filled), "good files still merge"


def test_absent_workdir_files_are_a_no_op():
    with tempfile.TemporaryDirectory() as tmp:
        data = {"optimized": {"title": "t"}}
        assert rr.merge_machine_blocks(data, Path(tmp)) == []
        assert data == {"optimized": {"title": "t"}}


def test_merge_covers_every_field_the_templates_read():
    """If a template reads data.photos.hero and the merge stops emitting it, the report
    silently loses a section. Keep the contract explicit."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _workdir(tmp, **{"photo_scores.json": PHOTO_SCORES, "comps.json": COMPS,
                             "occupancy.json": OCCUPANCY, "cadence.json": CADENCE})
        data = {}
        rr.merge_machine_blocks(data, d)
        required = {
            "photos": ["hero", "recommended_top5_order", "reshoot", "restage", "gaps", "scored"],
            "comps": ["top", "title_patterns"],
            "occupancy": ["forward_pct", "forward_days", "upcoming_reservations",
                          "monthly", "rankbreeze_crosscheck"],
            "cadence": ["due"],
        }
        for block, keys in required.items():
            for k in keys:
                assert k in data[block], f"template reads data.{block}.{k} but merge omits it"


def test_funnel_is_deliberately_not_merged():
    """result.json's funnel block is a model-NORMALISED view of RankBreeze. No run on disk
    had a funnel.json to verify the raw shape, so merging it would be a guess."""
    assert "funnel" not in rr.MACHINE_BLOCKS




def test_memory_record_rehydrates_the_machine_blocks():
    """REGRESSION (found by a full end-to-end run, 2026-09-20): when result.json stopped
    carrying `photos` and `occupancy`, `memory.py record` silently wrote photo_hero=null,
    photo_top5=[], reshoot_count=0 and occupancy_forward_pct=null. That destroys the
    run-to-run trend the memory layer exists for, and nothing errors — the record just
    goes quietly blank. memory.py now merges from the working dir using THIS module's
    merge, so the two can never drift apart."""
    import subprocess
    import sys as _sys

    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        d = _workdir(tmp, **{"photo_scores.json": PHOTO_SCORES, "comps.json": COMPS,
                             "occupancy.json": OCCUPANCY, "cadence.json": CADENCE})
        # A slim result.json exactly as the model is now told to write it.
        (d / "result.json").write_text(json.dumps({
            "listing": {"name": "X", "slug": "x", "city": "Y"},
            "run_date": "2026-09-20",
            "ale_scorecard": [{"dimension": "A", "score": 4}],
            "optimized": {"title": "t", "summary": "s", "summary_char_count": 1},
            "comps": {"amenity_gaps": ["hot tub"]},
        }), encoding="utf-8")
        r = subprocess.run(
            [_sys.executable, str(root / "scripts" / "memory.py"), "record",
             "--result", str(d / "result.json"), "--workdir", str(d),
             "--no-local", "--out", str(d / "record.json")],
            capture_output=True, text=True, cwd=str(root))
        assert r.returncode == 0, f"record failed: {r.stderr[-400:]}"
        rec = json.loads((d / "record.json").read_text())
        assert rec["photo_hero"] == 3, f"hero lost: {rec['photo_hero']}"
        assert rec["photo_top5"] == [3, 1, 2], f"top5 lost: {rec['photo_top5']}"
        assert rec["reshoot_count"] == 1, f"reshoot count lost: {rec['reshoot_count']}"
        assert rec["occupancy_forward_pct"] == 12.1, "occupancy lost"
        assert rec["occupancy_monthly"] == {"Sep": "13%"}, "monthly occupancy lost"
        assert rec["amenity_gaps"] == ["hot tub"], "model-authored value clobbered"


def test_memory_record_defaults_workdir_to_the_result_directory():
    """The skill passes --workdir, but a hand-run without it must still work rather than
    silently degrade, since the result almost always sits in the working dir."""
    import subprocess
    import sys as _sys

    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        d = _workdir(tmp, **{"photo_scores.json": PHOTO_SCORES, "occupancy.json": OCCUPANCY})
        (d / "result.json").write_text(json.dumps({
            "listing": {"slug": "x"}, "run_date": "2026-09-20",
            "ale_scorecard": [{"dimension": "A", "score": 4}],
            "optimized": {"title": "t"},
        }), encoding="utf-8")
        r = subprocess.run(
            [_sys.executable, str(root / "scripts" / "memory.py"), "record",
             "--result", str(d / "result.json"), "--no-local",
             "--out", str(d / "record.json")],
            capture_output=True, text=True, cwd=str(root))
        assert r.returncode == 0, r.stderr[-300:]
        rec = json.loads((d / "record.json").read_text())
        assert rec["photo_hero"] == 3, "workdir default did not kick in"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("✅ render merge tests passed")
