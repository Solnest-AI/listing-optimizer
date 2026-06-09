#!/usr/bin/env python3
"""
hospitable_api.py — direct Hospitable Public API v2 fallback (no MCP needed).

If a Hospitable MCP server is connected to Claude Code, the skill uses that.
If not, this script gives the same READ-ONLY data using the user's own
Hospitable Platform API token (my.hospitable.com → Apps → API access),
set as HOSPITABLE_TOKEN in the project .env.

READ-ONLY by construction: only GET requests exist in this file.

ZERO-PRICING WALL: the `calendar` subcommand strips `price` and `min_stay`
per-day BEFORE writing to disk — pricing never lands in a working file.

Usage:
  python scripts/hospitable_api.py properties --out subject_list.json
  python scripts/hospitable_api.py property --property-id UUID --out subject.json
  python scripts/hospitable_api.py images --property-id UUID --out images.json
  python scripts/hospitable_api.py reviews --property-id UUID --out reviews.json
  python scripts/hospitable_api.py calendar --property-id UUID \
      --start 2026-06-08 --end 2026-09-06 --out calendar.json
  python scripts/hospitable_api.py reservations --property-id UUID --out reservations.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

try:  # standalone: load keys from the project .env (gitignored)
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

BASE = os.environ.get("HOSPITABLE_BASE_URL", "https://public.api.hospitable.com/v2")
try:
    TIMEOUT = int(os.environ.get("HOSPITABLE_TIMEOUT", "30"))
except ValueError:
    TIMEOUT = 30


def _token() -> str:
    t = os.environ.get("HOSPITABLE_TOKEN", "").strip()
    if not t:
        sys.exit("[hospitable_api] HOSPITABLE_TOKEN not set. Create a Platform API token at "
                 "my.hospitable.com (Apps → API access) and put it in the project .env "
                 "(see .env.example). Or connect a Hospitable MCP server instead.")
    return t


def _get(path: str, params: dict | None = None) -> dict:
    r = httpx.get(BASE.rstrip("/") + path, params=params or {},
                  headers={"Authorization": f"Bearer {_token()}",
                           "Accept": "application/json"},
                  timeout=TIMEOUT)
    if r.status_code == 401:
        sys.exit("[hospitable_api] 401 Unauthorized — the HOSPITABLE_TOKEN is invalid or expired.")
    if r.status_code >= 400:
        sys.exit(f"[hospitable_api] HTTP {r.status_code} on {path}: {r.text[:200]}")
    return r.json()


def _paginate(path: str, params: dict | None = None, max_pages: int = 20) -> list:
    """Follow Laravel-style pagination (data + meta.current_page/last_page)."""
    items, page = [], 1
    params = dict(params or {})
    while page <= max_pages:
        params["page"] = page
        data = _get(path, params)
        items.extend(data.get("data") or [])
        meta = data.get("meta") or {}
        if page >= int(meta.get("last_page") or 1):
            break
        page += 1
    return items


def _strip_calendar_day(day: dict) -> dict:
    """Keep ONLY date + availability/reason — drop price, min_stay, everything else."""
    st = day.get("status")
    if not isinstance(st, dict):
        st = {"reason": str(st) if st is not None else "", "available": None}
    return {"date": day.get("date"),
            "status": {"available": st.get("available"), "reason": st.get("reason")}}


def main():
    ap = argparse.ArgumentParser(description="Hospitable Public API v2 — read-only fallback (no MCP).")
    ap.add_argument("command", choices=["properties", "property", "images", "reviews",
                                        "calendar", "reservations"])
    ap.add_argument("--property-id", dest="pid", default=None)
    ap.add_argument("--start", default=None, help="calendar start YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="calendar end YYYY-MM-DD")
    ap.add_argument("--out", default=None, help="write JSON here (else stdout)")
    args = ap.parse_args()

    needs_pid = args.command in ("property", "images", "reviews", "calendar", "reservations")
    if needs_pid and not args.pid:
        ap.error(f"'{args.command}' requires --property-id")

    if args.command == "properties":
        result = {"data": _paginate("/properties", {"include": "listings", "per_page": 50})}
    elif args.command == "property":
        result = _get(f"/properties/{args.pid}", {"include": "listings"})
    elif args.command == "images":
        result = _get(f"/properties/{args.pid}/images")
    elif args.command == "reviews":
        result = {"data": _paginate(f"/properties/{args.pid}/reviews", {"per_page": 50})}
    elif args.command == "calendar":
        params = {}
        if args.start:
            params["start_date"] = args.start
        if args.end:
            params["end_date"] = args.end
        raw = _get(f"/properties/{args.pid}/calendar", params)
        days = ((raw.get("data") or {}).get("days")) or []
        # zero-pricing wall: strip price/min_stay BEFORE anything touches disk
        result = {"data": {"days": [_strip_calendar_day(d) for d in days]}}
    else:  # reservations (upcoming/active only, per the API)
        result = {"data": _paginate("/reservations", {"properties[]": args.pid, "per_page": 50})}

    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        n = len(result.get("data") or []) if isinstance(result.get("data"), list) else "1"
        print(f"[hospitable_api] {args.command} → {out} ({n} item(s))")
    else:
        print(text)


if __name__ == "__main__":
    main()
