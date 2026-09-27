#!/usr/bin/env python3
"""owner_facts.py: a host's answer to a "Confirm with the host" question is saved once and
reaches every later digest, so no member is asked the same question twice."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import owner_facts as of


def test_add_creates_the_file_and_entry(tmp_path):
    path = tmp_path / "config" / "properties.json"
    assert of.add("lake-cabin", ["The airport is a 25-min drive"], path) == ["The airport is a 25-min drive"]
    assert json.loads(path.read_text())["lake-cabin"]["owner_facts"] == ["The airport is a 25-min drive"]


def test_add_keeps_other_entries_skips_repeats_and_keeps_a_backup(tmp_path):
    path = tmp_path / "properties.json"
    before = {"_comment": "local", "other": {"season": "summer"},
              "lake-cabin": {"season": "winter", "owner_facts": ["No A/C"]}}
    path.write_text(json.dumps(before))
    added = of.add("lake-cabin", ["no  a/c", "Owners live upstairs", "Owners live upstairs"], path)
    assert added == ["Owners live upstairs"]
    after = json.loads(path.read_text())
    assert after["other"] == {"season": "summer"} and after["_comment"] == "local"
    assert after["lake-cabin"] == {"season": "winter", "owner_facts": ["No A/C", "Owners live upstairs"]}
    assert json.loads((tmp_path / "properties.json.bak").read_text()) == before


def test_nothing_new_writes_nothing(tmp_path):
    path = tmp_path / "properties.json"
    path.write_text(json.dumps({"x": {"owner_facts": ["A"]}}))
    assert of.add("x", ["a"], path) == []
    assert not (tmp_path / "properties.json.bak").exists()


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"x": []}', '{"x": {"owner_facts": "A"}}'])
def test_a_file_it_cannot_read_safely_is_never_overwritten(tmp_path, content):
    path = tmp_path / "properties.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        of.add("x", ["fact"], path)
    assert path.read_text() == content


def test_bad_slug_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="slug"):
        of.add("../etc", ["fact"], tmp_path / "properties.json")


def test_saved_facts_reach_the_digest(tmp_path, monkeypatch):
    import build_digest as bd
    path = tmp_path / "properties.json"
    of.add("lake-cabin", ["Powder King is about a 2-hour drive"], path)
    monkeypatch.setattr(bd, "CONFIG", path)
    assert "- Powder King is about a 2-hour drive" in bd._owner_notes("lake-cabin")
