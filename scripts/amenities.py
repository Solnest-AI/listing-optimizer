"""
amenities.py — deterministic subject-vs-comps amenity comparison for the digest.

The model used to receive the 30 most common comp amenities and diff them against the
subject's PMS amenity keys by eye. In a real market the top 30 are all 92-100% basics
(linens, smoke alarm), so the amenities that actually separate listings (workspace, fire
pit, BBQ, pets) never reached it, and the two vocabularies do not match: Hospitable says
`carbon_monoxide_detector`, AirROI says "Carbon monoxide alarm". Two runs of one listing
disagreed about whether it allowed pets. This does the diff in code instead.

Output is evidence, not a verdict. A comp amenity absent from the PMS list may still exist
at the property and simply be unticked, which is its own fix (checkbox filters drive
Airbnb search), so the digest labels the list as "not in the PMS amenity list".
"""
from __future__ import annotations

import re

MISSING_MIN_PCT = 30        # below this share of comps an amenity is not a market expectation
DIFFERENTIATOR_MAX_PCT = 50  # the subject has it and at most this share of comps do

# Normalized PMS key -> normalized AirROI label, for pairs that do not normalize to the
# same string. Every entry was observed in a real subject.json / comps.json pair.
ALIASES = {
    "ac": "air conditioning",
    "alfresco dining": "outdoor dining area",
    "barbeque utensils": "barbecue utensils",
    "bbq": "bbq grill",
    "books": "books and reading material",
    "carbon monoxide detector": "carbon monoxide alarm",
    "clothes drying rack": "drying rack for clothing",
    "fireplace": "indoor fireplace",
    "free on premise parking": "free parking on premises",
    "garden": "backyard",
    "jacuzzi": "hot tub",
    "laptop friendly workspace": "dedicated workspace",
    "outdoor seating": "outdoor furniture",
    "patio": "patio or balcony",
    "smoke detector": "smoke alarm",
    "street parking": "free street parking",
    "trash compacter": "trash compactor",
    "travel crib": "pack n play travel crib",
    "wardrobe or closet": "clothing storage",
    # Live Airbnb label heads (after the qualifier is stripped by canon()).
    "free dryer": "dryer",
    "free washer": "washer",
    "paid dryer": "dryer",
    "paid washer": "washer",
    "private backyard": "backyard",
    "shared backyard": "backyard",
}

# Airbnb prefixes brands and specs onto these ("TEKA stainless steel oven", "Samsung
# refrigerator", "Sonos sound system"). A label ending in one of them is that amenity.
BRANDED_SUFFIXES = ("oven", "stove", "refrigerator", "sound system", "coffee maker", "hair dryer",
                    "shampoo", "conditioner", "body soap", "shower gel", "exercise equipment",
                    "game console", "pool table")

# Safety and disclosure items: never suggested as selling points for the title or summary.
NOT_SELLING_POINTS = {"exterior security cameras on property", "smoke alarm", "carbon monoxide alarm",
                      "fire extinguisher", "first aid kit", "noise decibel monitors on property",
                      "essentials", "hangers", "hot water"}

# house_rules flags that Airbnb (and AirROI) list as amenities.
HOUSE_RULE_AMENITIES = {"pets_allowed": "pets allowed", "smoking_allowed": "smoking allowed"}

# How hosts actually write an amenity in copy, when that differs from its label.
COPY_SYNONYMS = {
    "pets allowed": ("pet", "dog"),
    "hot tub": ("hot tub", "hottub", "jacuzzi", "spa"),
    "dedicated workspace": ("workspace", "desk", "work from", "office"),
    "bbq grill": ("bbq", "grill", "barbecue"),
    "indoor fireplace": ("fireplace",),
    "fire pit": ("fire pit", "firepit"),
    "game console": ("playstation", "xbox", "nintendo", "console"),
    "ev charger": ("ev charger", "electric vehicle", "tesla"),
    "self check in": ("self check in", "keypad", "keyless", "smart lock"),
    "pack n play travel crib": ("pack n play", "crib"),
}


def norm(text) -> str:
    s = str(text or "").casefold().replace("’", "'").replace("‘", "'").replace("'", "")
    s = re.sub(r"[_\-/]", " ", s)
    s = re.sub(r"[^\w ]", " ", s)
    return " ".join(s.split())


def canon(text) -> str:
    # Airbnb qualifies labels: "Free dryer – In building", "Indoor fireplace: wood-burning".
    head = re.split(r"\s+[–—-]\s+|:", str(text or ""), maxsplit=1)[0]
    n = norm(head)
    n = ALIASES.get(n, n)
    for suffix in BRANDED_SUFFIXES:
        if n != suffix and n.endswith(" " + suffix):
            return suffix
    return n


def subject_amenity_set(amenities, house_rules) -> set[str]:
    have = {canon(a) for a in (amenities or []) if canon(a)}
    for key, label in HOUSE_RULE_AMENITIES.items():
        if isinstance(house_rules, dict) and house_rules.get(key) is True:
            have.add(label)
    return have


def _in_copy(label: str, copy_norm: str) -> bool:
    return any(norm(p) in copy_norm for p in COPY_SYNONYMS.get(label, (label,)))


def compare(amenities, house_rules, frequency, top_comps, copy_text: str) -> dict:
    """Diff the subject against the comp pool.

    frequency: comps.json market_amenity_frequency ({amenity, pct} rows)
    top_comps: comps.json top_comps (each with an `amenities` list), demand-ranked
    copy_text: the subject's title + summary, for the "have it, never say it" check
    """
    have = subject_amenity_set(amenities, house_rules)
    top_sets = [{canon(a) for a in (c.get("amenities") or [])}
                for c in (top_comps or []) if isinstance(c, dict)]
    copy_norm = norm(copy_text)
    market = set()
    missing, unsurfaced = [], []
    for row in frequency or []:
        if not isinstance(row, dict) or not row.get("amenity"):
            continue
        key, pct = canon(row["amenity"]), int(row.get("pct") or 0)
        market.add(key)
        hits = sum(1 for s in top_sets if key in s)
        entry = {"amenity": row["amenity"], "pct": pct, "top_hits": hits, "top_n": len(top_sets)}
        if key not in have and pct >= MISSING_MIN_PCT:
            missing.append(entry)
        elif (key in have and pct <= DIFFERENTIATOR_MAX_PCT and key not in NOT_SELLING_POINTS
              and not _in_copy(key, copy_norm)):
            unsurfaced.append(entry)
    missing.sort(key=lambda g: (-g["pct"], -g["top_hits"], g["amenity"]))
    unsurfaced.sort(key=lambda g: (g["pct"], g["amenity"]))
    return {
        "missing": missing,
        "unsurfaced": unsurfaced,
        "unmatched_subject": sorted(have - market),
        "subject_list_empty": not (amenities or []),
    }
