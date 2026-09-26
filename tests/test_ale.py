#!/usr/bin/env python3
"""The ALE scorecard's seven dimensions must be the same seven strings every run.

state/history.jsonl holds three spellings of one dimension ("A — Amenities surfaced",
"A: Amenities surfaced", "A - Amenities surfaced"), so a per-dimension trend across runs
could not line up. The renderer now canonicalizes and requires all seven exactly once.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ale
import render_report as rr


def _card(names):
    return [{"dimension": n, "score": 3, "gap": "g", "fix": "f"} for n in names]


def _result(card):
    return {"listing": {"name": "x", "slug": "x"}, "run_date": "2026-09-25",
            "optimized": {"title": "t", "summary": "s", "the_space": "sp", "captions": []},
            "ale_scorecard": card, "ale_total": 9.9}


@pytest.mark.parametrize("spelling", ["A — Amenities surfaced", "A: Amenities surfaced",
                                      "A - Amenities surfaced", "Amenities"])
def test_every_seen_spelling_maps_to_one_name(spelling):
    assert ale.canonical_dimension(spelling) == "A: Amenities surfaced"


def test_renderer_canonicalizes_and_recomputes_the_total():
    data = _result(_card(["A — Amenities surfaced", "L - Location specifics", "E: Experiences",
                          "Photos channel", "Copy channel", "Captions channel", "Reviews channel"]))
    rr.validate_result(data)
    assert [r["dimension"] for r in data["ale_scorecard"]] == ale.DIMENSIONS
    assert data["ale_total"] == 3.0, "ale_total must be the mean of the scores, not a retyped number"


@pytest.mark.parametrize("names", [ale.DIMENSIONS[:6], ale.DIMENSIONS + ["Copy channel"],
                                   ale.DIMENSIONS[:6] + ["Vibes"]])
def test_incomplete_or_duplicate_scorecards_are_rejected(names):
    with pytest.raises(ValueError, match="scorecard"):
        rr.validate_result(_result(_card(names)))


def test_scores_must_be_0_to_5():
    card = _card(ale.DIMENSIONS)
    card[0]["score"] = 7
    with pytest.raises(ValueError, match="0..5"):
        rr.validate_result(_result(card))
