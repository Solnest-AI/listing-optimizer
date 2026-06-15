#!/usr/bin/env python3
"""Tests for scripts/memory.py — the run-history memory layer.

Covers: the price-free record extraction, the guardrail (incl. that it stays in
sync with render_report and that the funnel-prose drop keeps records clean),
local-history round-trip + same-day de-dup, and the injection-safe SQL emitters.

Run: .venv/bin/python -m pytest tests/test_memory.py -q
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import memory  # noqa: E402
from render_report import PRICING_RE as RENDER_PRICING_RE  # noqa: E402


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
            "lever_focus": "above-the-fold (cover/hero photo + title)",
            "diagnosis": "Page-1 ranked. Hand off to your revenue/pricing tool for $400 a night rates.",
        },
        "occupancy": {"forward_pct": 35.2, "monthly": {"Jun": "56%", "Jul": "61%"}},
    }
    base.update(overrides)
    return base


# ── guardrail stays in sync with the renderer ────────────────────────────────
def test_pricing_re_matches_render_report():
    assert memory.PRICING_RE.pattern == RENDER_PRICING_RE.pattern, \
        "memory.PRICING_RE drifted from render_report.PRICING_RE — keep them byte-identical."


# ── summarize ────────────────────────────────────────────────────────────────
def test_summarize_core_fields():
    rec = memory.summarize(_result(), result_path="/x/result.json")
    assert rec["listing_slug"] == "boho-bliss"
    assert rec["listing_name"] == "Boho Bliss"
    assert rec["city"] == "Prince George, BC"
    assert rec["run_date"] == "2026-06-08"
    assert rec["ale_total"] == round((4 + 4 + 2) / 3, 2)
    assert rec["ale_scores"] == [
        {"dimension": "A — Amenities", "score": 4},
        {"dimension": "L — Location", "score": 4},
        {"dimension": "E — Experiences", "score": 2},
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


# ── SQL emitters ─────────────────────────────────────────────────────────────
def test_upsert_sql_dollar_quoted_and_complete():
    rec = memory.summarize(_result())
    rec["listing_name"] = "Guest's Cozy Place"           # apostrophe must not break SQL
    sql = memory.upsert_sql(rec)
    assert "INSERT INTO public.listing_optimizer_runs" in sql
    assert "ON CONFLICT (listing_slug, run_date) DO UPDATE SET" in sql
    assert "::jsonb" in sql                                # jsonb columns cast
    assert "''" not in sql                                 # no fragile single-quote escaping
    assert "$lo$Guest's Cozy Place$lo$" in sql            # dollar-quoted text
    for col in memory.COLUMNS:                             # every column present
        assert col in sql


def test_dollar_quote_avoids_tag_collision():
    q = memory._dollar_quote("contains $lo$ literally")
    assert q.startswith("$lo1$") and q.endswith("$lo1$")


def test_prior_sql_shape():
    sql = memory.prior_sql("boho-bliss", limit=3)
    assert "WHERE listing_slug = $lo$boho-bliss$lo$" in sql
    assert "ORDER BY run_date DESC" in sql
    assert "LIMIT 3" in sql


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
