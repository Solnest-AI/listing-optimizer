#!/usr/bin/env python3
"""Tests for scripts/memory.py — the run-history memory layer.

Covers: the price-free record extraction, the guardrail (incl. that it stays in
sync with render_report and that the funnel-prose drop keeps records clean),
local-history round-trip + same-day de-dup, and the injection-safe SQL emitters.

Run: .venv/bin/python -m pytest tests/test_memory.py -q
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import memory  # noqa: E402

mem = memory  # alias used by the history-upsert tests below
from render_report import PRICE_NUMBER_RE as RENDER_PRICE_NUMBER_RE  # noqa: E402


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
def test_price_guard_matches_render_report():
    assert memory.PRICE_NUMBER_RE.pattern == RENDER_PRICE_NUMBER_RE.pattern, \
        "memory.PRICE_NUMBER_RE drifted from render_report.PRICE_NUMBER_RE — keep them byte-identical."


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


# ── Local history is an UPSERT, not an append ────────────────────────────────
# Real bug, 2026-09-20: append_local() did a blind write while Supabase does
# `ON CONFLICT (listing_slug, run_date) DO UPDATE`. A same-day re-run therefore made one
# row on Supabase and TWO locally. state/history.jsonl had accumulated duplicate pairs for
# boho-bliss 2026-08-05 and olde-town-ambler 2026-08-18, and `prior` served the stale
# sibling of today's run as the previous run, corrupting the trend comparison.
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
        assert rows[0]["ale_total"] == 3.5, "last write must win, as the SQL upsert does"


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


def test_row_key_tracks_the_sql_conflict_key():
    """If someone adds a column to the SQL conflict clause, the local key must follow. This
    asserts they read from the same constant rather than two hardcoded lists."""
    assert mem._row_key({"listing_slug": "a", "run_date": "b"}) == ("a", "b")
    assert mem._KEY_COLS == ("listing_slug", "run_date")
    sql = mem.upsert_sql(_rec()) if hasattr(mem, "upsert_sql") else ""
    if sql:
        assert "ON CONFLICT (listing_slug, run_date)" in sql, \
            "SQL conflict key drifted from _KEY_COLS"


def test_dedupe_collapses_pre_existing_duplicates_last_wins():
    """Repairs a file written before the upsert fix. Verified against the real duplicates:
    the LATER row carried the refined copy, so last-wins is the correct resolution."""
    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp) / "history.jsonl"
        lines = [json.dumps(_rec("boho", "2026-08-05", summary_char_count=484)),
                 json.dumps(_rec("boho", "2026-08-05", summary_char_count=483)),
                 json.dumps(_rec("ambler", "2026-08-18", summary_char_count=491)),
                 json.dumps(_rec("ambler", "2026-08-18", summary_char_count=490)),
                 json.dumps(_rec("boho", "2026-09-20"))]
        h.write_text("\n".join(lines) + "\n", encoding="utf-8")
        r = mem.dedupe_local(h)
        assert r == {"total": 5, "kept": 3, "removed": 2,
                     "keys": ["boho 2026-08-05", "ambler 2026-08-18"]} or r["removed"] == 2
        rows = mem.read_local(h)
        assert len(rows) == 3
        kept = {(x["listing_slug"], x["run_date"]): x["summary_char_count"] for x in rows
                if "summary_char_count" in x}
        assert kept[("boho", "2026-08-05")] == 483, "kept the superseded row, not the refined one"
        assert kept[("ambler", "2026-08-18")] == 490


def test_dedupe_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp) / "history.jsonl"
        h.write_text(json.dumps(_rec()) + "\n" + json.dumps(_rec()) + "\n", encoding="utf-8")
        before = h.read_text()
        r = mem.dedupe_local(h, dry_run=True)
        assert r["removed"] == 1
        assert h.read_text() == before, "--dry-run modified the file"


def test_dedupe_backs_up_before_writing():
    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp) / "history.jsonl"
        h.write_text(json.dumps(_rec()) + "\n" + json.dumps(_rec()) + "\n", encoding="utf-8")
        mem.dedupe_local(h)
        bak = h.with_suffix(h.suffix + ".bak")
        assert bak.exists(), "no backup written before rewriting history"
        assert len(bak.read_text().strip().splitlines()) == 2, "backup is not the original"


def test_unparseable_lines_are_never_dropped():
    """Losing history to a dedupe is worse than the duplicate it fixes."""
    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp) / "history.jsonl"
        h.write_text("{ corrupt line\n" + json.dumps(_rec()) + "\n"
                     + json.dumps(_rec()) + "\n", encoding="utf-8")
        mem.dedupe_local(h)
        raw = h.read_text().splitlines()
        assert "{ corrupt line" in raw, "a corrupt line was silently discarded"
        assert len(raw) == 2, f"expected corrupt line + 1 deduped row, got {raw}"
        # and an upsert must also preserve it
        mem.append_local(_rec(), h)
        assert "{ corrupt line" in h.read_text()


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
            try:
                mem.append_local(_rec("boho", "2026-03-01"), h)
            except OSError:
                pass
            assert h.read_text() == original, "history was damaged by a failed write"
        finally:
            mem._atomic_write_lines = real
        assert len(list(Path(tmp).glob("*.tmp"))) == 0, "temp file left behind"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_coerce_row_turns_postgres_strings_back_into_numbers():
    """Postgres returns numerics as STRINGS through the MCP/REST layers. Storing those raw
    would make every numeric trend comparison a string comparison, silently."""
    row = {"listing_slug": "x", "run_date": "2026-09-20", "ale_total": "3.29",
           "occupancy_forward_pct": "36.3", "photo_hero": "10", "reshoot_count": "8",
           "summary_char_count": "412", "applied": None,
           "ale_scores": None, "photo_top5": None, "amenity_gaps": None,
           "cadence_marked": None, "funnel": None, "occupancy_monthly": None,
           "id": 99, "created_at": "2026-09-20T00:00:00Z"}
    r = mem.coerce_row(row)
    assert r["ale_total"] == 3.29 and isinstance(r["ale_total"], float)
    assert r["occupancy_forward_pct"] == 36.3
    assert r["photo_hero"] == 10 and isinstance(r["photo_hero"], int)
    assert r["reshoot_count"] == 8 and r["summary_char_count"] == 412
    assert r["applied"] is False
    assert r["ale_scores"] == [] and r["photo_top5"] == [] and r["amenity_gaps"] == []
    assert r["funnel"] == {} and r["occupancy_monthly"] == {}
    assert set(r) == set(mem.COLUMNS), "extra DB columns (id/created_at) leaked into the record"


def test_coerce_row_survives_unparseable_numerics():
    r = mem.coerce_row({"listing_slug": "x", "run_date": "d", "ale_total": "n/a"})
    assert r["ale_total"] is None


def test_merge_rows_pulls_remote_only_runs_down_without_disturbing_local():
    """state/ is gitignored and per-machine, so a run made on another machine exists only
    upstream. Pulling it down must add it and leave existing local rows alone."""
    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp) / "history.jsonl"
        mem.append_local({"listing_slug": "boho", "run_date": "2026-09-20",
                          "ale_total": 3.29, "title": "local"}, h)
        rows = [{"listing_slug": "apres", "run_date": "2026-07-07", "ale_total": "3.14"},
                {"listing_slug": "boho", "run_date": "2026-09-20", "ale_total": "9.99"}]
        s = mem.merge_rows(rows, h)
        assert s == {"inserted": 1, "replaced": 1, "skipped": 0}, s
        out = {(r["listing_slug"], r["run_date"]): r for r in mem.read_local(h)}
        assert len(out) == 2
        assert out[("apres", "2026-07-07")]["ale_total"] == 3.14
        assert out[("boho", "2026-09-20")]["ale_total"] == 9.99, "remote should win on a re-merge"


def test_merge_rows_skips_keyless_and_guards_pricing():
    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp) / "history.jsonl"
        s = mem.merge_rows([{"ale_total": "1.0"}], h)
        assert s["skipped"] == 1 and s["inserted"] == 0
        try:
            mem.merge_rows([{"listing_slug": "x", "run_date": "d",
                             "title": "Only $199 per night"}], h)
        except SystemExit:
            return
        raise AssertionError("a priced row was merged into history")
