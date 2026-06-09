#!/usr/bin/env python3
"""Battery test for the zero-pricing guardrail regex (render_report.PRICING_RE).

The #1 hard rule is that no price/ADR/RevPAR/revenue/min-stay term reaches a
deliverable. The regex must catch all pricing forms WITHOUT false-positiving on
the funnel section's legit terms ("Booking rate", "Click-through rate", "occupancy").
Run: .venv/bin/python tests/test_guardrail.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from render_report import PRICING_RE, PRICE_NUMBER_RE  # noqa: E402

MUST_MATCH = [
    "$150", "costs $150", "from $400", "$ 1 258", "$1,258", "€300", "£175", "¥5000",
    "CAD 200", "USD120", "1,258/night", "175/night", "175 per night", "rate per night",
    "nightly rate", "average daily rate", "ADR is high", "RevPAR", "monthly revenue",
    "priced at", "dynamic pricing", "2-night minimum", "minimum stay", "min stay",
    "min-stay", "minimum nights", "3 night minimum", "$450 a night",
    "450 a night", "250 per stay", "300 dollars", "150 dollars a night",
    "300-500 a night", "200 to 400 a night",
    "min nights", "minimum 3 nights", "min 2 nights", "minimum nights", "minimum 3-night",
]
MUST_NOT_MATCH = [
    "Booking rate", "Click-through rate 18.86%", "conversion rate", "occupancy 21%",
    "Airbnb Occupancy", "rating 4.9", "5-star reviews", "CTR 18.86%", "first chairs",
    "2-min walk to the lift", "sleeps 7", "3 bedrooms", "golden-hour soak",
    "nightly s'mores by the firepit", "minimum age 25", "per the host's request",
    "the Old Town Village", "the Regional Airport", "City rank #38 of 320",
    "~80-145 listing views/month", "Forward 60 days", "0 upcoming reservations", "16 reviews",
    "perfect for a 2-night stay", "minimum age 25", "stay 3 nights and unwind",
]

# PRICE_NUMBER_RE (report scan): catches price NUMBERS, but ALLOWS the qualitative
# diagnostics/handoff meta-words so the "run revenue-manager" handoff can exist.
NUM_MUST_MATCH = [
    "$150", "costs $150", "from $400", "$ 1 258", "$1,258", "CA$420", "€300", "£175",
    "¥5000", "CAD 200", "USD120", "1,258/night", "175/night", "175 per night",
    "nightly rate", "average daily rate", "2-night minimum", "3 night minimum", "$450 a night",
    "450 a night", "250 per stay", "300 dollars", "150 dollars a night",
    "300-500 a night", "200 to 400 a night", "min nights", "minimum 3 nights",
]
NUM_MUST_NOT_MATCH = [
    "this is a pricing/availability lever", "run the revenue-manager skill",
    "pricing", "priced strategy", "revenue", "ADR", "RevPAR", "per night",
    "minimum stay", "min-stay", "Booking rate", "occupancy 0%", "Click-through rate",
    "~80-145 listing views/month", "Forward 60 days", "ranked page 3 (#38 of 320)", "16 reviews",
    "perfect for a 2-night stay", "stay 3 nights and unwind",
]


def run():
    fails = []
    for s in MUST_MATCH:
        if not PRICING_RE.search(s):
            fails.append(("strict SHOULD MATCH but didn't", s))
    for s in MUST_NOT_MATCH:
        m = PRICING_RE.search(s)
        if m:
            fails.append((f"strict SHOULD NOT MATCH but did (hit {m.group(0)!r})", s))
    for s in NUM_MUST_MATCH:
        if not PRICE_NUMBER_RE.search(s):
            fails.append(("numeric SHOULD MATCH but didn't", s))
    for s in NUM_MUST_NOT_MATCH:
        m = PRICE_NUMBER_RE.search(s)
        if m:
            fails.append((f"numeric SHOULD NOT MATCH but did (hit {m.group(0)!r})", s))
    return fails


def test_guardrail_no_regressions():
    """pytest entry — fails if any pricing form leaks or a legit phrase is blocked."""
    fails = run()
    assert not fails, "\n".join(f"[{k}] {s!r}" for k, s in fails)


if __name__ == "__main__":
    fails = run()
    if fails:
        print(f"❌ {len(fails)} guardrail regex failures:")
        for kind, s in fails:
            print(f"   [{kind}] {s!r}")
        sys.exit(1)
    print(f"✅ guardrail OK — strict: {len(MUST_MATCH)} caught / {len(MUST_NOT_MATCH)} clean · "
          f"numeric: {len(NUM_MUST_MATCH)} caught / {len(NUM_MUST_NOT_MATCH)} clean")
