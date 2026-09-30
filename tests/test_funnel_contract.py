"""The funnel block's city_rank and ctr_vs_similar shapes.

2026-09-29/30: SKILL.md showed both as "" strings, the templates read them as objects, and
both writer runs sent prose ("139 (page 8), Sep 1 to 29"). The reports rendered
"City rank: # of  (page )" and "_CTR  vs  similar. _".
"""
import json
import re
import sys
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import render_report as rr
from test_portfolio_bugs import _result

ROOT = Path(__file__).resolve().parents[1]


def _skill_example() -> dict:
    text = (ROOT / ".claude" / "skills" / "listing-optimizer" / "SKILL.md").read_text(encoding="utf-8")
    block = re.search(r"Author only this compact shape.*?```json\n(.*?)```", text, re.S).group(1)
    return json.loads(block)


def _render(funnel) -> tuple[str, str]:
    data = _result(funnel)
    rr.validate_result(data)
    data["branding"] = rr.load_branding(ROOT / "no-branding.json", data["listing"]["name"])
    env = Environment(loader=FileSystemLoader(str(rr.TEMPLATES_DIR)),
                      autoescape=lambda name: bool(name) and name.endswith((".html.j2", ".html")),
                      trim_blocks=True, lstrip_blocks=True)
    return tuple(env.get_template(t).render(data=data, lower_sections=rr.LOWER_SECTIONS)
                 for t in ("report.md.j2", "report.html.j2"))


def test_the_documented_example_passes_the_validator():
    funnel = _skill_example()["funnel"]
    assert isinstance(funnel["city_rank"], dict) and isinstance(funnel["ctr_vs_similar"], dict)
    rr.validate_result(_result(funnel))


@pytest.mark.parametrize("funnel, field", [
    ({"city_rank": "139 (page 8), Sep 1 to 29"}, "funnel.city_rank"),
    ({"city_rank": {"position": "139", "page": 8}}, "funnel.city_rank"),
    ({"ctr_vs_similar": "26.85% vs 11.43% for similar listings"}, "funnel.ctr_vs_similar"),
    ({"ctr_vs_similar": {"you": "26.85%"}}, "funnel.ctr_vs_similar"),
])
def test_prose_or_partial_objects_are_a_clear_validation_error(funnel, field):
    with pytest.raises(ValueError, match=re.escape(field) + r" must be \{"):
        rr.validate_result(_result(funnel))


def test_blank_fields_count_as_absent():
    data = _result({"source": "RankBreeze", "city_rank": "", "ctr_vs_similar": {}})
    rr.validate_result(data)
    assert "city_rank" not in data["funnel"] and "ctr_vs_similar" not in data["funnel"]
    md, html = _render({"source": "RankBreeze", "city_rank": ""})
    assert "City rank" not in md and "City rank" not in html


def test_an_average_rank_without_a_total_renders_cleanly():
    md, html = _render({"source": "RankBreeze", "city_rank": {"position": 139, "page": 8},
                        "ctr_vs_similar": {"you": "26.85%", "similar": "11.43%", "note": ""}})
    assert "- **City rank:** #139 (page 8)\n" in md
    assert "_CTR 26.85% vs 11.43% similar._" in md
    assert "City rank:</strong> #139 · page 8</div>" in html
    assert "CTR 26.85% vs 11.43% similar.</div>" in html
    assert " of " not in md.split("City rank:", 1)[1].split("\n", 1)[0]


def test_a_full_rank_and_note_render():
    md, html = _render({"source": "RankBreeze", "city_rank": {"position": 18, "of": 320, "page": 1},
                        "ctr_vs_similar": {"you": "8.68%", "similar": "15.07%", "note": "Aug pull."}})
    assert "- **City rank:** #18 of 320 (page 1)" in md
    assert "_CTR 8.68% vs 15.07% similar. Aug pull._" in md
    assert "#18 of 320 · page 1" in html
