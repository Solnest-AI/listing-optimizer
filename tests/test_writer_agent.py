"""The listing-writer agent carries the skill's rules in its system prompt.

2026-09-26: the rules were proposed to live in two places (skill files and agent prompt);
a hand edit to one would silently drift from the other. The agent file is generated, and
these tests fail the moment it goes stale.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_writer_agent as bwa


def test_committed_agent_matches_the_rule_files():
    assert bwa.main(["--check"]) == 0, "run scripts/build_writer_agent.py and commit the result"


def test_every_rule_file_is_copied_verbatim():
    text = bwa.build()
    for src in bwa.SOURCES:
        body = src.read_text(encoding="utf-8").rstrip()
        assert (bwa.skill_for_writer(body) if src.name == "SKILL.md" else body) in text, src.name


def test_writer_gets_the_writing_rules_but_not_the_setup_sections():
    text = bwa.build()
    for kept in ("## Non-negotiable boundaries", "## 2a.", "## 3. Write result.json",
                 "Private feedback in the digest", "remove_orders"):
        assert kept in text, kept
    for dropped in ("## 0. Preflight", "## 1. Scope and discovery", "## 2b. Photo fallback",
                    "## 5. Optional approved application", "run_portfolio.py --date"):
        assert dropped not in text, dropped


def test_writer_has_only_read_bash_write_and_inherits_the_model():
    """Fewer tools is the whole saving; a changed model would change copy quality. Write is
    there only for Windows, where a long heredoc command is truncated (2026-09-28)."""
    front = bwa.build().split("---")[1]
    assert 'tools: ["Read", "Bash", "Write", "PowerShell"]' in front
    assert "model: inherit" in front


def test_fixed_flow_writes_renders_and_records_in_one_call():
    text = bwa.build()
    assert f"<<'{bwa.DELIM}'" in text, "quoted heredoc, so $ and backticks in copy stay literal"
    assert "render_report.py" in text and '&& "$PY" scripts/memory.py record' in text and "PY=.venv/Scripts/python" in text
    assert "--applied" in text and "no `--applied`" in text


def test_windows_writes_result_with_the_write_tool_not_a_heredoc():
    text = bwa.build()
    assert "Windows (Platform: win32)" in text and "do NOT use the heredoc" in text
    assert "use the PowerShell tool" in text and "if ($LASTEXITCODE -eq 0)" in text, "no Git Bash: PowerShell path"


def test_writer_hands_back_when_photos_still_need_scoring():
    """Codex audit 2026-09-26: the writer may not rerun the pipeline, so on PHOTO FALLBACK
    REQUIRED it must stop and say so instead of writing from a partial photo set."""
    text = bwa.build()
    assert "PHOTO FALLBACK REQUIRED: <SLUG>" in text and "write nothing" in text


def test_writer_writes_compact_json():
    assert "compactly" in bwa.build()


def test_writer_sees_the_funnel_month_shape():
    """2026-09-26 lean run: 4 of 8 writers sent views_monthly as a list, were rejected by the
    renderer and spent a repeat turn (~90k tokens each). The contract now shows the shape."""
    text = bwa.build()
    assert '"views_monthly":{"Aug":687' in text and "never lists" in text
