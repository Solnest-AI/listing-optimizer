"""Regression checks at the CLI/subprocess boundary; no vendor calls."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_pipeline as rp


def stage(path, name, data):
    (path / name).write_text(json.dumps(data), encoding="utf-8")


def subject(bedrooms=2):
    return {"data": {"name": "Test cabin", "capacity": {"bedrooms": bedrooms,
            "bathrooms": 1, "max": 4}, "address": {"city": "Test town"}}}


def invoke(monkeypatch, tmp_path, *extra):
    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "--slug", "test-cabin",
        "--date", "2026-09-20", "--workdir", str(tmp_path), *extra])
    with pytest.raises(SystemExit) as e:
        rp.main()
    return e.value.code


def test_empty_workdir_cannot_claim_success(monkeypatch, tmp_path):
    assert invoke(monkeypatch, tmp_path, "--skip", "photos,comps,memory") == 1


@pytest.mark.parametrize("args", [("--date", "2026-99-99"), ("--slug", "../escape"),
    ("--photo-limit", "0"), ("--skip", "typo")])
def test_bad_arguments_stop_before_gathering(monkeypatch, tmp_path, args):
    calls = []
    monkeypatch.setattr(rp, "run", lambda *a: calls.append(a) or (True, ""))
    assert invoke(monkeypatch, tmp_path, *args) == 2
    assert not calls


def test_staged_calendar_never_calls_hospitable(monkeypatch, tmp_path):
    stage(tmp_path, "subject.json", subject())
    stage(tmp_path, "calendar.json", {"data": {"days": [
        {"date": "2026-09-20", "status": {"available": True, "reason": "AVAILABLE"}}]}})
    real_run = rp.run
    def local_only(cmd, label):
        assert "hospitable_api.py" not in str(cmd), "a staged PMS run made a Hospitable call"
        return real_run(cmd, label)
    monkeypatch.setattr(rp, "run", local_only)
    assert invoke(monkeypatch, tmp_path, "--skip", "photos,comps,memory") == 0
    occ = json.loads((tmp_path / "occupancy.json").read_text())
    assert occ["report_block"]["source"] == "Staged PMS calendar"
    assert occ["report_block"]["upcoming_reservations"] == "n/a"


def test_no_cache_and_studio_reach_paid_steps(monkeypatch, tmp_path):
    stage(tmp_path, "subject.json", subject(bedrooms=0))
    stage(tmp_path, "images.json", {"data": [{"order": 0, "url": "https://x/1.jpg"}]})
    stage(tmp_path, "comps.json", {"top_comps": [{"name": "stale"}]})
    stage(tmp_path, "photo_scores.json", {"hero": 99})
    calls = []
    real_run = rp.run
    def vendor_boundary(cmd, label):
        if label in ("photos", "comps"):
            calls.append((label, [str(x) for x in cmd]))
            return False, "vendor offline"
        return real_run(cmd, label)
    monkeypatch.setattr(rp, "run", vendor_boundary)
    assert invoke(monkeypatch, tmp_path, "--no-cache", "--skip", "memory,calendar,reviews") == 0
    assert {label for label, _ in calls} == {"comps", "photos"}
    assert all("--no-cache" in cmd for _, cmd in calls)
    manifest = json.loads((tmp_path / "pipeline_status.json").read_text())
    assert manifest["status"] == "degraded"
    assert {"comps.json", "photo_scores.json"} <= set(manifest["excluded_files"])
    assert "stale" not in (tmp_path / "digest.md").read_text()


def test_failed_refresh_cannot_optimize_old_subject(monkeypatch, tmp_path):
    stage(tmp_path, "subject.json", subject())
    stage(tmp_path, "comps.json", {"top_comps": [{"name": "old comp"}]})
    labels = []
    def offline(cmd, label):
        labels.append(label)
        return False, "HTTP 401"
    monkeypatch.setattr(rp, "run", offline)
    assert invoke(monkeypatch, tmp_path, "--refresh", "--property-id", "test-id") == 1
    assert "photos" not in labels and "comps" not in labels
    state = json.loads((tmp_path / "pipeline_status.json").read_text())
    assert state["status"] == "failed"
    assert "subject.json" in state["excluded_files"]
    # A plain retry must retain the invalidation, even if the old file is valid JSON.
    labels.clear()
    assert invoke(monkeypatch, tmp_path, "--skip", "photos,comps,memory") == 1
    assert "digest" not in labels


def test_child_timeout_is_a_named_failure(monkeypatch):
    def timeout(*a, **kw):
        assert kw.get("timeout", 0) > 0
        raise subprocess.TimeoutExpired("worker", kw["timeout"])
    monkeypatch.setattr(rp.subprocess, "run", timeout)
    ok, detail = rp.run(["worker"], "photos")
    assert not ok and "timed out" in detail.lower()
