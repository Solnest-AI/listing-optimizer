#!/usr/bin/env python3
"""Cadence rows must not claim something is DUE when there is no record of it at all.

Every report said "Full reshoot | never | DUE", including for listings shot last year:
the tool has no idea when the photos were taken, so "DUE" was a guess dressed as a fact.
"""
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
