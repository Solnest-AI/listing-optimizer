#!/usr/bin/env python3
"""Link this folder to the STR Secrets Connections kit and copy its keys into ./.env.

The kit (github.com/Solnest-AI/str-secrets-connections) is the one place an attendee's keys
live. Its fan-out-env.sh copies them into every skill folder named by a SKILL_PATH_* line in
the kit's .env. This script is the same bridge, driven from this side:

  1. find the kit folder (--kit, $STR_SECRETS_KIT, next to this folder, Desktop, Documents,
     Downloads, OneDrive copies of those, home; 3 levels deep)
  2. write SKILL_PATH_LISTING_OPTIMIZER=<this folder> into the kit's .env (a path, not a
     secret; the kit expects Claude to fill it on summit morning) so the kit's own fan-out
     keeps this folder in sync from now on
  3. merge the kit's keys into ./.env with fan-out-env.sh's rules: only names declared in
     ./.env.example, only non-blank kit values, no existing line removed, HOSPITABLE_TOKEN
     aliased from HOSPITABLE_API_KEY

It never prints a value. Exit 0: every required key is filled. Exit 2: kit found, some
declared keys still blank (setup opens the kit's .env for the attendee). Exit 1: no kit
found (standalone). Exit 3: a kit was found but is not set up yet (no .env); nothing written.

usage: kit_link.py [--kit PATH] [--no-register]
"""
from __future__ import annotations

import argparse
import contextlib
import os
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"
KIT_MARKERS = ("CONNECTIONS.md", "fan-out-env.sh")
SKILL_PATH_VAR = "SKILL_PATH_LISTING_OPTIMIZER"
REQUIRED = ("AIRROI_API_KEY", "GEMINI_API_KEY")
ALIASES = {"HOSPITABLE_TOKEN": "HOSPITABLE_API_KEY"}  # this tool's name <- the kit's name
LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "output", "state", "Library", "AppData"}


def read_env(path: Path) -> dict[str, str]:
    """KEY=VALUE lines, quotes and whitespace stripped, like the kit's lib/env.sh."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for line in lines:
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        val = m.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        values[m.group(1)] = val
    return values


def declared(example: Path) -> list[str]:
    names = []
    for line in example.read_text(encoding="utf-8-sig").splitlines():
        m = LINE_RE.match(line)
        if m and m.group(1) not in names:
            names.append(m.group(1))
    return names


def merge(target: Path, updates: dict[str, str]) -> list[str]:
    """Set each name to its value, in place; append names the file lacks; touch nothing else.
    A duplicated name is set on its LAST line, the one python-dotenv and the kit's env_load
    actually use. Returns the names written. Writes UTF-8 without a BOM and LF endings."""
    lines = target.read_text(encoding="utf-8-sig").splitlines() if target.exists() else []
    last: dict[str, int] = {}
    for i, line in enumerate(lines):
        m = LINE_RE.match(line)
        if m and m.group(1) in updates:
            last[m.group(1)] = i
    out = list(lines)
    for name, i in last.items():
        out[i] = f"{name}={updates[name]}"
    written = list(last)
    for name, value in updates.items():
        if name not in last:
            out.append(f"{name}={value}")
            written.append(name)
    # Atomic: this may be the kit's master key file. Write beside it, then replace in one step,
    # so an interruption can never leave it truncated.
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".lo-env-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(out) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return written


def is_kit(d: Path) -> bool:
    return all((d / m).is_file() for m in KIT_MARKERS)


def is_set_up(d: Path) -> bool:
    """A kit the attendee has run "Set up my connections" in: its .env exists. A fresh unzip
    has only .env.template, and linking to it would create a bogus .env there."""
    return (d / ".env").is_file()


def _rank(d: Path) -> tuple:
    """Best kit first: the one already registered to THIS folder, then any set-up kit (newest
    .env), then bare downloads."""
    env = d / ".env"
    if not env.is_file():
        return (0, 0, 0.0)
    registered = read_env(env).get(SKILL_PATH_VAR, "")
    mine = 0
    with contextlib.suppress(OSError, ValueError):
        mine = int(bool(registered) and Path(registered).resolve() == ROOT.resolve())
    return (1, mine, env.stat().st_mtime)


def _walk(root: Path, depth: int):
    if depth < 0 or not root.is_dir():
        return
    try:
        children = list(root.iterdir())
    except OSError:
        return
    for c in children:
        if c.is_dir() and not c.name.startswith(".") and c.name not in SKIP_DIRS:
            if is_kit(c):
                yield c
            else:
                yield from _walk(c, depth - 1)


def find_kit(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        return p if is_kit(p) else None
    env = os.environ.get("STR_SECRETS_KIT")
    if env and is_kit(Path(env).expanduser()):
        return Path(env).expanduser().resolve()
    home = Path.home()
    roots = [ROOT.parent, home / "Desktop", home / "Documents", home / "Downloads",
             home / "OneDrive" / "Desktop", home / "OneDrive" / "Documents", home]
    found: list[Path] = []
    for r in roots:
        if is_kit(r):
            found.append(r)
        found.extend(_walk(r, 2))
    uniq = []
    for f in found:
        f = f.resolve()
        if f not in uniq:
            uniq.append(f)
    if not uniq:
        return None
    uniq.sort(key=_rank, reverse=True)
    return uniq[0]


def link(kit: Path, register: bool = True) -> tuple[list[str], list[str]]:
    """Returns (names filled from the kit, declared names still blank)."""
    if not ENV.exists():
        ENV.write_text(EXAMPLE.read_text(encoding="utf-8-sig"), encoding="utf-8", newline="\n")
    kit_env = kit / ".env"
    kit_values = read_env(kit_env)
    for ours, theirs in ALIASES.items():
        if not kit_values.get(ours) and kit_values.get(theirs):
            kit_values[ours] = kit_values[theirs]
    names = declared(EXAMPLE)
    updates = {n: kit_values[n] for n in names if kit_values.get(n)}
    # The kit says this attendee is not on Hospitable (they switched PMS): a token left here from
    # before would be probed and fail. Blank it; a blank is not a secret.
    pms = kit_values.get("STACK_PMS", "").strip().lower()
    if pms and pms != "hospitable" and "HOSPITABLE_TOKEN" in names and read_env(ENV).get("HOSPITABLE_TOKEN"):
        updates["HOSPITABLE_TOKEN"] = ""
    filled = [n for n in merge(ENV, updates) if updates[n]] if updates else []
    if register:
        merge(kit_env, {SKILL_PATH_VAR: ROOT.as_posix()})
    ours = read_env(ENV)
    blank = [n for n in names if not ours.get(n)]
    return filled, blank


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kit", help="the STR Secrets Connections folder (found automatically if omitted)")
    ap.add_argument("--no-register", action="store_true", help="do not write this folder's path into the kit's .env")
    args = ap.parse_args(argv)
    kit = find_kit(args.kit)
    if kit is None:
        print("[kit] no STR Secrets Connections folder found (looked next to this folder, on the Desktop, "
              "in Documents and Downloads). Run again with --kit <folder>, or paste keys into .env by hand.")
        return 1
    if not is_set_up(kit):
        # Exit 3, not 1: a kit exists, so this is NOT a standalone install and keys must not be
        # pasted into this folder's .env. Setup stops and sends the attendee to the kit first.
        print(f"[kit] found the kit at {kit}, but it is not set up yet (no .env there). Open that folder "
              "in Claude Code and say \"Set up my connections\" first, then run this setup again.")
        return 3
    filled, blank = link(kit, register=not args.no_register)
    print(f"[kit] {kit}")
    if not args.no_register:
        print(f"[kit] {SKILL_PATH_VAR} set in the kit's .env, so its fan-out keeps this folder in sync")
    print(f"[kit] copied from the kit: {' '.join(filled) if filled else 'nothing new'}")
    optional = [n for n in blank if n not in REQUIRED]
    missing = [n for n in blank if n in REQUIRED]
    if missing:
        print(f"[kit] REQUIRED and still blank here and in the kit: {' '.join(missing)}")
    if optional:
        print(f"[kit] optional, blank: {' '.join(optional)}")
    return 2 if missing else 0


if __name__ == "__main__":
    import console
    console.utf8_stdio()
    raise SystemExit(main())
