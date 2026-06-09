#!/usr/bin/env python3
"""Tests for occupancy.py — booking-biased classification + cross-check integrity.
Run: .venv/bin/python tests/test_occupancy.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import occupancy as occ  # noqa: E402


def day(date, available=None, reason=""):
    return {"date": date, "status": {"available": available, "reason": reason}}


def run():
    fails = []

    def expect(cond, msg):
        if not cond:
            fails.append(msg)

    # 1) Booking-biased classification: an unavailable night is BOOKED unless it's a host block.
    expect(occ._classify(day("2026-06-01", available=True, reason="AVAILABLE")) == "open", "available→open")
    expect(occ._classify(day("2026-06-02", available=False, reason="Airbnb")) == "booked",
           "unavailable 'Airbnb' should be booked (not blocked)")
    expect(occ._classify(day("2026-06-03", available=False, reason="Guest confirmed - Airbnb")) == "booked",
           "unavailable 'Guest confirmed' should be booked")
    expect(occ._classify(day("2026-06-04", available=False, reason="Owner block")) == "blocked",
           "owner block → blocked")
    expect(occ._classify(day("2026-06-05", available=False, reason="Maintenance")) == "blocked",
           "maintenance → blocked")
    # status as a bare string (API shape drift) must not crash
    expect(occ._classify({"date": "x", "status": "unavailable"}) == "blocked", "string status handled")

    # 2) Adjusted occupancy: blocked excluded from the denominator.
    days = [day("2026-06-01", False, "Airbnb"), day("2026-06-02", False, "Airbnb"),
            day("2026-06-03", True, "AVAILABLE"), day("2026-06-04", False, "Owner block")]
    c = occ.compute(days)
    # 2 booked, 1 open, 1 blocked → 2/(2+1) = 66.7%
    expect(c["forward_window"]["occupancy_pct"] == 66.7, f"adjusted occ math (got {c['forward_window']})")

    # 3) Cross-check must NOT say "agree" when no months overlap.
    cc_bad = occ.crosscheck({"Jun": {"occupancy_pct": 0.0}}, "Foo:10,Bar:20")
    expect("no overlap" in cc_bad["verdict"], f"label mismatch must not 'agree' (got {cc_bad['verdict']})")
    # …and label normalization works: "June" → "Jun".
    cc_ok = occ.crosscheck({"Jun": {"occupancy_pct": 0.0}}, "June:0")
    expect(cc_ok["verdict"] == "agree", f"normalized month should agree (got {cc_ok['verdict']})")
    cc_div = occ.crosscheck({"Jun": {"occupancy_pct": 80.0}}, "Jun:0")
    expect("DIVERGE" in cc_div["verdict"], f"80 vs 0 should diverge (got {cc_div['verdict']})")

    return fails


def test_occupancy_no_regressions():
    """pytest entry — fails if classification/math/cross-check regress."""
    fails = run()
    assert not fails, "; ".join(fails)


if __name__ == "__main__":
    fails = run()
    if fails:
        print(f"❌ {len(fails)} occupancy test failures:")
        for f in fails:
            print(f"   - {f}")
        sys.exit(1)
    print("✅ occupancy tests OK — classification booking-biased, math + cross-check sound")
