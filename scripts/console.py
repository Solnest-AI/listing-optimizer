"""Console and OS helpers so the same scripts behave identically on Windows and macOS."""
from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

# Child scripts print UTF-8; parents must decode it the same way on every OS.
TEXT = {"encoding": "utf-8", "errors": "replace"}


def utf8_stdio() -> None:
    """Windows pipes and consoles default to cp1252, which cannot print the status marks
    (e.g. ✅ ⏭️ →) and crashes the run. Force UTF-8 on stdout/stderr for every entry script."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")


def desktop() -> Path:
    """The user's real Desktop. On Windows, OneDrive backup moves it to OneDrive\\Desktop,
    so ~/Desktop would be a folder the user never sees. Ask Windows where it is."""
    if sys.platform == "win32":
        try:
            import winreg
            key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                p = Path(os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0]))
            if p.is_dir():
                return p
        except OSError:
            pass
    return Path.home() / "Desktop"
