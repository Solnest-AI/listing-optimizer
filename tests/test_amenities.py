#!/usr/bin/env python3
"""Tests for amenities.py — the deterministic subject-vs-comps amenity diff.

Before this existed the digest printed the 30 most common comp amenities, which in a real
market are all 92-100% basics (linens, smoke alarm), and left the model to match the
subject's snake_case PMS keys against AirROI display names by eye. Two runs of the same
listing (olde-town-ambler 2026-07-07 vs 2026-08-18) contradicted each other on pets.
Every name below is taken from real subject.json / comps.json payloads.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import amenities as am


def _freq(pairs):
    return [{"amenity": a, "count": p, "pct": p} for a, p in pairs]


def test_pms_keys_match_airroi_display_names():
    """ac / carbon_monoxide_detector / travel_crib are the SAME amenities as AirROI's
    'Air conditioning' / 'Carbon monoxide alarm' / 'Pack ’n play/Travel crib'."""
    subject = ["ac", "carbon_monoxide_detector", "travel_crib", "laptop_friendly_workspace",
               "free_on_premise_parking", "bbq", "jacuzzi", "patio", "books"]
    have = am.subject_amenity_set(subject, {})
    for label in ("Air conditioning", "Carbon monoxide alarm", "Pack ’n play/Travel crib",
                  "Dedicated workspace", "Free parking on premises", "BBQ grill", "Hot tub",
                  "Patio or balcony", "Books and reading material"):
        assert am.norm(label) in have, f"{label} not matched to a PMS key"


def test_house_rules_count_as_amenities():
    """Pets live in house_rules, not the amenity list. The July run said 'you don't allow
    pets' for a listing that does, because only the amenity list was compared."""
    have = am.subject_amenity_set([], {"pets_allowed": True, "smoking_allowed": False})
    assert am.norm("Pets allowed") in have
    assert am.norm("Smoking allowed") not in have


def test_gaps_skip_basics_the_subject_has_and_surface_differentiators():
    freq = _freq([("Bed linens", 100), ("Smoke alarm", 100), ("Dedicated workspace", 79),
                  ("Fire pit", 67), ("Pets allowed", 54), ("Sauna", 12)])
    top = [{"amenities": ["Fire pit", "Dedicated workspace"]}, {"amenities": ["Fire pit"]},
           {"amenities": ["Bed linens"]}]
    r = am.compare(["bed_linens", "smoke_detector"], {"pets_allowed": True}, freq, top,
                   copy_text="")
    missing = [g["amenity"] for g in r["missing"]]
    assert missing == ["Dedicated workspace", "Fire pit"], missing
    fire = next(g for g in r["missing"] if g["amenity"] == "Fire pit")
    assert (fire["pct"], fire["top_hits"], fire["top_n"]) == (67, 2, 3)


def test_rare_market_amenities_are_not_called_gaps():
    """12% of comps is not a market expectation."""
    r = am.compare([], {}, _freq([("Sauna", 12)]), [], copy_text="")
    assert r["missing"] == []


def test_differentiators_absent_from_copy_are_flagged():
    """The subject HAS a hot tub that only 20% of comps have, but the title/summary never
    say so. That is a surfacing gap, not a purchase."""
    freq = _freq([("Hot tub", 20), ("Pets allowed", 40)])
    r = am.compare(["jacuzzi"], {"pets_allowed": True}, freq, [],
                   copy_text="Walk to Olde Town. Dogs welcome in the fenced yard.")
    labels = [x["amenity"] for x in r["unsurfaced"]]
    assert "Hot tub" in labels
    # 'dogs' is how a host writes pets; the synonym list keeps it from a false flag.
    assert "Pets allowed" not in labels


def test_unknown_subject_keys_are_reported_not_guessed():
    r = am.compare(["totally_new_key"], {}, _freq([("Wifi", 100)]), [], copy_text="")
    assert "totally new key" in r["unmatched_subject"]


def test_empty_subject_list_is_flagged():
    """July 2026: the PMS amenity list came back empty. Every comp amenity then looks
    'missing', which is noise, so the result must say the list was empty."""
    r = am.compare([], {}, _freq([("Wifi", 100)]), [], copy_text="")
    assert r["subject_list_empty"] is True


def test_airbnb_label_variants_match_the_market_labels():
    """Live Airbnb (RankBreeze) labels carry qualifiers and brands the AirROI market labels
    do not: seen 2026-09-26 on two real listings."""
    freq = _freq([("Dryer", 92), ("Washer", 92), ("Oven", 100), ("Backyard", 96),
                  ("Indoor fireplace", 29), ("Coffee maker", 90)])
    live = ["Free dryer – In building", "Free washer – In building", "TEKA stainless steel oven",
            "Private backyard – Fully fenced", "Indoor fireplace: wood-burning", "Coffee maker: Keurig coffee machine"]
    r = am.compare(live, {}, freq, [], copy_text="")
    assert r["missing"] == [], [g["amenity"] for g in r["missing"]]


def test_safety_and_disclosure_items_are_not_suggested_for_the_title():
    """A security camera or first aid kit is a disclosure, not a selling point."""
    freq = _freq([("Exterior security cameras on property", 46), ("First aid kit", 50),
                  ("Smoke alarm", 40), ("Fire pit", 12)])
    r = am.compare(["Exterior security cameras on property", "First aid kit", "Smoke alarm", "Fire pit"],
                   {}, freq, [], copy_text="")
    assert [x["amenity"] for x in r["unsurfaced"]] == ["Fire pit"]
