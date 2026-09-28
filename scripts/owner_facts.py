#!/usr/bin/env python3
"""owner_facts.py — save a host's answers as owner facts for every later run.

The report's "Confirm with the host" questions are gaps the writer refused to guess (drive
times, who lives on site, cameras). Once the host answers, save the answer here: the next
digest shows it under OWNER NOTES and the writer uses it, so no member is asked twice.

config/properties.json is gitignored and per-install. This keeps every other entry and key,
refuses to touch a file it cannot parse, keeps the previous version as .bak (one-step undo)
and writes atomically.

usage: owner_facts.py add --slug <SLUG> "Powder King is about a 2-hour drive" ["..."]
       owner_facts.py list --slug <SLUG>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config" / "properties.json"
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))   # a bad file raises; never overwrite it
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object; fix it by hand before adding facts")
    return data


def add(slug: str, facts: list[str], path: Path = CONFIG) -> list[str]:
    """Append new facts to the listing's owner_facts. Returns the ones actually added."""
    if not SLUG_RE.fullmatch(slug):
        raise ValueError(f"invalid listing slug: {slug!r}")
    data = _load(path)
    entry = data.setdefault(slug, {})
    if not isinstance(entry, dict):
        raise ValueError(f"config entry for {slug} is not an object")
    current = entry.setdefault("owner_facts", [])
    if not isinstance(current, list):
        raise ValueError(f"owner_facts for {slug} is not a list")
    seen = {" ".join(str(f).split()).lower() for f in current}
    added = []
    for fact in facts:
        clean = " ".join(fact.split())
        if clean and clean.lower() not in seen:
            current.append(clean)
            seen.add(clean.lower())
            added.append(clean)
    if not added:
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".properties.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return added


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Save host answers as owner facts for later runs.")
    ap.add_argument("action", choices=["add", "list"])
    ap.add_argument("--slug", required=True)
    ap.add_argument("facts", nargs="*")
    args = ap.parse_args(argv)
    try:
        if args.action == "list":
            entry = _load(CONFIG).get(args.slug) or {}
            for fact in entry.get("owner_facts") or []:
                print(f"- {fact}")
            return 0
        if not args.facts:
            ap.error("add needs at least one fact")
        added = add(args.slug, args.facts)
    except (OSError, ValueError) as e:
        print(f"[owner_facts] {e}", file=sys.stderr)
        return 1
    print(f"[owner_facts] {args.slug}: added {len(added)}, skipped {len(args.facts) - len(added)} already saved")
    return 0


if __name__ == "__main__":
    import console
    console.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
