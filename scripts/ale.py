"""ale.py — the seven ALE scorecard dimensions, one spelling each.

History held "A — Amenities surfaced", "A: Amenities surfaced" and "A - Amenities
surfaced" for the same dimension, so per-dimension trends could not line up across runs.
"""
from __future__ import annotations

DIMENSIONS = [
    "A: Amenities surfaced", "L: Location specifics", "E: Experiences staged",
    "Photos channel", "Copy channel", "Captions channel", "Reviews channel",
]
# Checked in this order; the first keyword found in the lowercased name wins.
_KEYWORDS = (("amenit", 0), ("location", 1), ("experience", 2), ("caption", 5),
             ("photo", 3), ("copy", 4), ("review", 6))


def canonical_dimension(name) -> str:
    """The canonical spelling, or the input unchanged when it matches no dimension."""
    low = str(name or "").lower()
    for word, idx in _KEYWORDS:
        if word in low:
            return DIMENSIONS[idx]
    return str(name or "")
