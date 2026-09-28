#!/usr/bin/env python3
"""listing_gaps.py — problems on the LIVE listing that the report flags as gaps.

Checked in code on every run, so they never depend on the writer noticing:
- Guest access on the live listing is missing, holds only a registration number, or is one
  vague line. Only RankBreeze returns this field; without it nothing is claimed.
- A camera or noise monitor is ticked on Airbnb, but the new copy never mentions it.
  Airbnb requires the listing description to disclose them.
- The new copy names an amenity that is not ticked on Airbnb. Either tick it (search
  filters read the boxes) or the copy is wrong.

The writer adds judgement gaps in result.json `listing_gaps` (for example a deal-breaker
guests mention in reviews that the listing never discloses). Both render together.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import amenities
import artifacts

VAGUE_GUEST_ACCESS = 80   # chars; top performers are short, but one line this short says nothing

# RankBreeze fills an empty Guest access with the listing's registration numbers (seen on 15
# BC listings, 2026-09-26). The word list covers the usual permit wording elsewhere too
# (TOT certificates, Hawaii TMK/GE/TA).
_REGISTRATION_TEXT = re.compile(
    r"(?i)\b(?:municipal|provincial|city|county|state|business|str|short|term|vacation|rental|"
    r"lodging|transient|occupancy|operator|zoning|registration|reg|licen[cs]e|permit|certificate|"
    r"cert|number|no|num|host|tax|id|tot|trn|tmk|ge|ta)\b|\S*\d\S*")


def registration_only(text: str) -> bool:
    """Only permit wording and numbers. A short line with no number ("All space") is vague
    copy, not a registration number."""
    return bool(re.search(r"\d", text)) and len(re.sub(r"[^A-Za-z]", "", _REGISTRATION_TEXT.sub(" ", text))) < 12


# Ticked on Airbnb -> must be disclosed (help article 3061). Airbnb's Safety devices field may
# already describe them, but no source we read returns it, so the gap asks the host to check.
DISCLOSURES = {
    "exterior security cameras on property": ("Exterior security cameras", ("camera",),
                                              "say where they are and what they cover"),
    "noise decibel monitors on property": ("Noise monitors", ("noise monitor", "decibel", "noise sensor"),
                                           "say the home has them"),
}

# Amenities guests filter by, as copy names them. canon label -> (Airbnb label, phrases).
CLAIMS = {
    "hot tub": ("Hot tub", ("hot tub", "hottub", "jacuzzi")),
    "pool": ("Pool", ("pool",)),
    "sauna": ("Sauna", ("sauna",)),
    "gym": ("Gym", ("gym",)),
    "ev charger": ("EV charger", ("ev charger", "ev charging", "electric vehicle charger")),
    "air conditioning": ("Air conditioning", ("air conditioning", "air conditioner", "a c", "central air")),
    "washer": ("Washer", ("washer", "washing machine")),
    "dryer": ("Dryer", ("dryer",)),
    "dishwasher": ("Dishwasher", ("dishwasher",)),
    "dedicated workspace": ("Dedicated workspace", ("workspace", "desk")),
    "bbq grill": ("BBQ grill", ("bbq", "barbecue grill", "grill")),
    "fire pit": ("Fire pit", ("fire pit", "firepit")),
    "indoor fireplace": ("Indoor fireplace", ("fireplace",)),
    "crib": ("Crib", ("crib",)),
    "high chair": ("High chair", ("high chair", "highchair")),
    "pets allowed": ("Pets allowed", ("pet friendly", "pets welcome", "pets allowed", "dog friendly",
                                      "dogs welcome", "dog welcome", "dogs are welcome")),
    "self check in": ("Self check-in", ("self check in", "self checkin")),
    "smart lock": ("Smart lock", ("smart lock",)),
    "keypad": ("Keypad", ("keypad",)),
    "lockbox": ("Lockbox", ("lockbox", "lock box")),
    "lake access": ("Lake access", ("lake access",)),
    "beach access": ("Beach access", ("beach access",)),
    "ski in ski out": ("Ski-in/Ski-out", ("ski in ski out", "ski in out")),
}
# A phrase that names something else: "pool table", "hair dryer", "outdoor fireplace".
NOT_THE_AMENITY = {"pool": re.compile(r"\bpool (?:table|cue|hall)|\bcar ?pool|\b(?:community|public|town|city) pool"),
                   "gym": re.compile(r"\b(?:community|public|nearby|local) gym"),
                   "dryer": re.compile(r"\bhair dryer"),
                   "indoor fireplace": re.compile(r"\boutdoor fireplace")}
NEGATION = {"no", "not", "without", "isnt", "dont", "doesnt", "never", "nor", "arent"}

# Amenity claims are read from the copy about the home itself. Neighborhood, Getting around
# and captions name places off the property ("the community pool"), so only the disclosure
# check reads them.
PROPERTY_FIELDS = ("title", "summary", "the_space", "guest_access", "other_notes")
OFF_PROPERTY_FIELDS = ("neighborhood", "getting_around")


def _copy_norm(optimized: dict, everything: bool) -> str:
    parts = [str(optimized.get(k) or "") for k in PROPERTY_FIELDS]
    if everything:
        parts += [str(optimized.get(k) or "") for k in OFF_PROPERTY_FIELDS]
        parts += [str(c.get("caption") or "") for c in optimized.get("captions") or [] if isinstance(c, dict)]
    return " ".join(amenities.norm(p) for p in parts)


def _mentions(copy_norm: str, key: str, phrases) -> bool:
    """A phrase in the copy, not negated ("no hot tub") and not naming something else."""
    stripped = NOT_THE_AMENITY[key].sub(" ", copy_norm) if key in NOT_THE_AMENITY else copy_norm
    for phrase in phrases:
        for m in re.finditer(rf"\b{re.escape(amenities.norm(phrase))}\b", stripped):
            before = stripped[:m.start()].split()[-3:]
            if not NEGATION & set(before):
                return True
    return False


def _ticked(key: str, have: set[str]) -> bool:
    """Airbnb qualifies labels past what amenities.canon strips ("Shared gym in building",
    "Private outdoor pool"), so a label naming the amenity as a whole word counts."""
    other = NOT_THE_AMENITY.get(key)
    return key in have or any(re.search(rf"\b{re.escape(key)}\b", h) and not (other and other.search(h))
                              for h in have)


def _live_listing(workdir: Path) -> dict | None:
    src = workdir / "live_gallery.json"
    if not src.exists() or artifacts.excluded(workdir, "live_gallery.json"):
        return None
    listing = json.loads(src.read_text(encoding="utf-8-sig")).get("listing")
    return listing if isinstance(listing, dict) else None


def detect(workdir: Path, optimized: dict) -> list[dict]:
    listing = _live_listing(workdir)
    if listing is None:
        return []
    gaps = []
    if "guest_access" in listing:
        access = " ".join(str(listing.get("guest_access") or "").split())
        if not access or registration_only(access):
            gaps.append({"issue": "The live listing has no Guest access section"
                                  + (" (the field holds only a registration number)." if access else "."),
                         "fix": "Paste the new Guest access copy: what's private, how guests get in, "
                                "where to park."})
        elif len(access) < VAGUE_GUEST_ACCESS:
            gaps.append({"issue": f"The live Guest access is one short line ({len(access)} characters) "
                                  f"that doesn't say how guests get in or where to park.",
                         "fix": "Replace it with the new Guest access copy."})
    ticked = listing.get("amenities")
    if not isinstance(ticked, list):
        return gaps
    have = {amenities.canon(a) for a in ticked}
    all_copy, home_copy = _copy_norm(optimized, True), _copy_norm(optimized, False)
    for key, (label, phrases, what) in DISCLOSURES.items():
        if key in have and not _mentions(all_copy, key, phrases):
            gaps.append({"issue": f"{label} are ticked on Airbnb, but the new copy never mentions them.",
                         "fix": f"Airbnb requires disclosing them. Check each one's description under "
                                f"Safety devices, and {what} in Other things to note so guests read it "
                                f"before booking."})
    for key, (label, phrases) in CLAIMS.items():
        if not _ticked(key, have) and _mentions(home_copy, key, phrases):
            gaps.append({"issue": f"The copy mentions {label.lower()}, but {label} isn't ticked on "
                                  f"the live Airbnb listing.",
                         "fix": f"If the home has it, tick {label} in Amenities (guests filter by it). "
                                f"If not, take it out of the copy before pasting."})
    return gaps
