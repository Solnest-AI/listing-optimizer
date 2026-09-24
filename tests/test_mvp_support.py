"""Data integrity and usable deliverables, including concurrent portfolio runs."""
import concurrent.futures
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_digest as bd  # noqa: E402
import memory  # noqa: E402
import occupancy as occ  # noqa: E402
import render_report as rr  # noqa: E402


def test_long_description_cannot_hide_structural_facts(tmp_path):
    (tmp_path / "subject.json").write_text(json.dumps({"data": {
        "name": "Cabin", "description": "word " * 3000,
        "amenities": ["sauna"], "capacity": {"max": 4},
        "room_details": ["loft"], "house_rules": ["No pets"]}}))
    digest = bd.build(tmp_path)
    assert "sauna" in digest and "No pets" in digest and "loft" in digest
    assert '"max": 4' in digest
    subject_line = digest.split("# SUBJECT\n")[1].splitlines()[0]
    assert json.loads(subject_line)["name"] == "Cabin"


def test_missing_calendar_status_is_unknown():
    result = occ.compute([{"date": "2026-09-21"}])["forward_window"]
    assert result["occupancy_pct"] is None
    assert result["booked"] == 0 and result["unknown"] == 1


def test_duplicate_calendar_dates_do_not_inflate_occupancy():
    booked = {"date": "2026-09-21", "status": {"available": False, "reason": "Airbnb"}}
    open_day = {"date": "2026-09-22", "status": {"available": True}}
    result = occ.compute([booked, booked, open_day])["forward_window"]
    assert result["occupancy_pct"] == 50.0 and result["days"] == 2


def test_conflicting_calendar_dates_are_not_confident_occupancy():
    result = occ.compute([
        {"date": "2026-09-21", "status": {"available": False, "reason": "Airbnb"}},
        {"date": "2026-09-21", "status": {"available": True}}])["forward_window"]
    assert result["occupancy_pct"] is None and result["unknown"] == 1


def test_different_years_do_not_overwrite_months():
    result = occ.compute([{"date": d, "status": {"available": True}}
                          for d in ("2026-09-21", "2027-09-20")])
    assert len(result["monthly"]) == 2


def test_parallel_history_updates_retain_every_listing(monkeypatch, tmp_path):
    path = tmp_path / "history.jsonl"
    real_write = memory._atomic_write_lines
    def slow_write(*args):
        time.sleep(0.025)  # widen the real read/replace race
        return real_write(*args)
    monkeypatch.setattr(memory, "_atomic_write_lines", slow_write)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: memory.append_local({"listing_slug": f"listing-{i}",
            "run_date": "2026-09-20"}, path), range(8)))
    assert {json.loads(line)["listing_slug"] for line in path.read_text().splitlines()} == {
        f"listing-{i}" for i in range(8)}


def test_parallel_cache_updates_retain_paid_results(monkeypatch, tmp_path):
    import cache
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.delenv("LO_NO_CACHE", raising=False)
    real_save = cache._save
    def slow_save(*args):
        time.sleep(0.025)
        return real_save(*args)
    monkeypatch.setattr(cache, "_save", slow_save)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: cache.put_many("photos", {str(i): {"avg": i}}), range(8)))
    assert len(cache.get_many("photos", [str(i) for i in range(8)], 1)) == 8


def report_data():
    return {"listing": {"name": "Cabin", "slug": "cabin"}, "run_date": "2026-09-20",
            "optimized": {"title": "Cabin with sauna", "summary": "Relax in the sauna.",
                          "the_space": "A quiet cabin with room to unwind."}}


@pytest.mark.parametrize("optimized", [{}, {"title": "x"*51, "summary": "ok", "the_space": "ok"},
    {"title": "ok", "summary": "x"*501, "the_space": "ok"}])
def test_invalid_copy_does_not_create_deliverables(tmp_path, optimized):
    data = report_data()
    data["optimized"] = optimized
    (tmp_path / "result.json").write_text(json.dumps(data))
    run = subprocess.run([sys.executable, str(ROOT / "scripts/render_report.py"),
        "--data", str(tmp_path / "result.json"), "--listing-slug", "cabin",
        "--date", "2026-09-20", "--out-base", str(tmp_path / "reports")],
        capture_output=True, text=True)
    assert run.returncode != 0
    assert not (tmp_path / "reports/cabin/2026-09-20/report.html").exists()


def test_photo_failures_are_visible_in_both_reports():
    data = report_data()
    data["photos"] = {"coverage_note": "ranked 1 of 30 photos; 29 FAILED",
        "unranked": [5, 6], "recommended_top5_order": [], "hero": 0}
    env = Environment(loader=FileSystemLoader(str(rr.TEMPLATES_DIR)))
    data["branding"] = json.loads((ROOT / "branding.example.json").read_text())
    for name in ("report.html.j2", "report.md.j2"):
        result = env.get_template(name).render(data=data)
        assert "29 FAILED" in result and "Unranked" in result


def test_unknown_occupancy_is_labelled_without_a_fake_percentage():
    data = report_data()
    data["occupancy"] = {"source": "Staged PMS calendar", "forward_days": 3,
        "forward_pct": None, "unknown_days": 1, "upcoming_reservations": "n/a"}
    data["branding"] = json.loads((ROOT / "branding.example.json").read_text())
    env = Environment(loader=FileSystemLoader(str(rr.TEMPLATES_DIR)))
    for name in ("report.html.j2", "report.md.j2"):
        result = env.get_template(name).render(data=data)
        assert "None%" not in result
        assert "Staged PMS calendar" in result and "unknown" in result.lower()


def test_rendered_copy_and_machine_notes_follow_punctuation_rule(tmp_path):
    data = report_data()
    data["optimized"]["summary"] = "Relax\u2014unwind."
    data["photos"] = {"hero": 0, "gaps": ["No map\u2014create one."]}
    (tmp_path / "result.json").write_text(json.dumps(data))
    subprocess.run([sys.executable, str(ROOT / "scripts/render_report.py"),
        "--data", str(tmp_path / "result.json"), "--listing-slug", "cabin",
        "--date", "2026-09-20", "--out-base", str(tmp_path / "reports")], check=True,
        capture_output=True, text=True)
    for name in ("report.html", "report.md", "paste-block.txt"):
        assert "\u2014" not in (tmp_path / "reports/cabin/2026-09-20" / name).read_text()


def test_failed_artifacts_cannot_reenter_a_report(tmp_path):
    (tmp_path / "pipeline_status.json").write_text(json.dumps({"status": "degraded",
        "excluded_files": ["photo_scores.json"], "steps": []}))
    (tmp_path / "photo_scores.json").write_text(json.dumps({"hero": 99}))
    data = report_data()
    data["photos"] = {"hero": 88}
    rr.merge_machine_blocks(data, tmp_path)
    assert "photos" not in data


@pytest.mark.parametrize("mismatch", [{"listing_slug": "other-cabin"}, {"run_date": "2026-09-19"}])
def test_report_rejects_another_listing_or_dates_workdir(tmp_path, mismatch):
    state = {"status": "ready", "excluded_files": [], "steps": [],
             "listing_slug": "cabin", "run_date": "2026-09-20", **mismatch}
    (tmp_path / "pipeline_status.json").write_text(json.dumps(state))
    (tmp_path / "result.json").write_text(json.dumps(report_data()))
    (tmp_path / "occupancy.json").write_text(json.dumps({
        "report_block": {"source": "Other listing calendar", "forward_pct": 90}}))
    run = subprocess.run([sys.executable, str(ROOT / "scripts/render_report.py"),
        "--data", str(tmp_path / "result.json"), "--workdir", str(tmp_path),
        "--listing-slug", "cabin", "--date", "2026-09-20",
        "--out-base", str(tmp_path / "reports")], capture_output=True, text=True)
    assert run.returncode != 0 and "match" in run.stderr
    assert not (tmp_path / "reports").exists()


@pytest.mark.parametrize("invalid", [{"status": "failed"}, {"status": "running"},
                                    {"listing_slug": "other-cabin"}])
def test_history_record_rejects_invalid_or_mismatched_run(tmp_path, invalid):
    state = {"status": "ready", "excluded_files": [], "steps": [],
             "listing_slug": "cabin", "run_date": "2026-09-20", **invalid}
    data = report_data()
    data["photos"] = {"hero": 99}
    (tmp_path / "pipeline_status.json").write_text(json.dumps(state))
    (tmp_path / "result.json").write_text(json.dumps(data))
    run = subprocess.run([sys.executable, str(ROOT / "scripts/memory.py"), "record",
        "--result", str(tmp_path / "result.json"), "--history", str(tmp_path / "history.jsonl"),
        "--out", str(tmp_path / "record.json")], capture_output=True, text=True)
    assert run.returncode != 0
    assert not (tmp_path / "history.jsonl").exists()
    assert not (tmp_path / "record.json").exists()


@pytest.mark.parametrize("declared", [None, 999])
def test_history_derives_known_summary_length(declared):
    data = report_data()
    if declared is not None:
        data["optimized"]["summary_char_count"] = declared
    assert memory.summarize(data)["summary_char_count"] == len(data["optimized"]["summary"])


def test_review_channel_gaps_are_limited_to_account_and_sample(tmp_path):
    (tmp_path / "subject.json").write_text(json.dumps({"data": {"name": "Cabin"}}))
    (tmp_path / "channels.json").write_text(json.dumps({"silent_channels": ["homeaway"]}))
    (tmp_path / "reviews.json").write_text(json.dumps({"data": [{"platform": "airbnb"}]}))
    digest = bd.build(tmp_path)
    assert "account-level" in digest and "retrieved review sample" in digest
    assert "never reach" not in digest and "these channels take bookings" not in digest


def test_competitor_titles_are_evidence_and_stay_verbatim(tmp_path):
    """The em-dash rule applies to the agent's copy, never to evidence pulled from disk.
    A second normalize pass after the merge used to rewrite 'Cabin — Hot Tub' as
    'Cabin. Hot Tub' in the rendered report."""
    (tmp_path / "pipeline_status.json").write_text(json.dumps({"status": "ready",
        "excluded_files": [], "steps": [], "listing_slug": "cabin", "run_date": "2026-09-20"}))
    (tmp_path / "comps.json").write_text(json.dumps({"comp_count": 1, "top_comps": [
        {"name": "Lakeview Cabin — Hot Tub", "airbnb_url": "https://a", "bedrooms": 2,
         "baths": 1, "guests": 4, "ratings": {"num_reviews": 1, "rating_overall": 5},
         "performance": {}}]}), encoding="utf-8")
    data = report_data()
    data["optimized"]["summary"] = "Relax—unwind."
    (tmp_path / "result.json").write_text(json.dumps(data))
    subprocess.run([sys.executable, str(ROOT / "scripts/render_report.py"),
        "--data", str(tmp_path / "result.json"), "--workdir", str(tmp_path),
        "--listing-slug", "cabin", "--date", "2026-09-20",
        "--out-base", str(tmp_path / "reports")], check=True, capture_output=True, text=True)
    md = (tmp_path / "reports/cabin/2026-09-20/report.md").read_text()
    assert "Lakeview Cabin — Hot Tub" in md, "competitor title was rewritten"
    assert "Relax. unwind." in md, "agent copy must still follow the rule"


def test_digest_survives_a_corrupt_status_file(tmp_path):
    """build_digest.py is rerun by hand after an optional funnel pull; a corrupt
    pipeline_status.json must degrade, not traceback."""
    (tmp_path / "subject.json").write_text(json.dumps({"data": {"name": "x"}}))
    (tmp_path / "pipeline_status.json").write_text("{not json")
    assert "# SUBJECT" in bd.build(tmp_path)


def test_cadence_survives_a_corrupt_state_file(tmp_path, monkeypatch):
    import datetime

    import cadence
    monkeypatch.setattr(cadence, "STATE_PATH", tmp_path / "refresh_state.json")
    cadence.STATE_PATH.write_text("{oops")
    rows = cadence.check("x", datetime.date(2026, 9, 21))
    assert rows and all(r["status"] == "DUE" for r in rows)
