#!/usr/bin/env python3
"""Cadence rows must not claim something is DUE when there is no record of it at all.

Every report said "Full reshoot | never | DUE", including for listings shot last year:
the tool has no idea when the photos were taken, so "DUE" was a guess dressed as a fact.
"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import cadence


def test_untracked_items_say_no_record_not_due(tmp_path, monkeypatch):
    monkeypatch.setattr(cadence, "STATE_PATH", tmp_path / "state.json")
    cadence.mark("cabin", ["title"], date(2026, 8, 1))
    rows = {r["item"]: r for r in cadence.check("cabin", date(2026, 9, 25))}
    assert rows["Title"]["status"] == "DUE"
    assert rows["Full reshoot"]["status"] == "no record"
    assert rows["Full reshoot"]["due"] == "unknown"


def test_malformed_date_in_state_is_no_record_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(cadence, "STATE_PATH", tmp_path / "state.json")
    (tmp_path / "state.json").write_text('{"cabin": {"title": "last tuesday"}}')
    rows = {r["item"]: r for r in cadence.check("cabin", date(2026, 9, 25))}
    assert rows["Title"]["status"] == "no record"


def test_html_report_does_not_paint_no_record_green():
    from jinja2 import Environment, FileSystemLoader
    root = Path(__file__).resolve().parent.parent
    env = Environment(loader=FileSystemLoader(str(root / ".claude/skills/listing-optimizer/output-templates")),
                      autoescape=True)
    html = env.get_template("report.html.j2").render(data={
        "listing": {"name": "x"}, "optimized": {"title": "t", "summary": "s", "the_space": "sp"},
        "branding": {}, "cadence": {"due": [{"item": "Full reshoot", "last": "no record",
                                             "due": "unknown", "status": "no record"}]}})
    assert "b-good" not in html.split("Full reshoot")[1][:300]


def test_markdown_photo_plan_keeps_hero_and_order_on_separate_lines():
    """olde-town-ambler 2026-09-25 rendered '- **Hero:** photo #50- **Recommended order:**'
    on one line: trim_blocks ate the newline after the hero's {% endif %}."""
    from jinja2 import Environment, FileSystemLoader
    root = Path(__file__).resolve().parent.parent
    env = Environment(loader=FileSystemLoader(str(root / ".claude/skills/listing-optimizer/output-templates")),
                      trim_blocks=True, lstrip_blocks=True)
    md = env.get_template("report.md.j2").render(data={
        "listing": {"name": "x"}, "optimized": {"title": "t", "summary": "s", "the_space": "sp"},
        "branding": {}, "photos": {"hero": 50, "recommended_top5_order": [50, 52]}})
    assert "- **Hero:** photo #50\n- **Recommended order:** 50 → 52" in md


def _hist(tmp_path, rows):
    h = tmp_path / "history.jsonl"
    h.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return h


def test_marks_from_draft_runs_do_not_count(tmp_path, monkeypatch):
    """boho-bliss 2026-09-26: cadence said 0/7 due because title/photos/captions were marked
    2026-09-20, but that run was a draft (applied=False) and the live Airbnb title was still
    the August one. A mark counts only when a run on that date was confirmed applied."""
    monkeypatch.setattr(cadence, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(cadence, "HISTORY_PATH", _hist(tmp_path, [
        {"listing_slug": "cabin", "run_date": "2026-09-20", "applied": False},
        {"listing_slug": "cabin", "run_date": "2026-08-05", "applied": True}]))
    cadence.mark("cabin", ["title"], date(2026, 9, 20))
    cadence.mark("cabin", ["the_space"], date(2026, 8, 5))
    rows = {r["item"]: r for r in cadence.check("cabin", date(2026, 9, 26))}
    assert rows["Title"]["status"] == "DUE" and "draft" in rows["Title"]["last"]
    space = rows["“The Space” rewrite"]
    assert space["status"] == "ok" and space["last"] == "2026-08-05", space


def test_marks_are_trusted_when_the_listing_has_no_history(tmp_path, monkeypatch):
    monkeypatch.setattr(cadence, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(cadence, "HISTORY_PATH", tmp_path / "missing.jsonl")
    cadence.mark("cabin", ["title"], date(2026, 9, 20))
    rows = {r["item"]: r for r in cadence.check("cabin", date(2026, 9, 26))}
    assert rows["Title"]["status"] == "ok"


def test_history_refuses_cadence_marks_on_a_draft():
    import pytest

    import memory
    with pytest.raises(SystemExit, match="applied"):
        memory.summarize({"listing": {"slug": "cabin"}, "run_date": "2026-09-26"},
                         cadence_marked=["title"], applied=False)
