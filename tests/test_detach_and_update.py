"""Student copies are never linked to our GitHub, and still get every fix (S2-002).

At the 2026-09-29 summit students said "save" and their Claude tried to commit and push to
Solnest-AI's GitHub, because a `git clone` leaves the copy linked to our repo (open PR #2 came
from a student's fork). The Comping Agent fixed this in Solnest-AI/solnest-str-comping-agent
PR #8; this is the same rule set for the Listing Optimizer, plus the missing half: a plain
folder has no `git pull`, so scripts/update.py brings it to the latest release, keeping the
student's files, with a one-step undo.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import detach_from_github as dg
import update

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = "https://github.com/Solnest-AI/listing-optimizer.git"
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1"}
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git needed")


def _git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, env=ENV, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _student_clone(tmp_path, origin=PUBLIC):
    """What `git clone` of the public repo looks like: main, tracking origin/main."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    work = tmp_path / "listing-optimizer"
    _git(tmp_path, "clone", "-q", str(remote), str(work))
    (work / "scripts").mkdir()
    (work / "scripts" / "tool.py").write_text("print('v1')\n", encoding="utf-8")
    (work / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "init")
    _git(work, "push", "-q", "origin", "main")
    _git(work, "remote", "set-url", "origin", origin)
    (work / ".env").write_text("GEMINI_API_KEY=keep-me\n", encoding="utf-8")  # gitignored in real life
    return work


# ── Detach ─────────────────────────────────────────────────────────────
@needs_git
def test_a_fresh_clone_is_detached_and_remembers_its_files(tmp_path):
    work = _student_clone(tmp_path)
    msg = dg.detach(work, env=ENV)
    assert "detached" in msg and not (work / ".git").exists()
    assert (work / "scripts" / "tool.py").exists() and (work / ".env").exists(), "only .git goes"
    files = json.loads((work / update.MANIFEST).read_text(encoding="utf-8"))["files"]
    assert set(files) == {"scripts/tool.py", "VERSION"}, "the manifest is git's tracked files, never .env"
    assert "not attached" in dg.detach(work, env=ENV), "running it again is a no-op"


@needs_git
@pytest.mark.parametrize("origin", ["git@github.com:solnest-ai/Listing-Optimizer",
                                    "https://github.com/Solnest-AI/listing-optimizer/"])
def test_ssh_and_mixed_case_addresses_count_as_ours(tmp_path, origin):
    work = _student_clone(tmp_path, origin=origin)
    dg.detach(work, env=ENV)
    assert not (work / ".git").exists()


@needs_git
@pytest.mark.parametrize("change", ["edit", "branch", "commit", "stash", "other_origin"])
def test_a_working_copy_is_never_touched(tmp_path, change):
    work = _student_clone(tmp_path, origin="https://github.com/someone/fork.git" if change == "other_origin" else PUBLIC)
    if change == "edit":
        (work / "scripts" / "tool.py").write_text("print('mine')\n", encoding="utf-8")
    elif change == "branch":
        _git(work, "branch", "gemini-3.8-flash")
    elif change == "commit":
        (work / "new.py").write_text("x = 1\n", encoding="utf-8")
        _git(work, "add", "new.py")
        _git(work, "commit", "-qm", "local work")
    elif change == "stash":
        (work / "scripts" / "tool.py").write_text("print('wip')\n", encoding="utf-8")
        _git(work, "stash", "-q")
    msg = dg.detach(work, env=ENV)
    assert (work / ".git").is_dir() and "kept attached" in msg
    assert _git(work, "remote").strip() == "origin"


@needs_git
def test_a_developer_can_opt_out(tmp_path):
    work = _student_clone(tmp_path)
    (work / ".git" / "str-keep-attached").touch()
    assert "still attached" in dg.detach(work, env=ENV) and (work / ".git").is_dir()
    (work / ".git" / "str-keep-attached").unlink()
    assert "still attached" in dg.detach(work, env={**ENV, "STR_KEEP_GIT": "1"}) and (work / ".git").is_dir()


def test_no_working_git_leaves_the_folder_alone(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    def broken(*a, **k):
        raise OSError("xcrun: error: invalid active developer path")
    monkeypatch.setattr(dg.subprocess, "run", broken)
    assert "left as is" in dg.detach(tmp_path, env={}) and (tmp_path / ".git").is_dir()


def test_detach_always_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dg, "detach", lambda *a, **k: "GitHub: kept attached. test")
    assert dg.main() == 0 and "kept attached" in capsys.readouterr().out


# ── Update ─────────────────────────────────────────────────────────────
def _tarball(files: dict, top="listing-optimizer-1.2.0", extra=None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        d = tarfile.TarInfo(top)
        d.type = tarfile.DIRTYPE
        tar.addfile(d)
        for rel, body in files.items():
            data = body.encode()
            info = tarfile.TarInfo(f"{top}/{rel}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info in extra or []:
            tar.addfile(info, io.BytesIO(b"x" * info.size) if info.isfile() else None)
    return buf.getvalue()


V1 = {"VERSION": "1.1.0\n", "scripts/tool.py": "print('v1')\n", "scripts/old.py": "print('old')\n",
      "tests/test_old.py": "def test_old(): pass\n"}
V2 = {"VERSION": "1.2.0\n", "scripts/tool.py": "print('v2')\n", "scripts/new.py": "print('new')\n"}
USER = {".env": "GEMINI_API_KEY=keep-me\n", "branding.json": "{}", "config/properties.json": "{}",
        "config/memory.json": "{}", "state/history.jsonl": "{}\n", "output/2026-10-10/x/digest.md": "d",
        ".venv/bin/python": "py"}


def _plain_copy(tmp_path, files=V1, manifest=True):
    root = tmp_path / "listing-optimizer"
    for rel, body in {**files, **USER}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    if manifest:
        update.write_manifest(root, list(files))
    return root


def _fake_release(monkeypatch, files=V2, tag="v1.2.0", blob=None):
    calls = []
    monkeypatch.setattr(update, "latest_tag", lambda: tag)
    def download(t):
        calls.append(t)
        return blob if blob is not None else _tarball(files)
    monkeypatch.setattr(update, "download", download)
    return calls


def _snapshot(root):
    return {str(p.relative_to(root)).replace(os.sep, "/"): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_an_update_replaces_the_tool_and_keeps_every_student_file(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path)
    _fake_release(monkeypatch)
    report = update.update(root)
    assert report["from"] == "1.1.0" and report["to"] == "1.2.0"
    assert (root / "scripts/tool.py").read_text() == "print('v2')\n"
    assert (root / "scripts/new.py").exists()
    assert not (root / "scripts/old.py").exists() and not (root / "tests/test_old.py").exists(), \
        "a file the release dropped would break setup's self-test if left behind"
    for rel, body in USER.items():
        assert (root / rel).read_text(encoding="utf-8") == body, f"{rel} must survive an update"
    assert (root / "VERSION").read_text().strip() == "1.2.0"
    assert set(json.loads((root / update.MANIFEST).read_text())["files"]) == set(V2)


def test_an_edited_file_is_backed_up_and_named(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path)
    (root / "scripts/tool.py").write_text("print('my tweak')\n", encoding="utf-8")
    _fake_release(monkeypatch)
    report = update.update(root)
    assert report["edited"] == ["scripts/tool.py"]
    backup = Path(report["backup"])
    assert (backup / "scripts/tool.py").read_text() == "print('my tweak')\n"


def test_undo_puts_back_exactly_the_previous_version(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path)
    before = _snapshot(root)
    _fake_release(monkeypatch)
    update.update(root)
    update.undo(root)
    after = {k: v for k, v in _snapshot(root).items() if not k.startswith(update.BACKUP_DIR)}
    assert after == before


def test_already_current_downloads_nothing(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path, files={**V1, "VERSION": "1.2.0\n"})
    calls = _fake_release(monkeypatch)
    assert update.update(root)["status"] == "current" and calls == []


def test_a_folder_still_linked_to_git_is_not_overwritten(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path)
    (root / ".git").mkdir()
    calls = _fake_release(monkeypatch)
    with pytest.raises(update.UpdateError, match="git pull"):
        update.update(root)
    assert calls == [] and (root / "scripts/tool.py").read_text() == "print('v1')\n"


@pytest.mark.parametrize("evil", ["../escape.py", "/etc/passwd", "link"])
def test_a_tarball_that_reaches_outside_the_folder_is_refused(tmp_path, monkeypatch, evil):
    info = tarfile.TarInfo(f"listing-optimizer-1.2.0/{evil}" if not evil.startswith("/") else evil)
    if evil == "link":
        info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
    else:
        info.size = 1
    root = _plain_copy(tmp_path)
    before = _snapshot(root)
    _fake_release(monkeypatch, blob=_tarball(V2, extra=[info]))
    with pytest.raises(update.UpdateError):
        update.update(root)
    assert _snapshot(root) == before and not (tmp_path / "escape.py").exists()


def test_a_failure_halfway_rolls_back(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path)
    before = _snapshot(root)
    _fake_release(monkeypatch)
    real = update._write
    def flaky(path, data):
        if path.name == "new.py":
            raise OSError("disk full")
        real(path, data)
    monkeypatch.setattr(update, "_write", flaky)
    with pytest.raises(update.UpdateError, match="put back"):
        update.update(root)
    assert {k: v for k, v in _snapshot(root).items() if not k.startswith(update.BACKUP_DIR)} == before


def test_no_network_is_a_clear_message_and_changes_nothing(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path)
    before = _snapshot(root)
    def offline():
        raise update.UpdateError("could not reach GitHub (URLError)")
    monkeypatch.setattr(update, "latest_tag", offline)
    with pytest.raises(update.UpdateError, match="could not reach GitHub"):
        update.update(root)
    assert _snapshot(root) == before


# ── Update notice ──────────────────────────────────────────────────────
def test_the_pipeline_notice_names_the_new_version_once_a_day(tmp_path, monkeypatch):
    monkeypatch.delenv("LO_NO_UPDATE_CHECK", raising=False)
    root = _plain_copy(tmp_path)
    asked = []
    monkeypatch.setattr(update, "latest_tag", lambda timeout=3: asked.append(1) or "v1.2.0")
    msg = update.notice(root, today="2026-10-10")
    assert "1.2.0" in msg and "update the listing optimizer" in msg and "—" not in msg
    update.notice(root, today="2026-10-10")
    assert len(asked) == 1, "one check per day, not one per run"


def test_the_notice_is_silent_when_current_offline_or_switched_off(tmp_path, monkeypatch):
    root = _plain_copy(tmp_path, files={**V1, "VERSION": "1.2.0\n"})
    monkeypatch.delenv("LO_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update, "latest_tag", lambda timeout=3: "v1.2.0")
    assert update.notice(root, today="2026-10-10") is None
    def offline(timeout=3):
        raise update.UpdateError("offline")
    monkeypatch.setattr(update, "latest_tag", offline)
    assert update.notice(root, today="2026-10-11") is None
    monkeypatch.setenv("LO_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(update, "latest_tag", lambda timeout=3: "v9.0.0")
    assert update.notice(root, today="2026-10-12") is None


# ── The rules are written where Claude reads them ──────────────────────
def test_the_repo_ships_a_version_and_ignores_local_update_files():
    assert update.parse_version((ROOT / "VERSION").read_text()) is not None
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert update.MANIFEST in ignored and update.BACKUP_DIR in ignored


def test_setup_detaches_and_the_docs_forbid_pushing():
    for script in ("setup.sh", "setup.ps1"):
        assert "detach_from_github.py" in (ROOT / script).read_text(encoding="utf-8"), script
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Never commit, push, fork" in claude and "scripts/update.py" in claude
    skill = (ROOT / ".claude/skills/listing-optimizer/SKILL.md").read_text(encoding="utf-8")
    assert "Never `git commit`, push, fork" in skill
    assert "detach_from_github" not in skill, "running the skill must never strip a developer's git"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    note = readme.split("> **Claude:**", 1)[1].split("\n\n", 1)[0]
    assert "archive/refs/heads/main.tar.gz" in note and "do **not** `git clone`" in note


def test_the_pipeline_summary_ends_with_the_update_notice(tmp_path, monkeypatch, capsys):
    import run_pipeline as rp
    from test_run_pipeline import GOOD_SUBJECT
    (tmp_path / "subject.json").write_text(json.dumps(GOOD_SUBJECT), encoding="utf-8")
    monkeypatch.setattr(rp.update, "notice", lambda *a, **k: "Update available: Listing Optimizer 9.9.9 (test)")
    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "--slug", "t", "--date", "2026-09-20",
                                      "--workdir", str(tmp_path), "--skip", "comps,photos,reviews"])
    with pytest.raises(SystemExit):
        rp.main()
    tail = capsys.readouterr().out.strip().splitlines()[-3:]
    assert any("Update available: Listing Optimizer 9.9.9" in line for line in tail), tail


def test_the_shipped_env_example_is_updated_but_real_env_files_never_are(tmp_path, monkeypatch):
    """Found on a live run 2026-10-10: the .env.* guard also caught .env.example, so a release
    could never update it (91 of 92 files in the manifest)."""
    root = _plain_copy(tmp_path, files={**V1, ".env.example": "GEMINI_API_KEY=\n"})
    (root / ".env.local").write_text("SECRET=1\n", encoding="utf-8")
    _fake_release(monkeypatch, files={**V2, ".env.example": "GEMINI_API_KEY=\n# GEMINI_MODEL=\n",
                                      ".env.local": "overwritten\n"})
    update.update(root)
    assert "GEMINI_MODEL" in (root / ".env.example").read_text()
    assert (root / ".env.local").read_text() == "SECRET=1\n" and (root / ".env").read_text() == USER[".env"]
