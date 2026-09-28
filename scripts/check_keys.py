#!/usr/bin/env python3
"""Verify the keys in .env with free, read-only requests. Never prints a key.

Hospitable: lists one property. Gemini: reads the model's metadata (no generation, no cost).
AirROI bills every call, so only its presence is checked; the first run proves it works.

usage: check_keys.py      exit 0 when every required key is present and nothing was rejected
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def _get(url: str, headers: dict) -> str:
    try:
        r = httpx.get(url, headers=headers, timeout=20)
    except httpx.HTTPError as e:
        return f"could not connect ({type(e).__name__})"
    if r.status_code == 200:
        return "ok"
    # Gemini answers a bad key with 400 API_KEY_INVALID rather than 401.
    if r.status_code in (401, 403) or (r.status_code == 400 and "API_KEY_INVALID" in r.text):
        return f"rejected (HTTP {r.status_code}): the key is wrong or expired"
    return f"unexpected HTTP {r.status_code}"


def check() -> list[tuple[str, str, bool]]:
    # override=True: this checks what is IN the file. A stale key inherited from the shell must
    # not make a blank or rejected line in .env look fine (Codex review, 2026-09-28).
    load_dotenv(ROOT / ".env", encoding="utf-8-sig", override=True)
    rows = []
    hosp = os.environ.get("HOSPITABLE_TOKEN") or os.environ.get("HOSPITABLE_API_KEY")
    if hosp:
        base = os.environ.get("HOSPITABLE_BASE_URL", "https://public.api.hospitable.com/v2")
        res = _get(base.rstrip("/") + "/properties?per_page=1",
                   {"Authorization": f"Bearer {hosp}", "Accept": "application/json"})
        rows.append(("Hospitable", res, res == "ok"))
    else:
        rows.append(("Hospitable", "not set (fine if you use another PMS)", True))
    gem = os.environ.get("GEMINI_API_KEY")
    if gem:
        model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        res = _get(f"https://generativelanguage.googleapis.com/v1beta/models/{model}", {"x-goog-api-key": gem})
        rows.append(("Gemini", res, res == "ok"))
    else:
        rows.append(("Gemini", "MISSING: needed for photo scoring", False))
    air = os.environ.get("AIRROI_API_KEY")
    rows.append(("AirROI", "present (checked on first run; every AirROI call is billed)" if air
                 else "MISSING: needed for competitor comps", bool(air)))
    return rows


def main() -> int:
    rows = check()
    for name, msg, ok in rows:
        print(f"  [{'OK' if ok else '!!'}] {name:11} {msg}")
    return 0 if all(ok for _, _, ok in rows) else 1


if __name__ == "__main__":
    import console
    console.utf8_stdio()
    raise SystemExit(main())
