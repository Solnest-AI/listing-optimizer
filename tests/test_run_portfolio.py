"""Portfolio runs: every listed property, stable slugs, no single failure stopping the run."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import claude_usage as cu
import run_portfolio as rp


def test_slugs_come_from_the_internal_name_without_accents_or_the():
    assert rp.slugify("The Après Arcade") == "apres-arcade"
    assert rp.slugify("The Urban Nest") == "urban-nest"
    assert rp.slugify("Boho Bliss") == "boho-bliss"


def test_config_pin_beats_memory_and_new_slugs_never_collide():
    props = [{"id": "p1", "name": "The Farm House"}, {"id": "p2", "name": "Lakehouse"},
             {"id": "p3", "name": "Lakehouse"}, {"id": "p4", "name": "Boho Bliss"}]
    known = {"p1": "farm-house", "p4": "boho-bliss"}
    config = {"farmhouse-at-sixty-six": {"property_id": "p1"}}
    slugs = rp.assign_slugs(props, known, config)
    assert slugs == {"p1": "farmhouse-at-sixty-six", "p2": "lakehouse", "p3": "lakehouse-2", "p4": "boho-bliss"}


def _fake_env(tmp_path, monkeypatch, outcomes):
    """outcomes: slug -> (exit code, status, extra files) the fake pipeline writes."""
    monkeypatch.setattr(rp, "ROOT", tmp_path)
    monkeypatch.setattr(rp, "SLUGS", tmp_path / "state/slugs.json")
    monkeypatch.setattr(rp, "CONFIG", tmp_path / "config/properties.json")
    props = [{"id": f"id-{s}", "name": s.replace("-", " "), "listed": True} for s in outcomes]
    props.append({"id": "id-draft", "name": "Cottage draft", "listed": False})
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        if "properties" in cmd:
            out = Path(cmd[cmd.index("--out") + 1])
            out.write_text(json.dumps({"data": props}))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        slug = cmd[cmd.index("--slug") + 1]
        code, status, extra = outcomes[slug]
        wd = tmp_path / "output/2026-09-26" / slug
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "pipeline_status.json").write_text(json.dumps({"status": status, "steps": []}))
        (wd / "digest.md").write_text("digest")
        (wd / "comps.json").write_text(json.dumps({"fetch": {"calls": 1}}))
        usage = {"api_calls": 5, "totalTokenCount": 100, **extra.get("usage", {})}
        (wd / "photo_scores.json").write_text(json.dumps({"usage": usage, "scored_count": 20}))
        if extra.get("fallback"):
            (wd / "photo_fallback.json").write_text("{}")
        return SimpleNamespace(returncode=code, stdout="ok", stderr="")
    return runner, calls


def _args(**kw):
    base = {"date": "2026-09-26", "only": "", "resume": False, "refresh": False, "no_cache": False,
            "include_unlisted": False, "summary": False}
    return SimpleNamespace(**{**base, **kw})


def test_one_failure_does_not_stop_the_portfolio(tmp_path, monkeypatch, capsys):
    runner, calls = _fake_env(tmp_path, monkeypatch, {
        "a-house": (0, "ready", {}), "b-house": (1, "failed", {}), "c-house": (0, "degraded", {})})
    report = rp.run(_args(), runner)
    by = {r["slug"]: r for r in report["listings"]}
    assert set(by) == {"a-house", "b-house", "c-house"}, "the unlisted draft is skipped"
    assert by["a-house"]["ready_to_write"] and by["c-house"]["ready_to_write"]
    assert not by["b-house"]["ready_to_write"]
    assert report["totals"] == {"airroi_calls": 3, "gemini_requests": 15, "gemini_tokens": 300}
    assert json.loads((tmp_path / "state/slugs.json").read_text(encoding="utf-8"))["id-a-house"] == "a-house"
    assert "WRITER QUEUE (2 of 3)" in capsys.readouterr().out


def test_gemini_daily_quota_stops_new_listings_and_holds_the_hit_one(tmp_path, monkeypatch):
    runner, calls = _fake_env(tmp_path, monkeypatch, {
        "a-house": (0, "ready", {"usage": {"quota_exhausted": "daily"}, "fallback": True}),
        "b-house": (0, "ready", {})})
    report = rp.run(_args(), runner)
    by = {r["slug"]: r for r in report["listings"]}
    assert not by["a-house"]["ready_to_write"] and by["a-house"]["photos_pending"]
    assert by["b-house"]["action"].startswith("not started")
    assert sum("run_pipeline.py" in " ".join(c) for c in calls) == 1
    assert "daily quota" in report["stopped"]


def test_resume_skips_listings_already_gathered(tmp_path, monkeypatch):
    runner, calls = _fake_env(tmp_path, monkeypatch, {"a-house": (0, "ready", {}), "b-house": (0, "ready", {})})
    rp.run(_args(), runner)
    calls.clear()
    report = rp.run(_args(resume=True), runner)
    assert not any("run_pipeline.py" in " ".join(c) for c in calls)
    assert all(r["action"].startswith("resumed") for r in report["listings"])


def test_claude_writer_tokens_are_read_once_per_message(tmp_path):
    cwd = Path("/Users/a_b/My Repo")
    assert cu.project_dir(cwd, tmp_path) == tmp_path / ".claude/projects/-Users-a-b-My-Repo"
    sub = cu.project_dir(cwd, tmp_path) / "session-1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-x.meta.json").write_text(json.dumps({"agentType": "listing-writer"}))
    usage = {"input_tokens": 1, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 0, "output_tokens": 9}
    rows = [{"type": "user", "message": {"content": "Write output/2026-09-26/a-house (slug a-house)"}},
            {"type": "assistant", "message": {"id": "m1", "usage": usage, "content": []}},
            {"type": "assistant", "message": {"id": "m1", "usage": usage, "content": []}},
            {"type": "assistant", "message": {"id": "m2", "usage": usage, "content": []}}]
    (sub / "agent-x.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    (sub / "agent-y.meta.json").write_text(json.dumps({"agentType": "general-purpose"}))
    (sub / "agent-y.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    got = cu.writer_usage(["a-house", "b-house"], "2026-09-26", cwd, tmp_path)
    assert got == {"a-house": {"agents": 1, "turns": 2, "total_tokens": 220, "peak_context": 110}}


def test_queue_says_when_owner_facts_are_missing_from_an_older_digest(tmp_path, monkeypatch, capsys):
    runner, _ = _fake_env(tmp_path, monkeypatch, {"a-house": (0, "ready", {})})
    (tmp_path / "config").mkdir()
    (tmp_path / "config/properties.json").write_text(json.dumps({"a-house": {"owner_facts": ["Hot tub is private"]}}))
    rp.run(_args(), runner)
    assert "1 in config but NOT in this digest" in capsys.readouterr().out


def test_a_partial_run_keeps_the_rest_of_the_days_portfolio(tmp_path, monkeypatch):
    runner, _ = _fake_env(tmp_path, monkeypatch, {"a-house": (0, "ready", {}), "b-house": (0, "ready", {})})
    rp.run(_args(), runner)
    report = rp.run(_args(only="b-house"), runner)
    assert [r["slug"] for r in report["listings"]] == ["a-house", "b-house"]
    assert report["totals"]["airroi_calls"] == 2


def test_a_shared_slug_never_mixes_two_properties():
    """Codex review: two property ids pinned or remembered to one slug shared a workdir."""
    props = [{"id": "p1", "name": "Cabin"}, {"id": "p2", "name": "Lodge"}]
    slugs = rp.assign_slugs(props, {"p1": "cabin", "p2": "cabin"}, {})
    assert slugs["p1"] == "cabin" and slugs["p2"] != "cabin"
    assert len(set(slugs.values())) == 2


def test_a_config_pin_wins_over_a_remembered_slug():
    """Codex review: a remembered slug on an earlier property stole a later property's pin."""
    props = [{"id": "p1", "name": "Old"}, {"id": "p2", "name": "New"}]
    slugs = rp.assign_slugs(props, {"p1": "cabin"}, {"cabin": {"property_id": "p2"}})
    assert slugs["p2"] == "cabin" and slugs["p1"] != "cabin"
