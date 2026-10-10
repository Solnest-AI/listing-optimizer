#!/usr/bin/env python3
"""
update.py — bring a student's copy of the Listing Optimizer to the latest release.

  .venv/bin/python scripts/update.py           update to the latest release, then run setup
  .venv/bin/python scripts/update.py --check   only say whether a newer release exists
  .venv/bin/python scripts/update.py --undo    put back the version from before the last update

Why it exists: a student's copy is a plain folder, not a git clone (setup runs
detach_from_github.py, because at the 2026-09-29 summit "save" turned into a push to
Solnest-AI's GitHub). A plain folder has no `git pull`, so without this no fix would ever
reach a student. Every fix ships as a GitHub release (a tag plus plain-English notes); this
downloads that release's source and replaces the tool's own files.

What it never touches: .env, branding.json, config/properties.json, config/memory.json,
.venv, state/ (history, caches) and output/ (reports). Only paths in the release are
written, and only paths the previous release shipped (the manifest, .lo-files.json) are
removed: a test or script a release dropped would otherwise break setup's self-test.
Every file it replaces or removes is copied to .update-backup/<time>/ first, so --undo is one
step, and a failure halfway puts everything back before it exits. A folder still linked to
git is refused (use `git pull` there).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
REPO = "Solnest-AI/listing-optimizer"
MANIFEST = ".lo-files.json"
BACKUP_DIR = ".update-backup"
KEEP_BACKUPS = 3
NOTICE_CACHE = Path("state") / "update_check.json"
UA = {"User-Agent": "listing-optimizer-updater"}

# Never written, never removed, whatever a manifest or tarball says.
PROTECTED_FILES = {".env", "branding.json", "config/properties.json", "config/memory.json",
                   MANIFEST}
PROTECTED_DIRS = (".venv/", "state/", "output/", ".git/", f"{BACKUP_DIR}/", ".cache/")


class UpdateError(RuntimeError):
    """The update could not run; the folder is unchanged (or was put back)."""


def _protected(rel: str) -> bool:
    return (rel in PROTECTED_FILES or (rel.startswith(".env.") and rel != ".env.example")
            or rel.startswith(PROTECTED_DIRS))


def parse_version(text) -> tuple[int, ...] | None:
    m = re.fullmatch(r"\s*v?(\d+)\.(\d+)\.(\d+)\s*", str(text or ""))
    return tuple(int(x) for x in m.groups()) if m else None


def current_version(root: Path) -> str | None:
    try:
        v = (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return v if parse_version(v) else None


# ── GitHub ─────────────────────────────────────────────────────────────
def latest_tag(timeout: float = 20) -> str:
    """The latest release's tag. github.com redirects /releases/latest to /releases/tag/<tag>;
    unlike the API, that is not limited to 60 requests an hour per network (a classroom shares one)."""
    req = urllib.request.Request(f"https://github.com/{REPO}/releases/latest", headers=UA, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            final = r.geturl()
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"could not reach GitHub ({type(e).__name__})") from e
    m = re.search(r"/releases/tag/([^/?#]+)$", final)
    if not m or not parse_version(m.group(1)):
        raise UpdateError("GitHub has no published release of the Listing Optimizer yet")
    return m.group(1)


def download(tag: str, timeout: float = 60) -> bytes:
    url = f"https://github.com/{REPO}/archive/refs/tags/{tag}.tar.gz"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.read()
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"could not download {tag} from GitHub ({type(e).__name__})") from e


def read_tarball(blob: bytes) -> dict[str, bytes]:
    """{relative path: content} from a GitHub source tarball. Anything that is not a plain file
    or folder inside the one top folder (a link, an absolute path, a ..) refuses the whole
    release: nothing is written."""
    files: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            members = tar.getmembers()
            # GitHub's pax_global_header is metadata, not a path.
            tops = {PurePosixPath(m.name).parts[0] for m in members
                    if m.name and not m.name.startswith("/") and m.type not in (tarfile.XGLTYPE, tarfile.XHDTYPE)}
            for m in members:
                p = PurePosixPath(m.name)
                if m.name.startswith(("/", "\\")) or ".." in p.parts or len(tops) != 1:
                    raise UpdateError(f"the release contains an unsafe path ({m.name}); nothing was changed")
                if m.isdir() or m.type == tarfile.XGLTYPE or m.type == tarfile.XHDTYPE:
                    continue
                if not m.isfile():
                    raise UpdateError(f"the release contains a link or device ({m.name}); nothing was changed")
                rel = "/".join(p.parts[1:])
                if rel:
                    files[rel] = tar.extractfile(m).read()
    except (tarfile.TarError, OSError, EOFError) as e:
        raise UpdateError(f"the downloaded release is not a valid archive ({type(e).__name__})") from e
    if "VERSION" not in files or not parse_version(files["VERSION"].decode("utf-8", "replace")):
        raise UpdateError("the downloaded release has no VERSION file; nothing was changed")
    return files


# ── Manifest ───────────────────────────────────────────────────────────
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_manifest(root: Path) -> dict:
    try:
        m = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
        return m if isinstance(m, dict) and isinstance(m.get("files"), dict) else {"files": {}}
    except (OSError, ValueError):
        return {"files": {}}


def write_manifest(root: Path, paths, version: str | None = None) -> None:
    """Record the tool's own files (and their content) so the next update knows what it may
    replace or remove, and which ones the student edited."""
    files = {}
    for rel in paths:
        p = root / rel
        if p.is_file() and not _protected(rel):
            files[rel] = _sha(p.read_bytes())
    _write(root / MANIFEST, json.dumps({"version": version or current_version(root), "files": files},
                                       indent=1, sort_keys=True).encode())


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".lo-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# ── Update / undo ──────────────────────────────────────────────────────
def update(root: Path = ROOT, force: bool = False) -> dict:
    if (root / ".git").exists():
        raise UpdateError("this folder is still linked to git: update it with `git pull --ff-only`, "
                          "or run setup, which unlinks an untouched copy")
    have = current_version(root)
    tag = latest_tag()
    want = tag.lstrip("v")
    if have and parse_version(have) >= parse_version(want) and not force:
        return {"status": "current", "from": have, "to": have}
    new = {rel: data for rel, data in read_tarball(download(tag)).items() if not _protected(rel)}
    old = _load_manifest(root)["files"]
    gone = sorted(rel for rel in set(old) - set(new) if not _protected(rel))
    edited = sorted(rel for rel, h in old.items()
                    if (root / rel).is_file() and _sha((root / rel).read_bytes()) != h)

    backup = root / BACKUP_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    existed, added = [], []
    for rel in sorted(set(new) | set(gone)):
        src = root / rel
        if src.is_file():
            _write(backup / rel, src.read_bytes())
            existed.append(rel)
        elif rel in new:
            added.append(rel)
    if (root / MANIFEST).is_file():
        _write(backup / MANIFEST, (root / MANIFEST).read_bytes())
    _write(backup / "undo.json", json.dumps({"from": have, "to": want, "existed": existed,
                                             "added": added, "had_manifest": (root / MANIFEST).is_file()},
                                            indent=1).encode())
    try:
        for rel, data in new.items():
            _write(root / rel, data)
        for rel in gone:
            (root / rel).unlink(missing_ok=True)
        write_manifest(root, list(new), want)
    except OSError as e:
        undo(root, backup)
        raise UpdateError(f"the update stopped halfway ({type(e).__name__}: {e}) and the previous "
                          f"version was put back; close other programs using this folder and try again") from e
    _prune(root / BACKUP_DIR)
    return {"status": "updated", "from": have or "unknown", "to": want, "added": added, "removed": gone,
            "edited": [rel for rel in edited if rel in new or rel in gone], "backup": str(backup)}


def undo(root: Path = ROOT, backup: Path | None = None) -> dict:
    """Put back the files from before the last update (or the given backup)."""
    if backup is None:
        found = sorted(p for p in (root / BACKUP_DIR).glob("*") if (p / "undo.json").is_file()) \
            if (root / BACKUP_DIR).is_dir() else []
        if not found:
            raise UpdateError("there is no earlier version to go back to")
        backup = found[-1]
    meta = json.loads((backup / "undo.json").read_text(encoding="utf-8"))
    for rel in meta.get("added") or []:
        if not _protected(rel):
            (root / rel).unlink(missing_ok=True)
    for rel in meta.get("existed") or []:
        if not _protected(rel):
            _write(root / rel, (backup / rel).read_bytes())
    if meta.get("had_manifest") and (backup / MANIFEST).is_file():
        _write(root / MANIFEST, (backup / MANIFEST).read_bytes())
    else:
        (root / MANIFEST).unlink(missing_ok=True)
    shutil.rmtree(backup, ignore_errors=True)
    if (root / BACKUP_DIR).is_dir() and not any((root / BACKUP_DIR).iterdir()):
        (root / BACKUP_DIR).rmdir()
    return {"status": "undone", "to": meta.get("from") or "unknown", "from": meta.get("to")}


def _prune(folder: Path) -> None:
    for old in sorted(p for p in folder.glob("*") if p.is_dir())[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)


# ── The once-a-day notice the pipeline prints ──────────────────────────
def notice(root: Path = ROOT, today: str | None = None) -> str | None:
    """'Update available' text when a newer release exists, else None. Checked at most once a
    day (cached in state/), silent when offline, and off with LO_NO_UPDATE_CHECK=1. Never raises."""
    if (os.environ.get("LO_NO_UPDATE_CHECK") or "").strip() in ("1", "true", "yes"):
        return None
    today = today or date.today().isoformat()
    cache = root / NOTICE_CACHE
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    latest = cached.get("latest") if isinstance(cached, dict) and cached.get("checked") == today else None
    if latest is None:
        try:
            latest = latest_tag(timeout=3)
        except Exception:  # never let an update check touch a run
            latest = ""
        with contextlib.suppress(OSError):
            _write(cache, json.dumps({"checked": today, "latest": latest}).encode())
    have = current_version(root)
    if not parse_version(latest) or (have and parse_version(have) >= parse_version(latest)):
        return None
    return (f"Update available: Listing Optimizer {latest.lstrip('v')} (this copy: {have or 'an older version'}). "
            f"Say \"update the listing optimizer\" to get it; your keys, settings and reports are kept.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Update this copy to the latest Listing Optimizer release.")
    ap.add_argument("--check", action="store_true", help="only say whether a newer release exists")
    ap.add_argument("--undo", action="store_true", help="put back the version from before the last update")
    args = ap.parse_args()
    try:
        if args.undo:
            r = undo()
            print(f"[update] Put back version {r['to']} (was {r['from']}). Run setup next so packages match.")
        elif args.check:
            tag, have = latest_tag(), current_version(ROOT)
            newer = not have or parse_version(have) < parse_version(tag)
            print(f"[update] latest release: {tag.lstrip('v')}; this copy: {have or 'unknown'}"
                  + (". An update is available." if newer else ". Up to date."))
        else:
            r = update()
            if r["status"] == "current":
                print(f"[update] Already on the latest release ({r['to']}). Nothing to do.")
            else:
                print(f"[update] Updated {r['from']} -> {r['to']}: {len(r['added'])} new file(s), "
                      f"{len(r['removed'])} removed. Kept: .env, branding.json, config, state/ and output/.")
                if r["edited"]:
                    print(f"[update] You had edited {len(r['edited'])} file(s); your versions are saved in "
                          f"{r['backup']}: {', '.join(r['edited'][:10])}")
                print("[update] Next: run setup (it installs any new packages and runs the tests). "
                      "To go back: scripts/update.py --undo")
    except UpdateError as e:
        print(f"[update] {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    import console
    console.utf8_stdio()
    raise SystemExit(main())
