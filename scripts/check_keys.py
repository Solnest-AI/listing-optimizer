#!/usr/bin/env python3
"""Verify the keys in .env with small, read-only requests. Never prints a key.

Hospitable: lists one property. Gemini: one tiny real request (about 20 tokens, a 1-line JSON
answer) to the scoring model with the scoring settings. Reading the model's metadata is not
enough: on 2026-09-29 it still answered 200 for gemini-2.5-flash while every scoring request
from a new key got a 404, and it cannot catch a setting the model rejects. On Google's free
tier the check uses one request of the day's allowance. AirROI bills every call, so only its
presence is checked; the first run proves it works.

usage: check_keys.py      exit 0 when every required key is present and nothing was rejected
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

import analyze_photos

ROOT = Path(__file__).resolve().parent.parent
_PROBE_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def _get(url: str, headers: dict) -> str:
    try:
        r = httpx.get(url, headers=headers, timeout=20)
    except httpx.HTTPError as e:
        return f"could not connect ({type(e).__name__})"
    if r.status_code == 200:
        return "ok"
    if r.status_code in (401, 403):
        return f"rejected (HTTP {r.status_code}): the key is wrong or expired"
    return f"unexpected HTTP {r.status_code}"


def check_gemini(key: str, model: str) -> tuple[str, bool]:
    """(message, ok) from one real request with the exact settings photo scoring sends."""
    body = {"contents": [{"parts": [{"text": 'Answer {"ok": true}'}]}],
            "generationConfig": analyze_photos.generation_config(model, _PROBE_SCHEMA, 16)}
    try:
        r = httpx.post(analyze_photos.GEMINI_URL.format(model=model), headers={"x-goog-api-key": key},
                       json=body, timeout=30)
    except httpx.HTTPError as e:
        return f"could not connect ({type(e).__name__})", False
    if r.status_code == 200:
        return f"ok ({model} answered a test request)", True
    # Gemini answers a bad key with 400 API_KEY_INVALID rather than 401.
    if r.status_code in (401, 403) or (r.status_code == 400 and "API_KEY_INVALID" in r.text):
        return f"rejected (HTTP {r.status_code}): the key is wrong or expired", False
    if r.status_code in (400, 404):
        return analyze_photos.settings_error(model, r.status_code, analyze_photos._google_message(r)), False
    if r.status_code == 429:
        _, exhausted = analyze_photos._rate_limit(r)
        if exhausted == "no_free_tier":
            return f"{model}: {analyze_photos.QUOTA_MESSAGES['no_free_tier']}", False
        if exhausted == "daily":
            return "key works, but today's free Gemini quota is used up (it resets at midnight Pacific)", True
        return "key works (Gemini is busy right now: a per-minute limit; runs wait it out)", True
    return f"unexpected HTTP {r.status_code}", False


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
    gem = analyze_photos.load_gemini_key()
    if gem:
        msg, ok = check_gemini(gem, analyze_photos.configured_model())
        rows.append(("Gemini", msg, ok))
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
