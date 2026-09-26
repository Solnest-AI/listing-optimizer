#!/usr/bin/env python3
"""Tests for the photo cover-set ranking — the highest-leverage output of the tool.

Every case here is a REAL failure taken from a shipped run in output/, not a synthetic
scenario. If one of these regresses, the tool starts recommending duplicate cover photos
again, which is exactly what it did before 2026-09-20.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import analyze_photos as ap


def _p(order, avg, kind, people=False, is_map=False, flags=None):
    """Minimal scored-photo record. `avg` is what the model returned."""
    return {"order": order, "avg": avg, "subject_kind": kind, "subject": kind,
            "has_people": people, "is_map": is_map, "flags": flags or [],
            "scored": True, "url": f"https://x/{order}.jpg"}


def test_no_duplicate_beats_in_top5():
    """apres-arcade 2026-06-08 shipped a top5 of fireplace / arcade_room / hot_tub /
    hot_tub / arcade — the hot tub AND the arcade twice in a 5-photo cover set."""
    photos = [
        _p(3, 4.67, "food_drink_staging"), _p(13, 4.67, "game_room_arcade"),
        _p(0, 4.5, "hot_tub"), _p(4, 4.5, "hot_tub"), _p(6, 4.5, "game_room_arcade"),
        _p(7, 4.5, "kitchen_dining"), _p(10, 4.33, "living_room"),
        _p(11, 4.0, "primary_bedroom"), _p(12, 3.5, "bathroom"),
    ]
    r = ap.aggregate(photos)
    beats = r["top5_beats"]
    assert len(beats) == len(set(beats)), f"duplicate beat in cover set: {beats}"
    assert len(r["recommended_top5_order"]) == 5


def test_free_text_labels_still_dedupe_via_fallback():
    """A cached score written before subject_kind existed has only free text. It must
    still dedupe on that rather than crash or silently allow duplicates."""
    photos = [{"order": i, "avg": a, "subject": s, "has_people": False, "is_map": False,
               "flags": [], "scored": True, "url": f"https://x/{i}.jpg"}
              for i, (a, s) in enumerate([(5.0, "Hot Tub"), (4.9, "hot tub"),
                                          (4.8, "kitchen"), (4.7, "bedroom"),
                                          (4.6, "bathroom"), (4.5, "exterior")])]
    r = ap.aggregate(photos)
    assert len(r["top5_beats"]) == len(set(r["top5_beats"])), "case-folded fallback failed"


def test_top5_order_is_non_increasing_after_hero():
    """boho-bliss 2026-06-08 shipped top5 avgs 5.0, 4.17, 3.67, 2.67, 3.5 — a 2.67 photo
    published ahead of a 3.5 one, because the people-swap appended at the end."""
    photos = [_p(10, 5.0, "fire_pit"), _p(0, 4.17, "living_room"),
              _p(1, 3.67, "kitchen_dining"), _p(11, 2.67, "bedroom"),
              _p(15, 3.5, "kids_pet_family", people=True), _p(14, 4.0, "bathroom")]
    r = ap.aggregate(photos)
    top5 = r["recommended_top5_order"]
    by = {p["order"]: p for p in photos}
    bands = [ap._band(by[o]["avg"]) for o in top5]
    assert top5[0] == r["hero"], "hero must be slot 1"
    assert bands[1:] == sorted(bands[1:], reverse=True), f"slots 2-5 out of rank: {bands}"


def test_people_shot_forced_into_top5():
    photos = [_p(0, 5.0, "hot_tub"), _p(1, 4.8, "kitchen_dining"),
              _p(2, 4.6, "living_room"), _p(3, 4.4, "bedroom"),
              _p(4, 4.2, "bathroom"), _p(5, 2.0, "fire_pit", people=True)]
    r = ap.aggregate(photos)
    assert 5 in r["recommended_top5_order"], "a people shot must make the top 5"
    assert r["people_swap"] is not None
    assert not any("No person in the top 5" in g for g in r["gaps"])


def test_map_never_becomes_hero():
    photos = [_p(0, 5.0, "location_map", is_map=True), _p(1, 4.0, "hot_tub"),
              _p(2, 3.0, "kitchen_dining")]
    r = ap.aggregate(photos)
    assert r["hero"] == 1, "a map may sit in the top 10 but must never be the cover"


def test_banding_absorbs_measured_model_noise():
    """Measured drift on identical input: mean 0.21, max 0.66. Two photos whose scores
    differ by less than one band must not swap places between runs."""
    run1 = [_p(0, 3.83, "living_room"), _p(1, 4.0, "kitchen_dining")]
    run2 = [_p(0, 4.0, "living_room"), _p(1, 3.83, "kitchen_dining")]  # drift flips raw order
    assert ap.aggregate(run1)["hero"] == ap.aggregate(run2)["hero"], \
        "a sub-band drift must not move the hero"


def test_thin_gallery_flags_missing_beats():
    """Three shots of one living room is ONE beat, not three. The old free-text dedupe
    hid that; the report must now say the top 5 cannot show 5 different things."""
    photos = [_p(0, 4.0, "living_room"), _p(1, 3.9, "living_room"),
              _p(2, 3.8, "living_room"), _p(3, 3.7, "kitchen_dining")]
    r = ap.aggregate(photos)
    assert r["distinct_beats"] == ["kitchen_dining", "living_room"]
    assert any("distinct photo beats" in g for g in r["gaps"]), "missing-beats gap not raised"


def test_failed_photos_are_reported_not_swallowed():
    """sunburst-chalet scored 29 then 27 on the same gallery. A photo that fails is absent
    from the ranking, so the run must SAY the ranking is incomplete."""
    photos = [_p(0, 5.0, "hot_tub"), _p(1, 4.0, "kitchen_dining"),
              {"order": 2, "scored": False, "error": "gemini 429: rate limited",
               "url": "https://x/2.jpg"}]
    r = ap.aggregate(photos)
    assert r["scored_count"] == 2 and r["submitted_count"] == 3
    assert r["failed"] == [{"order": 2, "error": "gemini 429: rate limited"}]
    assert "FAILED" in r["coverage_note"], f"incomplete ranking not surfaced: {r['coverage_note']}"


def test_all_enum_kinds_are_distinct_beats():
    """The enum is the dedupe key, so a duplicate or empty member would silently merge
    two different beats into one."""
    assert len(ap.SUBJECT_KINDS) == len(set(ap.SUBJECT_KINDS))
    assert all(k and k == k.strip().lower() for k in ap.SUBJECT_KINDS)
    assert ap._SCHEMA["properties"]["subject_kind"]["enum"] == ap.SUBJECT_KINDS
    assert "subject_kind" in ap._SCHEMA["required"]


def test_empty_input_does_not_crash():
    r = ap.aggregate([])
    assert r["hero"] is None and r["recommended_top5_order"] == []



def _s(order, avg, kind, ale=4, emo=4, flags=None):
    return {**_p(order, avg, kind, flags=flags), "ale_fit": ale, "emotion": emo}


def test_collage_and_non_property_shots_never_become_the_cover():
    """olde-town-ambler 2026-09-24: the machine cover was #0, a collage captioned with a
    filename. The agent had to override it by hand. A street scene is not the property
    either, and a bathroom or close-up detail is not a search thumbnail."""
    photos = [_s(0, 4.0, "collage_multi"), _s(22, 4.17, "neighbourhood_area", ale=5),
              _s(6, 4.0, "bathroom"), _s(14, 4.0, "amenity_detail"), _s(1, 4.0, "living_room")]
    r = ap.aggregate(photos)
    assert r["hero"] == 1, f"hero {r['hero']} is not the property"
    assert 0 not in r["recommended_top5_order"], "a collage took a cover-set slot"
    assert any("#0" in g and "collage_multi" in g for g in r["gaps"]), "current cover problem not named"


def test_reshoot_flagged_photos_stay_out_of_the_cover_set():
    photos = [_s(0, 5.0, "hot_tub", flags=["reshoot"]), _s(1, 4.0, "living_room"),
              _s(2, 3.5, "kitchen_dining")]
    r = ap.aggregate(photos)
    assert r["hero"] == 1 and 0 not in r["recommended_top5_order"]


def test_ties_go_to_the_photo_that_sells_not_the_current_gallery_slot():
    """olde-town-ambler: seven photos tied at band 4.0 and gallery order broke the tie, so
    the recommendation repeated the current order and #22 (Olde Town, ale_fit 5) lost a
    cover slot to a bathroom (ale_fit 4)."""
    photos = [_s(1, 4.0, "living_room"), _s(6, 4.0, "bathroom"), _s(7, 4.0, "deck_patio_yard"),
              _s(8, 4.0, "bedroom"), _s(9, 4.0, "exterior"),
              _s(22, 4.17, "neighbourhood_area", ale=5, emo=4)]
    r = ap.aggregate(photos)
    top5 = r["recommended_top5_order"]
    assert 22 in top5, f"the strongest selling shot lost its slot: {top5}"
    assert top5.index(22) == 1, f"#22 should lead the non-hero slots: {top5}"



def test_off_property_scenery_takes_at_most_one_cover_slot_and_a_bedroom_gets_one():
    """boho-bliss 2026-09-26 (live Airbnb gallery): the top 5 held a skier at Powder King (#36)
    AND a snowcat at sunset (#47), both off the property, and no bedroom, on a listing where
    4.69% of visitors book vs 34% for similar listings. The rubric's cover set is hero
    amenity, experience with people, key living space, bedroom, view/location."""
    photos = [_s(45, 5.0, "fire_pit", ale=5, emo=5), _s(47, 4.67, "view_scenery", ale=5, emo=5),
              _s(11, 4.67, "fire_pit"), _s(36, 4.33, "neighbourhood_area", ale=5, emo=5),
              _s(31, 4.17, "neighbourhood_area"), _s(8, 4.0, "kitchen_dining"),
              _s(29, 4.0, "exterior"), _s(1, 3.83, "living_room"), _s(12, 3.17, "bedroom"),
              _s(17, 2.0, "bedroom", flags=["reshoot"])]
    photos[0]["has_people"] = photos[3]["has_people"] = True
    r = ap.aggregate(photos)
    beats = r["top5_beats"]
    assert sum(b in ("view_scenery", "neighbourhood_area") for b in beats) <= 1, beats
    assert 12 in r["recommended_top5_order"], f"no bedroom in the cover set: {r['recommended_top5_order']}"
    assert 17 not in r["recommended_top5_order"], "a reshoot-flagged bedroom took the slot"
    assert r["hero"] == 45


def test_bedroom_rule_never_evicts_the_only_people_shot():
    photos = [_s(0, 5.0, "hot_tub"), _s(1, 4.5, "living_room"), _s(2, 4.5, "kitchen_dining"),
              _s(3, 4.0, "exterior"), _s(4, 3.5, "deck_patio_yard"), _s(5, 3.0, "bedroom")]
    photos[4]["has_people"] = True
    r = ap.aggregate(photos)
    assert 4 in r["recommended_top5_order"] and 5 in r["recommended_top5_order"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("✅ photo ranking tests passed")
