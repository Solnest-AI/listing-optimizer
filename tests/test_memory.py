#!/usr/bin/env python3
"""Tests for scripts/memory.py — the run-history memory layer.

Covers: the price-free record extraction, the guardrail (shared with render_report; the
funnel-prose drop keeps records clean) and the local-history upsert round-trip.

Run: .venv/bin/python -m pytest tests/test_memory.py -q
"""
import contextlib
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import memory
import render_report

mem = memory  # alias used by the history-upsert tests below


def _result(**overrides) -> dict:
    """A representative, price-free result.json — funnel includes prose `diagnosis`
    (which uses meta-words like 'pricing') to prove it gets dropped from the record."""
    base = {
        "listing": {"name": "Boho Bliss", "slug": "boho-bliss", "city": "Prince George, BC"},
        "run_date": "2026-06-08",
        "ale_scorecard": [
            {"dimension": "A — Amenities", "score": 4, "gap": "g", "fix": "f"},
            {"dimension": "L — Location", "score": 4, "gap": "g", "fix": "f"},
            {"dimension": "E — Experiences", "score": 2, "gap": "g", "fix": "f"},
        ],
        "optimized": {"title": "Walk to UHNBC | Boho Suite", "summary_char_count": 488},
        "photos": {"hero": 10, "recommended_top5_order": [10, 0, 1, 11, 15],
                   "reshoot": [5, 11, 18], "restage": [11, 12]},
        "comps": {"amenity_gaps": ["Cooling — portable fans (33% of comps)",
                                   "Free street parking (63% of comps)"]},
        "funnel": {
            "source": "RankBreeze", "rankbreeze_id": "148285",
            "city_rank": {"position": 18, "of": 320, "page": 1},
            "views_monthly": {"May": "186 views", "Jun": "39 views"},
            "booking_rate_monthly": {"Jun": "71.79%"},
            "ctr_vs_similar": {"you": "8.68%", "similar": "15.07%"},
            # lever_focus legitimately carries the qualitative handoff meta-words
            # "revenue/pricing" (no numbers) — allowed, like in the rendered report.
            "lever_focus": "above-the-fold (cover/hero + title); the booking gap is a "
                           "rate/availability question — hand to a revenue/pricing tool",
            "diagnosis": "Page-1 ranked. Hand off to your revenue/pricing tool for $400 a night rates.",
        },
        "occupancy": {"forward_pct": 35.2, "monthly": {"Jun": "56%", "Jul": "61%"}},
    }
    base.update(overrides)
    return base


# ── guardrail stays in sync with the renderer ────────────────────────────────
def test_price_guard_is_the_renderers_pattern():
    assert memory.PRICE_NUMBER_RE is render_report.PRICE_NUMBER_RE


# ── summarize ────────────────────────────────────────────────────────────────
def test_summarize_core_fields():
    rec = memory.summarize(_result(), result_path="/x/result.json")
    assert rec["listing_slug"] == "boho-bliss"
    assert rec["listing_name"] == "Boho Bliss"
    assert rec["city"] == "Prince George, BC"
    assert rec["run_date"] == "2026-06-08"
    assert rec["ale_total"] == round((4 + 4 + 2) / 3, 2)
    # Stored under the canonical spelling so per-dimension trends line up across runs.
    assert rec["ale_scores"] == [
        {"dimension": "A: Amenities surfaced", "score": 4},
        {"dimension": "L: Location specifics", "score": 4},
        {"dimension": "E: Experiences staged", "score": 2},
    ]
    assert rec["title"] == "Walk to UHNBC | Boho Suite"
    assert rec["summary_char_count"] == 488
    assert rec["photo_hero"] == 10
    assert rec["photo_top5"] == [10, 0, 1, 11, 15]
    assert rec["reshoot_count"] == 3
    assert rec["occupancy_forward_pct"] == 35.2
    assert rec["occupancy_monthly"] == {"Jun": "56%", "Jul": "61%"}
    assert rec["applied"] is False
    assert rec["result_path"] == "/x/result.json"
    # exactly the COLUMNS keys, nothing extra
    assert set(rec.keys()) == set(memory.COLUMNS)


def test_funnel_subset_drops_prose_diagnosis():
    rec = memory.summarize(_result())
    assert "diagnosis" not in rec["funnel"]          # prose dropped...
    assert rec["funnel"]["city_rank"] == {"position": 18, "of": 320, "page": 1}
    assert rec["funnel"]["lever_focus"].startswith("above-the-fold")
    memory.assert_price_free(rec)                     # ...so the record stays clean


def test_cli_overrides_win():
    rec = memory.summarize(_result(), season="summer", applied=True,
                           cadence_marked=["title", "photo_rotation"])
    assert rec["season"] == "summer"
    assert rec["applied"] is True
    assert rec["cadence_marked"] == ["title", "photo_rotation"]


def test_missing_slug_or_date_raises():
    bad = _result()
    bad["listing"] = {"name": "x"}
    with pytest.raises(SystemExit):
        memory.summarize(bad)


# ── price guardrail ──────────────────────────────────────────────────────────
def test_assert_price_free_passes_clean_record():
    memory.assert_price_free(memory.summarize(_result()))  # no raise


def test_assert_price_free_rejects_planted_price():
    bad = _result()
    bad["comps"] = {"amenity_gaps": ["Competitors charge $450 a night for this"]}
    rec = memory.summarize(bad)
    with pytest.raises(SystemExit):
        memory.assert_price_free(rec)


def test_assert_price_free_allows_handoff_metawords():
    """The funnel lever_focus says 'revenue/pricing tool' (meta-words, no number) —
    that must pass, exactly as the rendered report allows it."""
    rec = memory.summarize(_result())
    assert "revenue/pricing" in rec["funnel"]["lever_focus"]
    memory.assert_price_free(rec)  # no raise


def test_assert_price_free_rejects_price_number_in_kept_field():
    bad = _result()
    bad["funnel"]["lever_focus"] = "raise the floor to $300/night next season"
    rec = memory.summarize(bad)
    with pytest.raises(SystemExit):
        memory.assert_price_free(rec)


# ── local history ────────────────────────────────────────────────────────────
def test_local_history_roundtrip_and_order(tmp_path):
    h = tmp_path / "history.jsonl"
    memory.append_local(memory.summarize(_result(run_date="2026-06-01")), h)
    memory.append_local(memory.summarize(_result(run_date="2026-06-08")), h)
    rows = memory.prior_runs("boho-bliss", h, limit=3)
    assert [r["run_date"] for r in rows] == ["2026-06-08", "2026-06-01"]  # newest first


def test_prior_runs_dedups_same_day(tmp_path):
    h = tmp_path / "history.jsonl"
    r1 = _result(run_date="2026-06-08")
    r1["optimized"]["title"] = "old title"
    r2 = _result(run_date="2026-06-08")
    r2["optimized"]["title"] = "new title"
    memory.append_local(memory.summarize(r1), h)
    memory.append_local(memory.summarize(r2), h)
    rows = memory.prior_runs("boho-bliss", h, limit=5)
    assert len(rows) == 1 and rows[0]["title"] == "new title"  # latest same-day wins


def test_prior_runs_filters_by_listing(tmp_path):
    h = tmp_path / "history.jsonl"
    memory.append_local(memory.summarize(_result()), h)
    other = _result()
    other["listing"]["slug"] = "urban-nest"
    memory.append_local(memory.summarize(other), h)
    assert len(memory.prior_runs("boho-bliss", h)) == 1


# ── Local history is an UPSERT, not an append ────────────────────────────────
# Real bug, 2026-09-20: a blind append left two rows per same-day re-run, and `prior`
# served the stale sibling of today's run as the previous run.
def _rec(slug="x", date="2026-09-20", **extra):
    r = {"listing_slug": slug, "run_date": date, "ale_total": 3.0, "title": "t"}
    r.update(extra)
    return r


def test_same_day_rerun_replaces_instead_of_appending():
    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp) / "history.jsonl"
        assert mem.append_local(_rec(ale_total=3.0), h) == "inserted"
        assert mem.append_local(_rec(ale_total=3.5), h) == "replaced"
        rows = mem.read_local(h)
        assert len(rows) == 1, f"same-day re-run appended a duplicate: {len(rows)} rows"
        assert rows[0]["ale_total"] == 3.5, "last write must win"


def test_different_days_and_listings_still_append():
    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp) / "history.jsonl"
        mem.append_local(_rec("boho", "2026-09-01"), h)
        mem.append_local(_rec("boho", "2026-09-20"), h)
        mem.append_local(_rec("ambler", "2026-09-20"), h)
        assert len(mem.read_local(h)) == 3, "upsert must not collapse distinct keys"


def test_replacement_keeps_chronological_position():
    """A re-run must update the row in place, not move it to the end, or the history stops
    reading chronologically and `prior` picks the wrong 'previous' run."""
    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp) / "history.jsonl"
        for d in ("2026-07-01", "2026-08-01", "2026-09-01"):
            mem.append_local(_rec("boho", d), h)
        mem.append_local(_rec("boho", "2026-08-01", ale_total=4.9), h)
        dates = [r["run_date"] for r in mem.read_local(h)]
        assert dates == ["2026-07-01", "2026-08-01", "2026-09-01"], f"order broken: {dates}"
        mid = [r for r in mem.read_local(h) if r["run_date"] == "2026-08-01"][0]
        assert mid["ale_total"] == 4.9


def test_row_key_is_listing_and_date():
    assert mem._row_key({"listing_slug": "a", "run_date": "b"}) == ("a", "b")
    assert mem._KEY_COLS == ("listing_slug", "run_date")


def test_unparseable_lines_are_never_dropped():
    """Losing history to a repair is worse than the duplicate it fixes."""
    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp) / "history.jsonl"
        h.write_text("{ corrupt line\n" + json.dumps(_rec()) + "\n"
                     + json.dumps(_rec()) + "\n", encoding="utf-8")
        mem.append_local(_rec(ale_total=4.0), h)
        raw = h.read_text().splitlines()
        assert "{ corrupt line" in raw, "a corrupt line was silently discarded"
        assert len(raw) == 2, f"expected corrupt line + 1 collapsed row, got {raw}"
        assert json.loads(raw[1])["ale_total"] == 4.0


def test_keyless_record_is_appended_not_collapsed():
    """Defensive: two records that both lack a key are not 'the same row'."""
    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp) / "history.jsonl"
        mem.append_local({"note": "a"}, h)
        mem.append_local({"note": "b"}, h)
        assert len(mem.read_local(h)) == 2, "keyless rows were wrongly collapsed"


def test_history_write_is_atomic_no_partial_file():
    """An interrupted rewrite must not truncate the history."""
    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp) / "history.jsonl"
        mem.append_local(_rec("boho", "2026-01-01"), h)
        mem.append_local(_rec("boho", "2026-02-01"), h)
        original = h.read_text()
        real = mem._atomic_write_lines

        def boom(path, lines):
            raise OSError("disk full")
        mem._atomic_write_lines = boom
        try:
            with contextlib.suppress(OSError):
                mem.append_local(_rec("boho", "2026-03-01"), h)
            assert h.read_text() == original, "history was damaged by a failed write"
        finally:
            mem._atomic_write_lines = real
        assert len(list(Path(tmp).glob("*.tmp"))) == 0, "temp file left behind"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
