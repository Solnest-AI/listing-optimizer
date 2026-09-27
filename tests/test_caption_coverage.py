"""Full caption coverage (Ryan, 2026-09-26): every photo the host keeps gets a caption.

The 2026-09-26 portfolio run captioned 33 of 79 photos on apres-arcade and 35 of 89 on
farmhouse-at-sixty-six, and nothing reported the gap. Photos the writer says to delete, and
exact duplicates the tool detected, are not missing captions.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import render_report as rr


def _data(caption_orders, remove=None, repeats=None, live=True):
    opt = {"title": "Quiet suite by the hospital", "summary": "Calm.", "the_space": "One bedroom.",
           "captions": [{"order": o, "subject": f"s{o}", "caption": f"Caption {o}"} for o in caption_orders]}
    if remove is not None:
        opt["remove_orders"] = remove
    photos = {"duplicate_repeats": repeats or [],
              "gallery_source": {"kind": "live_airbnb" if live else "pms"}}
    return {"listing": {"name": "x", "slug": "x"}, "run_date": "2026-09-26", "optimized": opt, "photos": photos}


def test_every_photo_captioned_is_full_coverage():
    cov = rr.caption_coverage(_data([0, 1, 2, 3]), {0, 1, 2, 3})
    assert cov == {"gallery": 4, "captioned": 4, "missing": [], "remove": []}


def test_uncaptioned_photos_are_listed():
    assert rr.caption_coverage(_data([0, 1]), {0, 1, 2, 3, 4})["missing"] == [2, 3, 4]


def test_photos_to_delete_and_detected_duplicates_are_not_missing():
    cov = rr.caption_coverage(_data([0, 1, 99], remove=[2], repeats=[4]), {0, 1, 2, 3, 4})
    assert cov["missing"] == [3]
    assert cov["remove"] == [2, 4]
    assert cov["captioned"] == 2, "a caption for a NEW photo (#99) does not cover the gallery"


def test_a_captioned_duplicate_is_kept_not_removed():
    cov = rr.caption_coverage(_data([0, 1, 4], repeats=[4]), {0, 1, 4})
    assert cov["remove"] == [] and cov["missing"] == []


@pytest.mark.parametrize("bad", [[-1], [1, 1], ["3"], "3"])
def test_remove_orders_must_be_unique_photo_numbers(bad):
    with pytest.raises(ValueError, match="remove_orders"):
        rr.validate_result(_data([0], remove=bad))


def test_paste_block_lists_the_photos_to_delete():
    data = _data([0], remove=[5])
    data["caption_coverage"] = rr.caption_coverage(data, {0, 5})
    paste = rr.build_paste_block(data)
    assert "--- DELETE THESE PHOTOS ---" in paste and "Airbnb photo 5" in paste


def test_gallery_orders_come_from_images_json(tmp_path):
    (tmp_path / "images.json").write_text(json.dumps({"data": [{"order": 1}, {"order": 2}]}))
    assert rr.gallery_orders(tmp_path) == {1, 2}
    assert rr.gallery_orders(tmp_path / "missing") is None
