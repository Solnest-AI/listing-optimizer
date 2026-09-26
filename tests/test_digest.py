#!/usr/bin/env python3
"""Tests for build_digest.py — the one file the model is allowed to read.

The digest is the token wall. If it silently grows (e.g. stops capping reviews) the run
cost goes back up; if it silently drops a field the rubrics score, the optimization gets
worse with no visible error. Both directions are tested.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_digest as bd


def _wd(tmp, **files):
    d = Path(tmp)
    for name, payload in files.items():
        (d / name).write_text(json.dumps(payload), encoding="utf-8")
    return d


SUBJECT = {"data": {"name": "Cabin", "public_name": "Cozy Cabin", "summary": "S",
                    "description": "D", "amenities": ["Hot tub"],
                    "capacity": {"max": 6, "bedrooms": 2, "bathrooms": 1},
                    "room_details": [], "house_rules": {},
                    "address": {"city": "Sun Peaks", "coordinates": {"latitude": "50.8",
                                                                     "longitude": "-119.9"}}}}


def _reviews(n):
    return {"data": [{"reviewed_at": f"2026-{(i % 12) + 1:02d}-01",
                      "public": {"rating": 5, "review": f"review body {i}"},
                      "private": {"detailed_ratings": [{"type": "cleanliness", "rating": 5}]},
                      "responded_at": None if i % 2 else "2026-01-01"} for i in range(n)]}


def test_review_cap_is_enforced_regardless_of_what_landed_on_disk():
    """Even if a whole history somehow lands on disk, the digest carries only 20 bodies."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _wd(tmp, **{"subject.json": SUBJECT, "reviews.json": _reviews(154)})
        text = bd.build(d, review_cap=20)
        assert text.count("review body ") == 20, "review cap leaked"
        assert "20 shown; 154 pulled" in text


def test_aggregates_span_the_pull_and_are_labelled_with_it():
    """The pull is now the 20 newest, so the aggregates cover 20 — and they must SAY so.
    Labelling a 20-review average "(all)" would overstate it, and the unanswered count is
    the number a host might actually act on."""
    with tempfile.TemporaryDirectory() as tmp:
        revs = _reviews(20)
        revs["_pull"] = {"requested": 20, "returned": 20, "total_available": 101,
                         "complete_history": False}
        d = _wd(tmp, **{"subject.json": SUBJECT, "reviews.json": revs})
        text = bd.build(d, review_cap=20)
        assert "20 shown; 20 pulled of 101 lifetime" in text, "lifetime denominator lost"
        assert "unanswered_reviews(last 20 only): 10" in text, f"mislabelled scope:\n{text[-500:]}"
        assert "category_avgs_0to5(last 20 only, unrated excluded)" in text
        assert "NOT all 101" in text, "the model must be told the window is partial"


def test_complete_history_is_labelled_all():
    with tempfile.TemporaryDirectory() as tmp:
        revs = _reviews(40)
        revs["_pull"] = {"requested": "all", "returned": 40, "total_available": 40,
                         "complete_history": True}
        d = _wd(tmp, **{"subject.json": SUBJECT, "reviews.json": revs})
        text = bd.build(d, review_cap=20)
        assert "unanswered_reviews(all): 20" in text
        assert "NOT all" not in text, "a complete pull must not carry the partial warning"


def test_unknown_provenance_defaults_to_the_cautious_label():
    """A non-Hospitable PMS stages reviews.json itself and writes no `_pull` block, so we
    cannot know whether it is complete. Understating confidence is the safe default —
    claiming a lifetime total we did not verify is not."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _wd(tmp, **{"subject.json": SUBJECT, "reviews.json": _reviews(30)})
        text = bd.build(d, review_cap=20)
        assert "last 30 only" in text, "unverified completeness must not be labelled (all)"


def test_newest_reviews_are_the_ones_kept():
    with tempfile.TemporaryDirectory() as tmp:
        revs = {"data": [
            {"reviewed_at": "2021-01-01", "public": {"rating": 3, "review": "OLD"}},
            {"reviewed_at": "2026-09-01", "public": {"rating": 5, "review": "NEW"}},
        ]}
        d = _wd(tmp, **{"subject.json": SUBJECT, "reviews.json": revs})
        text = bd.build(d, review_cap=1)
        assert "NEW" in text and "OLD" not in text


def test_photo_beats_and_coverage_reach_the_digest():
    """The model must see that the cover set has distinct beats and whether the ranking
    was complete — both are new signals it cannot infer from scores alone."""
    with tempfile.TemporaryDirectory() as tmp:
        ps = {"hero": 3, "recommended_top5_order": [3, 1],
              "top5_beats": ["hot_tub", "kitchen_dining"],
              "coverage_note": "ranked 2 of 3 photos — 1 FAILED and were excluded from ranking",
              "failed": [{"order": 9, "error": "429"}],
              "distinct_beats": ["hot_tub", "kitchen_dining"], "gaps": [],
              "reshoot": [], "restage": [],
              "photos": [{"order": 3, "avg": 4.5, "subject_kind": "hot_tub",
                          "subject": "hot tub at dusk", "scored": True, "flags": []},
                         {"order": 9, "scored": False, "error": "429"}]}
        d = _wd(tmp, **{"subject.json": SUBJECT, "photo_scores.json": ps})
        text = bd.build(d)
        assert "top5_beats: ['hot_tub', 'kitchen_dining']" in text
        assert "FAILED" in text and "UNRANKED (failed scoring): [9]" in text
        assert "[hot_tub] hot tub at dusk" in text
        assert "#9" not in text, "an unscored photo must not appear as a scored row"


def _review(platform, orig, rating, ratings, responded=True):
    """A review shaped exactly like the live payload (public/private as dicts)."""
    return {"platform": platform, "reviewed_at": "2026-09-01",
            "responded_at": "2026-09-02" if responded else None,
            "public": {"rating": rating, "rating_platform_original": orig, "review": "text"},
            "private": {"detailed_ratings": [{"type": k, "rating": v, "comment": None}
                                             for k, v in ratings.items()]}}


def test_booking_10_scale_is_normalised_to_5():
    """THE BUG: averaging Booking.com's 1-10 categories with Airbnb's 1-5 produced
    cleanliness 5.5 out of 5 on a real report. Measured: booking's
    rating_platform_original/rating is exactly 2.0, so the divisor comes from the payload."""
    with tempfile.TemporaryDirectory() as tmp:
        revs = {"data": [
            _review("booking", 10, 5, {"cleanliness": 10, "location": 10}),
            _review("airbnb", 5, 5, {"cleanliness": 5, "location": 5}),
        ]}
        d = _wd(tmp, **{"subject.json": SUBJECT, "reviews.json": revs})
        text = bd.build(d)
        assert '"cleanliness": {"avg": 5.0, "n": 2}' in text, f"scale not normalised:\n{text[-400:]}"
        assert "5.5" not in text, "an impossible >5 average survived"


def test_a_ten_scale_low_score_is_not_read_as_a_five_scale_low_score():
    """booking 7.5/10 is 3.75/5, a mediocre score. Left raw it reads as 7.5/5 (impossible);
    divided by the wrong factor it could read as a crisis. Both are wrong."""
    with tempfile.TemporaryDirectory() as tmp:
        revs = {"data": [_review("booking", 9, 4.5, {"staff": 7.5})]}
        d = _wd(tmp, **{"subject.json": SUBJECT, "reviews.json": revs})
        assert '"staff": {"avg": 3.75, "n": 1}' in bd.build(d)


def test_unrated_categories_are_excluded_not_counted_as_zero():
    """Booking.com sends no 'communication', so it arrived as 0 and dragged the Airbnb
    average down. 'services' read 0.0 on a real report purely because nobody rates it."""
    with tempfile.TemporaryDirectory() as tmp:
        revs = {"data": [
            _review("booking", 10, 5, {"cleanliness": 10, "communication": 0, "services": 0}),
            _review("airbnb", 5, 5, {"cleanliness": 5, "communication": 5}),
        ]}
        d = _wd(tmp, **{"subject.json": SUBJECT, "reviews.json": revs})
        text = bd.build(d)
        assert '"communication": {"avg": 5.0, "n": 1}' in text, "a 0 was averaged in as a score"
        assert "services" not in text.split("category_avgs")[1].split("\n")[0], \
            "a category nobody rated must be absent, not 0.0"


def test_sample_size_is_reported_per_category():
    """8 guests rating 'facilities' is not the same evidence as 92 rating 'cleanliness'."""
    with tempfile.TemporaryDirectory() as tmp:
        revs = {"data": [_review("airbnb", 5, 5, {"cleanliness": 5}) for _ in range(3)]
                + [_review("booking", 10, 5, {"facilities": 10})]}
        d = _wd(tmp, **{"subject.json": SUBJECT, "reviews.json": revs})
        text = bd.build(d)
        assert '"cleanliness": {"avg": 5.0, "n": 3}' in text
        assert '"facilities": {"avg": 5.0, "n": 1}' in text
        assert "review_platforms: {'airbnb': 3, 'booking': 1}" in text


def test_scale_divisor_falls_back_when_the_pair_is_missing():
    """A PMS that doesn't send rating_platform_original still needs normalising: a category
    above 5 can only be a 10-scale."""
    r = {"public": {"rating": 5}, "private": {"detailed_ratings": [{"type": "x", "rating": 9}]}}
    assert bd._scale_divisor(r) == 2.0
    r5 = {"public": {"rating": 5}, "private": {"detailed_ratings": [{"type": "x", "rating": 4}]}}
    assert bd._scale_divisor(r5) == 1.0


def test_a_weird_ratio_is_not_treated_as_a_scale_signal():
    """Only a clean 1x or 2x is a scale. Anything else means the field means something
    else, and guessing a divisor from it would corrupt real scores."""
    r = {"public": {"rating": 3, "rating_platform_original": 4.4},
         "private": {"detailed_ratings": [{"type": "x", "rating": 4}]}}
    assert bd._scale_divisor(r) == 1.0, "an unexplained ratio must fall back, not scale"


def test_an_underivable_scale_warns_loudly_instead_of_printing_a_bad_number():
    """Measured channels are airbnb (1-5) and booking (1-10). A channel on some other scale
    (say 0-100) cannot be derived, and the failure mode that shipped was a confident
    impossible number. It must announce itself now."""
    with tempfile.TemporaryDirectory() as tmp:
        revs = {"data": [{"platform": "mystery", "reviewed_at": "2026-09-01",
                          "responded_at": None,
                          "public": {"rating": 5, "rating_platform_original": 88,
                                     "review": "x"},
                          "private": {"detailed_ratings": [
                              {"type": "cleanliness", "rating": 88}]}}]}
        d = _wd(tmp, **{"subject.json": SUBJECT, "reviews.json": revs})
        text = bd.build(d)
        assert "SCALE WARNING" in text, "an impossible average was printed without a warning"
        assert "DO NOT cite any category average" in text


def test_clean_data_carries_no_scale_warning():
    with tempfile.TemporaryDirectory() as tmp:
        revs = {"data": [_review("airbnb", 5, 5, {"cleanliness": 5}),
                         _review("booking", 10, 5, {"cleanliness": 10})]}
        d = _wd(tmp, **{"subject.json": SUBJECT, "reviews.json": revs})
        assert "SCALE WARNING" not in bd.build(d)


def test_airbnb_zero_filled_categories_are_dropped():
    """Hospitable emits all 9 category keys on EVERY review and zero-fills the ones the
    channel doesn't use. Verified across 276 reviews: Airbnb sends staff/facilities/services
    as 0, which is what put staff at 1.38 and services at 0.0 in a shipped report."""
    with tempfile.TemporaryDirectory() as tmp:
        revs = {"data": [_review("airbnb", 5, 5, {
            "cleanliness": 5, "checkin": 5, "accuracy": 5, "communication": 5,
            "location": 5, "value": 5, "staff": 0, "facilities": 0, "services": 0})]}
        d = _wd(tmp, **{"subject.json": SUBJECT, "reviews.json": revs})
        line = next(line for line in bd.build(d).split("\n") if line.startswith("category_avgs_0to5"))
        for absent in ("staff", "facilities", "services"):
            assert absent not in line, f"{absent} was zero-filled by the channel, not rated"
        assert '"cleanliness": {"avg": 5.0, "n": 1}' in line


def test_no_category_average_can_exceed_five():
    """The invariant that was broken in production, asserted directly."""
    import json as _json
    with tempfile.TemporaryDirectory() as tmp:
        revs = {"data": [_review("booking", 10, 5, dict.fromkeys(("cleanliness", "location", "staff", "facilities", "value"), 10))]}
        d = _wd(tmp, **{"subject.json": SUBJECT, "reviews.json": revs})
        line = next(line for line in bd.build(d).split("\n") if line.startswith("category_avgs_0to5"))
        blob = _json.loads(line.split(": ", 1)[1])
        assert all(0 < v["avg"] <= 5 for v in blob.values()), f"out-of-range average: {blob}"


def test_review_silent_channels_are_named_in_the_digest():
    """VRBO is CONNECTED on this account (Hospitable calls it `homeaway`: 35 reservations,
    plus 4 vrbo.com iCal feeds) yet delivers zero reviews — iCal cannot by protocol, and the
    homeaway API measurably does not. The report must not imply its guest language covers
    every channel."""
    with tempfile.TemporaryDirectory() as tmp:
        chans = {"connected_platforms": ["airbnb", "booking", "homeaway", "ical"],
                 "silent_channels": ["homeaway", "ical"]}
        d = _wd(tmp, **{"subject.json": SUBJECT, "reviews.json": _reviews(5),
                        "channels.json": chans})
        text = bd.build(d)
        assert "CHANNELS WITH NO REVIEW DATA: ['homeaway', 'ical']" in text
        assert "NOT the whole picture" in text


def test_a_silent_channel_that_did_report_is_not_flagged():
    """Only flag channels genuinely absent from the pulled reviews — if homeaway ever starts
    delivering, the warning must disappear on its own rather than lie in the other direction."""
    with tempfile.TemporaryDirectory() as tmp:
        revs = {"data": [_review("homeaway", 5, 5, {"cleanliness": 5})]}
        chans = {"connected_platforms": ["homeaway"], "silent_channels": ["homeaway"]}
        d = _wd(tmp, **{"subject.json": SUBJECT, "reviews.json": revs,
                        "channels.json": chans})
        text = bd.build(d)
        assert "CHANNELS WITH NO REVIEW DATA" not in text, \
            "homeaway reported reviews here, so it must not be called silent"


def test_no_channels_file_means_no_claim_either_way():
    with tempfile.TemporaryDirectory() as tmp:
        d = _wd(tmp, **{"subject.json": SUBJECT, "reviews.json": _reviews(3)})
        assert "CHANNELS WITH NO REVIEW DATA" not in bd.build(d)


def test_missing_files_degrade_gracefully():
    """No funnel, no occupancy, no comps — the digest still builds and the run continues."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _wd(tmp, **{"subject.json": SUBJECT})
        text = bd.build(d)
        assert "# SUBJECT" in text and "Cozy Cabin" in text
        assert "# FUNNEL" not in text and "# OCCUPANCY" not in text


def test_corrupt_file_does_not_abort_the_digest():
    with tempfile.TemporaryDirectory() as tmp:
        d = _wd(tmp, **{"subject.json": SUBJECT})
        (d / "comps.json").write_text("{ broken")
        text = bd.build(d)  # must not raise
        assert "# SUBJECT" in text


def test_prior_runs_reach_the_digest_for_the_trend_line():
    with tempfile.TemporaryDirectory() as tmp:
        d = _wd(tmp, **{"subject.json": SUBJECT,
                        "prior_runs.json": [{"run_date": "2026-08-05", "ale_total": 3.3}]})
        text = bd.build(d)
        assert "# PRIOR RUNS" in text and "2026-08-05" in text


def test_comps_fetch_cost_is_visible():
    """So the run can report the true paid-call count instead of assuming."""
    with tempfile.TemporaryDirectory() as tmp:
        comps = {"comp_count": 25, "fetch": {"calls": 0, "path": "cache"},
                 "top_comps": [], "market_amenity_frequency": [], "comp_title_samples": []}
        d = _wd(tmp, **{"subject.json": SUBJECT, "comps.json": comps})
        text = bd.build(d)
        assert "source: cache (0 paid call(s))" in text


def test_digest_stays_far_smaller_than_the_raw_inputs():
    """The whole point. Guard the ratio against accidental un-capping."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _wd(tmp, **{"subject.json": SUBJECT, "reviews.json": _reviews(150)})
        raw = (d / "reviews.json").stat().st_size
        text = bd.build(d, review_cap=20)
        assert len(text) < raw / 3, f"digest {len(text)}B vs raw {raw}B — cap is leaking"


def test_sub_band_warning_travels_with_the_scores():
    """The model reads ONLY the digest. A noise warning that lives just in photo_scores.json
    is invisible to it, and it will read avg 4.0 vs 3.83 as a real difference when measured
    drift is 0.21 mean / 0.66 max. Regression: a SKILL.md rewrite dropped this guidance and
    the digest did not carry it, so it vanished entirely from the run."""
    with tempfile.TemporaryDirectory() as tmp:
        ps = {"hero": 3, "recommended_top5_order": [3], "top5_beats": ["hot_tub"],
              "score_band": 0.5, "distinct_beats": ["hot_tub"], "gaps": [],
              "reshoot": [], "restage": [], "coverage_note": "ranked 1 of 1 photos (complete)",
              "photos": [{"order": 3, "avg": 4.0, "subject_kind": "hot_tub",
                          "subject": "hot tub", "scored": True, "flags": []}]}
        d = _wd(tmp, **{"subject.json": SUBJECT, "photo_scores.json": ps})
        text = bd.build(d)
        assert "score_band: 0.5" in text
        assert "NOT a real difference" in text, "the sub-band warning is missing from the digest"
        assert "sub-band" in text


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("✅ digest tests passed")


def test_every_prior_run_reaches_the_digest_intact(tmp_path):
    """Raw JSON used to be cut at 1,500 chars, hiding every run after the first."""
    long_gaps = ["x" * 300] * 4
    runs = [{"run_date": f"2026-0{m}-01", "season": "Fall", "applied": False, "ale_total": 2.5,
             "title": f"Title {m}", "ale_scores": [{"dimension": "A", "score": 3}],
             "amenity_gaps": long_gaps} for m in (8, 7, 6)]
    (tmp_path / "subject.json").write_text(json.dumps({"data": {"name": "x"}}))
    (tmp_path / "prior_runs.json").write_text(json.dumps(runs))
    out = bd.build(tmp_path)
    for m in (8, 7, 6):
        assert f'title="Title {m}"' in out, f"prior run {m} missing from digest"


def test_amenity_gaps_are_computed_not_left_to_the_model(tmp_path):
    """olde-town-ambler 2026-09-24: the top-30 frequency line was all 92-100% basics, so
    workspace (79%) and fire pit (67%) never reached the model. The digest now carries the
    computed diff, matched across PMS keys and AirROI labels, with house-rule pets."""
    subject = {"data": {"name": "x", "public_name": "Walk to Olde Town", "summary": "Dogs welcome.",
                        "amenities": ["bed_linens", "smoke_detector", "jacuzzi"],
                        "house_rules": {"pets_allowed": True}}}
    comps = {"comp_count": 3, "fetch": {"calls": 0, "path": "cache"},
             "top_comps": [{"name": "A", "amenities": ["Fire pit", "Dedicated workspace"]}],
             "market_amenity_frequency": [
                 {"amenity": "Bed linens", "pct": 100}, {"amenity": "Smoke alarm", "pct": 100},
                 {"amenity": "Dedicated workspace", "pct": 79}, {"amenity": "Fire pit", "pct": 67},
                 {"amenity": "Pets allowed", "pct": 54}, {"amenity": "Hot tub", "pct": 20}],
             "comp_title_samples": []}
    (tmp_path / "subject.json").write_text(json.dumps(subject))
    (tmp_path / "comps.json").write_text(json.dumps(comps))
    out = bd.build(tmp_path)
    assert "Dedicated workspace 79% (1/1 top)" in out
    assert "Fire pit 67% (1/1 top)" in out
    missing_line = next(line for line in out.splitlines() if line.startswith("missing_from_pms"))
    assert "Pets allowed" not in missing_line and "Bed linens" not in missing_line
    assert "Hot tub 20%" in out, "differentiator absent from the title/summary not flagged"
    assert "market_amenity_freq(top30)" not in out


def test_review_complaint_at_the_end_survives_and_private_feedback_is_carried(tmp_path):
    """olde-town-ambler 2026-09-24: the 320-char cut ended a real review mid-complaint.
    Complaints sit at the end. Private feedback (7 of the last 20 reviews) was never shown.
    Fixture text is synthetic: guest reviews and private feedback never go in the repo."""
    body = ("Lovely home in a great spot, and the kitchen had everything we needed for the "
            "week. " * 4) + "Only downside was the upstairs bedroom running warm at night."
    reviews = {"data": [{"reviewed_at": "2026-08-02", "platform": "airbnb",
                         "public": {"rating": 5, "review": body},
                         "private": {"feedback": "The hallway smoke alarm chirped once overnight.",
                                     "detailed_ratings": []}},
                        {"reviewed_at": "2026-08-01", "platform": "airbnb",
                         "public": {"rating": 5, "review": "Great."},
                         "private": {"feedback": None, "detailed_ratings": []}}]}
    (tmp_path / "subject.json").write_text(json.dumps({"data": {"name": "x"}}))
    (tmp_path / "reviews.json").write_text(json.dumps(reviews))
    out = bd.build(tmp_path)
    assert "running warm at night." in out, "the complaint at the end was cut"
    assert "smoke alarm chirped once overnight" in out
    assert "never quote" in out.lower(), "private feedback must carry its no-quote rule"


def test_nothing_is_sliced_mid_record(tmp_path):
    """Four spots cut serialized JSON at a fixed length. A 365-day calendar puts twelve
    months in occupancy.json and the 1,500-char cut dropped the later ones; ten long comp
    titles overflowed the 900-char cut. Same bug class as the prior-runs fix in 70d5451."""
    months = {f"2027-{m:02d}": {"occupancy_pct": 50.0, "booked": 15, "available": 15,
                                 "blocked": 0, "unknown": 0} for m in range(1, 13)}
    occ = {"source": "Hospitable", "monthly": months,
           "forward_window": {"occupancy_pct": 50.0, "booked": 180, "available": 180,
                              "blocked": 5, "days": 365, "unknown": 0},
           "reason_counts": {"RESERVED": 180}, "upcoming_reservations": 30,
           "report_block": {"forward_pct": 50.0}}
    titles = [f"Comp {i} " + "x" * 90 for i in range(10)]
    comps = {"comp_count": 10, "fetch": {"calls": 0, "path": "cache"}, "top_comps": [],
             "market_amenity_frequency": [], "comp_title_samples": titles}
    (tmp_path / "subject.json").write_text(json.dumps(SUBJECT))
    (tmp_path / "occupancy.json").write_text(json.dumps(occ))
    (tmp_path / "comps.json").write_text(json.dumps(comps))
    out = bd.build(tmp_path)
    assert "2027-12" in out, "later months were cut off"
    assert "Comp 9 " in out, "later comp titles were cut off"
    assert out.count("report_block") == 0, "occupancy printed its duplicate report block"


def test_unanswered_reviews_separate_the_ones_that_can_still_be_answered(tmp_path):
    """boho-bliss 2026-09-26: 3 reviews had no response, but the Booking.com and direct ones
    had can_respond=false. Telling a host to reply to 3 sends them after 2 impossible replies."""
    rv = {"data": [
        {"reviewed_at": "2026-09-23", "platform": "airbnb", "responded_at": None, "can_respond": True,
         "public": {"rating": 5, "review": "a"}},
        {"reviewed_at": "2026-07-10", "platform": "booking", "responded_at": None, "can_respond": False,
         "public": {"rating": 4.5, "review": ""}},
        {"reviewed_at": "2026-06-22", "platform": "direct", "responded_at": None, "can_respond": False,
         "public": {"rating": 5, "review": "b"}},
        {"reviewed_at": "2026-06-01", "platform": "airbnb", "responded_at": "2026-06-02", "can_respond": False,
         "public": {"rating": 5, "review": "c"}}]}
    (tmp_path / "subject.json").write_text(json.dumps({"data": {"name": "x"}}))
    (tmp_path / "reviews.json").write_text(json.dumps(rv))
    out = bd.build(tmp_path)
    line = next(x for x in out.splitlines() if x.startswith("unanswered_reviews"))
    assert ": 3" in line and "1 can still be answered" in line, line
