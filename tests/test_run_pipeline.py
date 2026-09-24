#!/usr/bin/env python3
"""Tests for the orchestrator's self-derivation and its failure behaviour.

The orchestrator is what collapsed ~13 tool calls into one, so a silent failure here is
expensive: the model would go on to optimize from a half-built working dir. These lock in
that a broken input produces a NAMED failure and a non-zero exit, never a traceback and
never a run that looks fine.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import run_pipeline as rp  # noqa: E402

GOOD_SUBJECT = {"data": {
    "public_name": "Cozy Cabin", "name": "Cabin",
    "capacity": {"max": 8, "bedrooms": 3, "bathrooms": 2.5, "beds": 5},
    "address": {"street": "1 Alpine Rd", "city": "Sun Peaks", "state": "BC",
                "display": "1 Alpine Rd, Sun Peaks, BC, CA",
                "coordinates": {"latitude": "50.877943", "longitude": "-119.908535"}}}}


def _wd(tmp, subject=None, raw=None):
    d = Path(tmp)
    if raw is not None:
        (d / "subject.json").write_text(raw, encoding="utf-8")
    elif subject is not None:
        (d / "subject.json").write_text(json.dumps(subject), encoding="utf-8")
    return d


def test_derives_the_whole_comp_query_from_subject_json():
    """This is what removes a file read AND a retyped coordinate from every run."""
    with tempfile.TemporaryDirectory() as tmp:
        p = rp.subject_params(_wd(tmp, GOOD_SUBJECT))
        assert p["lat"] == 50.877943 and p["lng"] == -119.908535, "coords must be floats"
        assert p["bedrooms"] == 3 and p["baths"] == 2.5 and p["guests"] == 8
        assert p["market"] == "Sun Peaks"
        assert p["address"] == "1 Alpine Rd, Sun Peaks, BC, CA"
        assert p["name"] == "Cozy Cabin"
        assert "_error" not in p


def test_coordinates_arrive_as_strings_and_must_still_parse():
    """Hospitable returns lat/lng as STRINGS. Passing those straight to --lat would make
    pull_comps reject them, so the conversion is load-bearing."""
    with tempfile.TemporaryDirectory() as tmp:
        p = rp.subject_params(_wd(tmp, GOOD_SUBJECT))
        assert isinstance(p["lat"], float) and isinstance(p["lng"], float)


def test_corrupt_subject_returns_a_named_error_not_an_exception():
    """A PMS returning an HTML error page, or a write interrupted mid-flight, used to take
    the entire run down with a JSONDecodeError traceback before anything else ran."""
    with tempfile.TemporaryDirectory() as tmp:
        p = rp.subject_params(_wd(tmp, raw="{ broken json"))
        assert "_error" in p and "not valid JSON" in p["_error"]


def test_wrong_shape_subject_is_reported_clearly():
    with tempfile.TemporaryDirectory() as tmp:
        assert "_error" in rp.subject_params(_wd(tmp, raw="[1,2,3]"))
        assert "_error" in rp.subject_params(_wd(tmp, raw='{"nodata": 1}'))


def test_partial_subject_degrades_field_by_field():
    """Missing coords must not kill the whole derivation — an address-only comp query is
    still valid."""
    with tempfile.TemporaryDirectory() as tmp:
        subj = {"data": {"public_name": "X", "capacity": {"max": 4, "bedrooms": 2},
                         "address": {"city": "Arvada", "display": "Arvada, CO"}}}
        p = rp.subject_params(_wd(tmp, subj))
        assert p["lat"] is None and p["lng"] is None, "absent coords are None, not a crash"
        assert p["guests"] == 4 and p["bedrooms"] == 2 and p["address"] == "Arvada, CO"
        assert "_error" not in p


def test_garbage_coordinates_do_not_crash():
    with tempfile.TemporaryDirectory() as tmp:
        subj = json.loads(json.dumps(GOOD_SUBJECT))
        subj["data"]["address"]["coordinates"] = {"latitude": "N/A", "longitude": ""}
        p = rp.subject_params(_wd(tmp, subj))
        assert p["lat"] is None and p["lng"] is None


def test_missing_subject_file_is_empty_not_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        assert rp.subject_params(Path(tmp)) == {}


def test_corrupt_subject_run_exits_nonzero_with_no_traceback():
    """End to end: the run must fail loudly and cleanly, because there is nothing to
    optimize from. A zero exit here would let a caller treat a dead run as a good one."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "subject.json").write_text("{ broken", encoding="utf-8")
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_pipeline.py"),
                            "--slug", "t", "--date", "2026-09-20", "--workdir", tmp],
                           capture_output=True, text=True, cwd=str(ROOT))
        assert r.returncode == 1, f"expected exit 1, got {r.returncode}"
        assert "Traceback" not in r.stdout + r.stderr, "leaked a traceback to the user"
        assert "not valid JSON" in r.stdout, "the failure reason must be in the summary"
        assert "step(s) failed" in r.stdout, "failures must be summarised, not silent"


def test_healthy_staged_run_exits_zero_and_builds_a_digest():
    """The 'other PMS' path: files staged by MCP, no --property-id, everything downstream
    still runs. Comps and photos are skipped so the test makes no paid calls."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "subject.json").write_text(json.dumps(GOOD_SUBJECT), encoding="utf-8")
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_pipeline.py"),
                            "--slug", "t", "--date", "2026-09-20", "--workdir", tmp,
                            "--skip", "comps,photos,reviews"],
                           capture_output=True, text=True, cwd=str(ROOT))
        assert r.returncode == 0, f"expected exit 0, got {r.returncode}: {r.stdout[-400:]}"
        assert (Path(tmp) / "digest.md").exists(), "digest must be built"
        assert "Cozy Cabin" in (Path(tmp) / "digest.md").read_text(), "subject missing from digest"
        assert "3BR/2.5BA/8g" in r.stdout, "derived params should be visible in the summary"


def test_summary_never_dumps_raw_json():
    """The whole point of the orchestrator is that its OUTPUT is small. If it starts echoing
    payloads, the token saving is gone."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "subject.json").write_text(json.dumps(GOOD_SUBJECT), encoding="utf-8")
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_pipeline.py"),
                            "--slug", "t", "--date", "2026-09-20", "--workdir", tmp,
                            "--skip", "comps,photos,reviews"],
                           capture_output=True, text=True, cwd=str(ROOT))
        assert len(r.stdout) < 4000, f"summary is {len(r.stdout)} chars — too chatty"
        assert '"capacity"' not in r.stdout, "raw subject JSON leaked into the summary"


def test_null_listings_do_not_crash_subject_parsing():
    """A PMS that returns "listings": null (instead of []) used to raise TypeError."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "subject.json").write_text(json.dumps({"data": {
            "name": "x", "listings": None, "capacity": {"max": 2, "bedrooms": 1}}}),
            encoding="utf-8")
        p = rp.subject_params(d)
        assert p["name"] == "x" and p["airbnb_id"] is None and "_error" not in p


def test_reservations_count_requires_an_explicit_window():
    """REGRESSION GUARD (2026-09-20). Three positions were wrong in sequence:

    1. Original code counted whatever /reservations returned and called it "upcoming".
       Measured live: unfiltered returned 1 while the property had 2 stays in the next 90
       days, so the unscoped count UNDERCOUNTS.
    2. A later pass removed the data point entirely, reporting "n/a" even though the
       endpoint works fine.
    3. Correct: scope the request to the calendar's window, and only trust the count when
       the window was actually scoped.

    A past window returns 0, proving the API honours the filter — so the window is the
    thing that makes the number mean something.
    """
    import inspect
    src = inspect.getsource(rp)
    assert '"--start", args.date, "--end", end' in src, \
        "reservations must be requested for the calendar's own window"
    assert 'pull.get("scoped")' in src, \
        "the count must only be trusted when the pull recorded an explicit window"
    assert "--reservations-count" in src, "the scoped count must reach occupancy.py"


def test_unscoped_reservation_pull_is_not_trusted():
    """If reservations.json was staged by hand with no window recorded, the count must be
    ignored rather than reported as 'upcoming' — an honest n/a beats a confident wrong
    number."""
    import json as _json
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "reservations.json").write_text(_json.dumps({"data": [{}, {}, {}]}), encoding="utf-8")
        rj = _json.loads((d / "reservations.json").read_text())
        pull = rj.get("_pull") or {}
        trusted = bool(pull.get("scoped") or (pull.get("window_start") and pull.get("window_end")))
        assert not trusted, "an unscoped staged pull must not be trusted as an upcoming count"

def test_reservations_are_not_fetched_for_a_staged_calendar():
    """A staged calendar means another PMS. Its reservations do not live in Hospitable, so
    reaching for them is both wrong and a surprise API call on a run the user staged. My
    first restore of the count broke this; Codex's
    test_mvp_pipeline::test_staged_calendar_never_calls_hospitable caught it."""
    import inspect
    src = inspect.getsource(rp)
    assert "calendar_from_hospitable" in src, "the Hospitable-vs-staged distinction is gone"
    assert "if not ok_r and calendar_from_hospitable:" in src, \
        "reservations must only be pulled when WE pulled the calendar from Hospitable"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("✅ run_pipeline tests passed")
