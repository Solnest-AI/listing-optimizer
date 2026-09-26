#!/usr/bin/env python3
"""Tests for photo_dupes.py — near-duplicate gallery photos.

Calibrated on olde-town-ambler 2026-09-24 (54 photos, every pair checked by eye):
17 true duplicate pairs (same shot, sometimes re-edited) and 10 "same room, now with
people" pairs. A whole-image hash alone put two people-versions (#7/#52, #42/#49) inside
the duplicate range because the people are small in a wide shot. Calling a lifestyle
shot a duplicate would tell the host to delete their best photo, so the second, blockwise
check exists and the thresholds lean toward missing a duplicate rather than that.
"""
import sys
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw, ImageEnhance  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import photo_dupes as pdup  # noqa: E402


def _room(seed=0):
    """A synthetic 'room': gradients and shapes with real structure, like a photo."""
    im = Image.new("RGB", (480, 320), (200, 190, 180))
    dr = ImageDraw.Draw(im)
    for i in range(0, 480, 40):
        dr.rectangle([i, 0, i + 20, 320], fill=(150 + (i + seed) % 90, 140, 120))
    dr.rectangle([60, 180, 300, 300], fill=(60, 60, 70))       # sofa
    dr.ellipse([340, 40, 440, 140], fill=(250, 240, 200))       # window light
    return im


def _sig(im):
    return pdup.signature(im)


def test_identical_and_re_edited_shots_are_duplicates():
    base = _room()
    brighter = ImageEnhance.Brightness(base).enhance(1.15)
    assert pdup.is_duplicate(_sig(base), _sig(base))
    assert pdup.is_duplicate(_sig(base), _sig(brighter)), "a white-balance re-edit is the same shot"


def test_same_room_with_people_is_not_a_duplicate():
    """#8 vs #50: the same kitchen, then with a couple in it. Different photo."""
    base = _room()
    with_people = base.copy()
    ImageDraw.Draw(with_people).rectangle([200, 60, 280, 300], fill=(30, 40, 90))
    assert not pdup.is_duplicate(_sig(base), _sig(with_people))


def test_different_rooms_are_not_duplicates():
    other = Image.new("RGB", (480, 320), (240, 240, 240))
    ImageDraw.Draw(other).rectangle([0, 200, 480, 320], fill=(90, 60, 30))
    assert not pdup.is_duplicate(_sig(_room()), _sig(other))


def test_pairs_are_reported_once_in_gallery_order():
    a, b, c = _room(), _room(), Image.new("RGB", (480, 320), (10, 10, 10))
    sigs = {40: _sig(b), 16: _sig(a), 3: _sig(c)}
    assert pdup.find_pairs(sigs) == [(16, 40)]


def test_duplicate_gap_names_every_pair_and_the_count():
    import analyze_photos as ap
    gap = ap.duplicate_gap([[16, 40], [1, 26]])
    assert "2 duplicate" in gap and "#40 repeats #16" in gap and "#26 repeats #1" in gap
    assert ap.duplicate_gap([]) is None
