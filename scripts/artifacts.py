"""Atomic working files and the run's explicit data-availability boundary."""
from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".lo-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def run_status(workdir: Path) -> dict:
    path = workdir / "pipeline_status.json"
    if not path.exists():
        return {}  # legacy/manually staged working directories
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("excluded_files"), list):
        raise ValueError("pipeline_status.json is invalid; rerun the pipeline")
    return data


def excluded(workdir: Path, name: str) -> bool:
    status = run_status(workdir)
    return status.get("status") in ("running", "failed") or name in status.get("excluded_files", [])


@contextmanager
def file_lock(path: Path, timeout: float = 15):
    """Serialize a read/modify/replace transaction across processes on Mac/Windows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as fh:
        if fh.tell() == 0:
            fh.write(b"0")
            fh.flush()
        start = time.monotonic()
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() - start >= timeout:
                    raise TimeoutError(f"Timed out waiting for {path.name}")
                time.sleep(0.025)
        try:
            yield
        finally:
            if os.name == "nt":
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fh, fcntl.LOCK_UN)
