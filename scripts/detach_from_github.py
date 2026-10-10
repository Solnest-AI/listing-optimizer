#!/usr/bin/env python3
"""
detach_from_github.py — make a student's copy a plain folder with no link back to GitHub.

  .venv/bin/python scripts/detach_from_github.py        (setup runs it; always exits 0)

At the 2026-09-29 summit, students said "save" and their Claude tried to commit and push to
Solnest-AI's GitHub: a `git clone` leaves the copy linked to our repo. The tool never needs
git to run, and updates come from scripts/update.py. The rules match the Comping Agent's
detach_from_github.sh (Solnest-AI/solnest-str-comping-agent PR #8), in Python so setup.sh and
setup.ps1 share one tested copy.

It removes .git only from an UNTOUCHED copy of the public repo. It keeps it, and says why, when
the copy is someone's working copy: another origin, edits to tracked files, branches, commits
or stashes that are not on GitHub, or a computer where git does not answer. A developer keeps
git on a clean checkout with `touch .git/str-keep-attached` (permanent) or STR_KEEP_GIT=1
(one run). Before removing .git it records the tracked files as update.py's manifest, so the
first update knows exactly which files are the tool's.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import update

ROOT = Path(__file__).resolve().parent.parent
REPO = "solnest-ai/listing-optimizer"  # compared lower-case: GitHub ignores case


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=30, check=False)


def _ours(origin: str) -> bool:
    o = origin.strip().lower().rstrip("/")
    o = o[:-4] if o.endswith(".git") else o
    return o.endswith(("/" + REPO, ":" + REPO))


def _force_remove(func, path, *_):
    # Git makes its object files read-only; Windows refuses to delete those as they are.
    os.chmod(path, stat.S_IWRITE)
    func(path)


def detach(root: Path = ROOT, env=os.environ) -> str:
    git_dir = root / ".git"
    if not git_dir.exists():
        return "GitHub: not attached (plain folder)."
    if env.get("STR_KEEP_GIT") or (git_dir / "str-keep-attached").exists():
        return "GitHub: still attached (a developer's copy: STR_KEEP_GIT or .git/str-keep-attached)."
    # A Mac without developer tools has a /usr/bin/git stub that opens an install dialog
    # instead of answering (2026-09-28). Only trust git when it answers.
    try:
        works = _git(root, "rev-parse", "--git-dir").returncode == 0
    except (OSError, subprocess.SubprocessError):
        works = False
    if not works:
        return "GitHub: left as is. Git does not answer on this computer, so nothing can use the link."
    origin = _git(root, "remote", "get-url", "origin").stdout.strip()
    if not _ours(origin):
        return f"GitHub: kept attached. This folder's git is not a copy of {REPO} (origin: {origin or 'none'})."
    if _git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        return "GitHub: kept attached. The code here has local edits (a working copy, not a fresh download)."
    branches = [b for b in _git(root, "branch", "--format=%(refname:short)").stdout.split() if b != "main"]
    if (branches or _git(root, "log", "--branches", "--not", "--remotes", "--oneline").stdout.strip()
            or _git(root, "stash", "list").stdout.strip()):
        return "GitHub: kept attached. This copy has branches, commits or stashed work that are not on GitHub."

    tracked = [p for p in _git(root, "ls-files", "-z").stdout.split("\0") if p]
    update.write_manifest(root, tracked)
    _git(root, "remote", "remove", "origin")
    if sys.version_info >= (3, 12):
        shutil.rmtree(git_dir, onexc=_force_remove)
    else:
        shutil.rmtree(git_dir, onerror=_force_remove)
    if git_dir.exists():
        return ("GitHub: the link is removed, but the .git folder could not be deleted (another program "
                "has it open). Close other windows on this folder and run setup again.")
    return ("GitHub: detached. This is now a plain folder, not linked to GitHub. To update later, say "
            "\"update the listing optimizer\".")


def main() -> int:
    try:
        print(f"[setup] {detach()}")
    except Exception as e:  # setup must never stop on this step
        print(f"[setup] GitHub: left as is ({type(e).__name__}).")
    return 0


if __name__ == "__main__":
    import console
    console.utf8_stdio()
    raise SystemExit(main())
