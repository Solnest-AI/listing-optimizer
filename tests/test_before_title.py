"""The report's "Before" title is what guests see, never the PMS's internal name (S2-021).

Bert Perez (KeysCations, Lodgify), 2026-09-29: the "before" title came from the PMS property
name instead of the live Airbnb title. current.title was left to the writer, and the digest
shows both the PMS `name` and `public_name`.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import render_report as rr

LIVE_TITLE = "Oceanfront Keys escape | Pool & kayaks"
SUBJECT = {"data": {"name": "KC-04 Duck Key (internal)", "public_name": "Keys escape with pool"}}


def _wd(tmp_path, subject=SUBJECT, live=True):
    (tmp_path / "subject.json").write_text(json.dumps(subject))
    if live:
        (tmp_path / "live_gallery.json").write_text(json.dumps({
            "provider": "airbnb", "room_id": "1", "photos": [],
            "listing": {"title": LIVE_TITLE, "summary": "", "description": "", "amenities": []}}))
    return tmp_path


def test_before_title_is_the_live_airbnb_title_even_if_the_writer_used_the_pms_name(tmp_path):
    data = {"current": {"title": "KC-04 Duck Key (internal)"}}
    rr.fill_current_title(data, _wd(tmp_path))
    assert data["current"]["title"] == LIVE_TITLE


def test_without_a_live_read_the_public_name_is_used(tmp_path):
    data = {"current": {"title": "KC-04 Duck Key (internal)"}}
    rr.fill_current_title(data, _wd(tmp_path, live=False))
    assert data["current"]["title"] == "Keys escape with pool"


def test_the_internal_name_is_never_shown_as_the_before_title(tmp_path):
    data = {"current": {"title": "KC-04 Duck Key (internal)"}}
    rr.fill_current_title(data, _wd(tmp_path, subject={"data": {"name": "KC-04 Duck Key (internal)"}}, live=False))
    assert "title" not in data["current"], "no public title known: show no Before line rather than the wrong one"


def test_a_writer_title_is_kept_when_nothing_on_disk_says_otherwise(tmp_path):
    data = {"current": {"title": "Keys escape with pool"}}
    rr.fill_current_title(data, _wd(tmp_path, subject={"data": {"name": "KC-04"}}, live=False))
    assert data["current"]["title"] == "Keys escape with pool"


def test_an_excluded_live_read_is_not_used(tmp_path):
    wd = _wd(tmp_path)
    (wd / "pipeline_status.json").write_text(json.dumps({"status": "degraded", "steps": [],
                                                         "excluded_files": ["live_gallery.json"]}))
    data = {"current": {"title": ""}}
    rr.fill_current_title(data, wd)
    assert data["current"]["title"] == "Keys escape with pool"
