"""Digest and cadence lines that told students something false (S2-017, S2-018, S2-038).

- Anna Ohmes, 2026-09-29 (Hostfully): the Hostfully MCP returns no listing summary or
  description, so every run warned "PMS copy differs from live Airbnb ... edits made only in the
  PMS may not be reaching Airbnb". An empty PMS field has nothing to compare.
- Anna, 2026-09-29: RankBreeze review rows have no host-response field (checked live
  2026-10-10: id, room_id, name, tag, rating, comment, date), so every review counted as
  unanswered.
- Ben Pileggi, 2026-09-29: on a first run the refresh schedule said "no record"/"unknown" for
  all 7 items and the console said "0/7 items DUE", which reads like "all up to date".
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_digest as bd
import cadence
from test_live_gallery import GOOD

LIVE = {"title": "Ski-in/out retreat | Hot tub", "summary": "For families seeking winter adventure.",
        "description": "WINTER AT SUN PEAKS. Private hot tub and a games room."}


def _digest(tmp_path, subject, listing=LIVE, reviews=None):
    (tmp_path / "subject.json").write_text(json.dumps({"data": subject}))
    (tmp_path / "live_gallery.json").write_text(json.dumps({**GOOD, "listing": {**GOOD["listing"], **listing}}))
    if reviews is not None:
        (tmp_path / "reviews.json").write_text(json.dumps(reviews))
    return bd.build(tmp_path)


def _line(out, start):
    return next(x for x in out.splitlines() if x.startswith(start))


# ── S2-017: an empty PMS field is not drift ─────────────────────────────
def test_a_pms_with_no_listing_copy_does_not_warn_that_edits_miss_airbnb(tmp_path):
    out = _digest(tmp_path, {"name": "Cabin 4", "public_name": "", "summary": "", "description": None})
    copy = _line(out, "copy_source")
    assert "differs" not in copy and "may not be reaching Airbnb" not in copy
    assert "PMS copy not available to compare: title, summary, description" in copy


def test_only_the_fields_the_pms_left_empty_are_skipped(tmp_path):
    out = _digest(tmp_path, {"name": "Cabin 4", "public_name": "Ski-in/out retreat | Hot tub", "summary": "",
                             "description": "Private hot tub."})
    copy = _line(out, "copy_source")
    assert "PMS copy differs from live Airbnb: description" in copy, "real drift must still be reported"
    assert "not available to compare: summary" in copy


# ── S2-018: a review source without host responses ─────────────────────
def _rankbreeze_reviews(flag=True):
    rows = [{"reviewed_at": "2026-09-21", "platform": "airbnb", "public": {"rating": 5, "review": "Great place."}},
            {"reviewed_at": "2026-08-15", "platform": "airbnb", "public": {"rating": 5, "review": "Clean."}}]
    pull = {"total_available": 22, "source": "RankBreeze"}
    if flag:
        pull["responses_known"] = False
    return {"data": rows, "_pull": pull}


def test_rankbreeze_reviews_show_unanswered_as_unknown(tmp_path):
    out = _digest(tmp_path, {"name": "x", "public_name": "T", "summary": "S"}, reviews=_rankbreeze_reviews())
    line = _line(out, "unanswered_reviews")
    assert "unknown" in line and ": 2" not in line
    assert "2 of the last 2 unanswered" not in out, "the NOTE example must not quote a count either"


def test_rows_with_no_response_field_at_all_are_unknown_even_without_the_flag(tmp_path):
    out = _digest(tmp_path, {"name": "x", "public_name": "T", "summary": "S"}, reviews=_rankbreeze_reviews(flag=False))
    assert "unknown" in _line(out, "unanswered_reviews")


def test_hospitable_reviews_still_count_unanswered(tmp_path):
    rows = [{"reviewed_at": "2026-09-21", "responded_at": None, "public": {"rating": 5, "review": "a"}},
            {"reviewed_at": "2026-08-15", "responded_at": "2026-08-16", "public": {"rating": 5, "review": "b"}}]
    out = _digest(tmp_path, {"name": "x", "public_name": "T", "summary": "S"},
                  reviews={"data": rows, "_pull": {"total_available": 2}})
    assert _line(out, "unanswered_reviews").endswith(": 1")


# ── S2-038: the refresh schedule on a first run ─────────────────────────
def test_a_first_run_says_not_tracked_yet_instead_of_no_record(tmp_path, monkeypatch):
    monkeypatch.setattr(cadence, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(cadence, "HISTORY_PATH", tmp_path / "history.jsonl")
    rows = {r["item"]: r for r in cadence.check("cabin", date(2026, 10, 10))}
    reshoot = rows["Full reshoot"]
    assert reshoot["status"] == "not tracked" and reshoot["last"] == "not tracked yet"
    assert "apply" in reshoot["due"], "the host is told when tracking starts"
    assert "no record" not in json.dumps(rows) and "unknown" not in json.dumps(rows)


def test_this_run_counts_as_the_conversion_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(cadence, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(cadence, "HISTORY_PATH", tmp_path / "history.jsonl")
    audit = next(r for r in cadence.check("cabin", date(2026, 10, 10)) if r["item"] == "Conversion audit")
    assert audit == {"item": "Conversion audit", "last": "2026-10-10 (this run)", "due": "2026-11-09", "status": "ok"}


def test_the_console_never_says_zero_due_when_nothing_is_tracked(tmp_path):
    env_state = tmp_path / "state"
    script = Path(__file__).resolve().parent.parent / "scripts" / "cadence.py"
    code = (f"import sys, runpy, pathlib; sys.argv = {['cadence.py', '--listing', 'cabin', '--check', '--today', '2026-10-10', '--out', str(tmp_path / 'c.json')]!r}; "
            f"sys.path.insert(0, {str(script.parent)!r}); import cadence; "
            f"cadence.STATE_PATH = pathlib.Path({str(env_state / 'r.json')!r}); "
            f"cadence.HISTORY_PATH = pathlib.Path({str(env_state / 'h.jsonl')!r}); cadence.main()")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert "0/7" not in out and "not tracked yet" in out, out
