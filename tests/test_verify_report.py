"""The writer's factual claims are checked against the run's files before anything renders.

apres-arcade 2026-09-28: "About 27 photos have no caption" shipped when the live gallery had 22.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import verify_report as vr


def _workdir(tmp_path, n_photos=10, uncaptioned=3, reviews=20, guest_access=""):
    photos = [{"order": i + 1, "url": f"https://x/{i}.jpg", "caption": "" if i < uncaptioned else "c"}
              for i in range(n_photos)]
    (tmp_path / "images.json").write_text(json.dumps({"data": photos}))
    (tmp_path / "reviews.json").write_text(json.dumps({"data": [{}] * reviews}))
    (tmp_path / "live_gallery.json").write_text(json.dumps({"provider": "airbnb", "listing": {
        "title": "T", "amenities": [], "guest_access": guest_access}}))
    return tmp_path


def _data(gap, captions=()):
    return {"ale_scorecard": [{"dimension": "Photos channel", "gap": gap, "fix": ""}],
            "optimized": {"captions": list(captions)}}


def test_true_claims_pass(tmp_path):
    wd = _workdir(tmp_path)
    assert vr.check(_data("3 of 10 live photos have no caption; move #4 up. 0 of the last 20 reviews "
                          "unanswered. Only 2 of 10 photos are winter."), wd) == []


def test_a_wrong_uncaptioned_count_is_caught(tmp_path):
    problems = vr.check(_data("About 7 photos have no caption."), _workdir(tmp_path))
    assert problems == ["ale_scorecard Photos channel.gap: says 7 photos have no caption, but 3 of 10 have none"]


def test_a_wrong_gallery_size_and_missing_photo_are_caught(tmp_path):
    problems = vr.check(_data("2 of 12 photos are winter; move #11 to the cover."), _workdir(tmp_path))
    assert any("gallery has 10 photos" in p for p in problems)
    assert any("photo #11 is not in this gallery" in p for p in problems)


def test_a_new_photo_the_writer_asks_for_may_be_numbered(tmp_path):
    captions = [{"order": 11, "caption": "Map", "new_photo": True}]
    assert vr.check(_data("Add a pinned map as #11.", captions), _workdir(tmp_path)) == []


def test_a_wrong_review_window_is_caught(tmp_path):
    problems = vr.check(_data("2 of the last 18 reviews mention stairs."), _workdir(tmp_path))
    assert problems == ["ale_scorecard Photos channel.gap: says the last 18 reviews, but 20 were pulled"]


def test_no_guest_access_claim_must_match_the_live_listing(tmp_path):
    long_access = "Guests have access to the main house only; the lower suite is a long-term tenant's home."
    problems = vr.check(_data("The live listing has no Guest access section."), _workdir(tmp_path, guest_access=long_access))
    assert problems and "has one" in problems[0]
    assert vr.check(_data("The live listing has no Guest access section."), _workdir(tmp_path)) == []
