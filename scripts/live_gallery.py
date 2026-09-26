#!/usr/bin/env python3
"""
live_gallery.py — fetch the photo gallery guests actually see on Airbnb.

The PMS copy of a gallery can differ from the live listing. Measured 2026-09-25: the PMS
held 54 photos (collage cover, 16 duplicate pairs) while Airbnb showed 32, with a different
cover and different captions. A photo plan built on the PMS copy was wrong for Airbnb.

Sources, in order (the first configured one that knows the listing wins):
  1. RankBreeze MCP   (RANKBREEZE_MCP_URL): full gallery, live order, captions.
  2. IntelliHost MCP  (INTELLIHOST_MCP_TOKEN): live order, no captions. Premium-gated per
     property, and measured returning 29 of 42 photos whatever the limit, so a short list
     is marked incomplete rather than trusted as the whole gallery.
  3. Neither: the pipeline falls back to the PMS gallery and says so in the report.

Only position, URL and caption are kept. Both providers also return prices, fees and
minimum stays; none of that is ever written to disk (zero-pricing rule).

Usage:
  python scripts/live_gallery.py --room-id 52316045 --out output/<DATE>/<SLUG>/live_gallery.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from mcp_http import MCPError, Session

INTELLIHOST_URL = "https://clients.intellihost.co/api/mcp"
ROOT = Path(__file__).resolve().parent.parent
# Where Claude Code keeps MCP connections: the user config (global and per-project
# sections) and this project's .mcp.json. A member who connected RankBreeze or IntelliHost
# in Claude Code needs no .env entry. Only those two servers are ever read from these files.
CLAUDE_CONFIGS = [Path.home() / ".claude.json", ROOT / ".mcp.json"]
AIRBNB_IMG = re.compile(r"^https://a\d\.muscache\.com/im/pictures/\S+$")


class GalleryUnavailable(RuntimeError):
    """The provider is reachable but cannot supply this listing's gallery."""


def _env(name: str) -> str:
    return (os.environ.get(name) or "").split("#")[0].strip()


def _from_claude_config() -> dict:
    """RankBreeze URL / IntelliHost token from MCP servers already connected in Claude Code."""
    found = {}
    for path in CLAUDE_CONFIGS:
        try:
            cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        groups = [cfg.get("mcpServers") or {}]
        project = (cfg.get("projects") or {}).get(str(ROOT)) if isinstance(cfg.get("projects"), dict) else None
        if isinstance(project, dict):
            groups.append(project.get("mcpServers") or {})
        for servers in groups:
            for server in servers.values() if isinstance(servers, dict) else []:
                url = str((server or {}).get("url") or "")
                auth = str(((server or {}).get("headers") or {}).get("Authorization") or "")
                if "rankbreeze.com/api/mcp/" in url:
                    found.setdefault("rankbreeze", url)
                elif "intellihost.co/api/mcp" in url and auth.startswith("Bearer "):
                    found.setdefault("intellihost", auth[len("Bearer "):].strip())
    return found


def configured_sources() -> list[tuple[str, dict]]:
    """RankBreeze first (full gallery with captions), then IntelliHost. .env wins over the
    Claude Code config for each provider."""
    claude = _from_claude_config()
    out = []
    rb = _env("RANKBREEZE_MCP_URL") or claude.get("rankbreeze")
    if rb:
        out.append(("rankbreeze", {"url": rb}))
    ih = _env("INTELLIHOST_MCP_TOKEN") or claude.get("intellihost")
    if ih:
        out.append(("intellihost", {"url": INTELLIHOST_URL, "token": ih}))
    return out


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_url(u) -> str:
    return str(u or "").split("?")[0]


def from_rankbreeze(session, room_id: str) -> dict:
    cursor, rb_id = None, None
    for _ in range(50):
        args = {"limit": 60, **({"cursor": cursor} if cursor else {})}
        page = session.call("get_user_listings", args)
        for row in page.get("listings") or []:
            if str(row.get("room_id")) == str(room_id):
                rb_id = row.get("id")
                break
        cursor = page.get("nextCursor")
        if rb_id is not None or not cursor:
            break
    if rb_id is None:
        raise GalleryUnavailable(f"Airbnb listing {room_id} is not in this RankBreeze account")
    content = session.call("get_listing_content", {"listing_id": str(rb_id), "include_images": True})
    images = sorted((i for i in content.get("images") or [] if isinstance(i, dict)),
                    key=lambda i: int(i.get("position") or 0))
    photos = [{"position": int(i["position"]), "url": _clean_url(i.get("url")),
               "caption": str(i.get("caption") or "")} for i in images if i.get("position")]
    return {"provider": "rankbreeze", "room_id": str(room_id),
            "fetched_at": content.get("fetched_at") or _now(),
            "returned": len(photos), "reported": len(photos), "complete": True, "photos": photos}


def from_intellihost(session, room_id: str) -> dict:
    props = session.call("list-properties-tool", {"include_inactive": True, "limit": 200})
    match = next((p for p in props.get("properties") or [] if str(p.get("listing_id")) == str(room_id)), None)
    if match is None:
        raise GalleryUnavailable(f"Airbnb listing {room_id} is not in this IntelliHost account")
    try:
        d = session.call("get-listing-details-tool", {"property_id": match["id"],
                                                      "include_photos": True, "photo_limit": 50})
    except MCPError as e:
        if "Premium" in str(e):
            raise GalleryUnavailable("IntelliHost needs a Premium subscription on this property "
                                     "to read its listing through the API") from e
        raise
    urls = [_clean_url(p.get("url")) for p in d.get("photos") or [] if isinstance(p, dict)]
    photos = [{"position": i + 1, "url": u, "caption": ""} for i, u in enumerate(urls)]
    reported = d.get("photo_count") if isinstance(d.get("photo_count"), int) else len(photos)
    return {"provider": "intellihost", "room_id": str(room_id), "fetched_at": _now(),
            "returned": len(photos), "reported": reported,
            "complete": len(photos) >= reported, "photos": photos}


def validate(g: dict) -> dict:
    """Shape check for a fetched OR agent-staged gallery. Raises ValueError."""
    photos = g.get("photos") if isinstance(g, dict) else None
    if not isinstance(photos, list) or not photos:
        raise ValueError("live gallery has no photos")
    seen = set()
    for p in photos:
        if (not isinstance(p, dict) or type(p.get("position")) is not int or p["position"] < 1
                or p["position"] in seen or not AIRBNB_IMG.match(str(p.get("url") or ""))):
            raise ValueError("live gallery photos need unique positions >= 1 and Airbnb image URLs")
        seen.add(p["position"])
    if g.get("provider") not in ("rankbreeze", "intellihost"):
        raise ValueError("live gallery provider must be rankbreeze or intellihost")
    return g


def to_images(g: dict) -> dict:
    """The pipeline's images.json contract, keyed by Airbnb position (1 = cover)."""
    return {"data": [{"url": p["url"] + "?im_w=1200", "thumbnail_url": p["url"] + "?im_w=480",
                      "caption": p.get("caption") or "", "order": p["position"]} for p in g["photos"]],
            "_source": {"kind": "live_airbnb", "provider": g["provider"], "fetched_at": g.get("fetched_at"),
                        "complete": bool(g.get("complete", True)), "returned": g.get("returned", len(g["photos"])),
                        "reported": g.get("reported", len(g["photos"]))}}


def fetch(room_id: str) -> tuple[dict | None, list[str]]:
    """Try each configured source in order. Returns (gallery or None, notes on misses)."""
    notes = []
    for name, cfg in configured_sources():
        session = None
        try:
            session = Session(cfg["url"], cfg.get("token"))
            g = (from_rankbreeze if name == "rankbreeze" else from_intellihost)(session, room_id)
            return validate(g), notes
        except (GalleryUnavailable, MCPError, ValueError) as e:
            notes.append(f"{name}: {e}")
        finally:
            if session:
                session.close()
    return None, notes


def main():
    ap = argparse.ArgumentParser(description="Fetch the live Airbnb gallery (RankBreeze or IntelliHost).")
    ap.add_argument("--room-id", required=True, help="Airbnb listing id")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if not configured_sources():
        sys.exit("[live_gallery] no RANKBREEZE_MCP_URL or INTELLIHOST_MCP_TOKEN configured")
    g, notes = fetch(args.room_id)
    for n in notes:
        print(f"[live_gallery] {n}", file=sys.stderr)
    if g is None:
        sys.exit("[live_gallery] no configured source could supply this listing's gallery")
    Path(args.out).write_text(json.dumps(g, indent=2, ensure_ascii=False), encoding="utf-8")
    state = "complete" if g["complete"] else f"INCOMPLETE ({g['returned']} of {g['reported']})"
    print(f"[live_gallery] {g['provider']}: {g['returned']} live Airbnb photos, {state}")


if __name__ == "__main__":
    main()
