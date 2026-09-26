#!/usr/bin/env python3
"""
live_gallery.py — fetch the photo gallery guests actually see on Airbnb.

The PMS copy of a gallery can differ from the live listing. Measured 2026-09-25: the PMS
held 54 photos (collage cover, 16 duplicate pairs) while Airbnb showed 32, with a different
cover and different captions. A photo plan built on the PMS copy was wrong for Airbnb.

Sources, in order (the first configured one that knows the listing wins):
  1. RankBreeze MCP   (RANKBREEZE_MCP_URL): full gallery, live order, captions, and the live
     title, summary, description and amenity checkboxes.
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


def _text(html) -> str:
    """Airbnb returns listing copy as HTML with <br /> line breaks."""
    t = re.sub(r"<br\s*/?>", "\n", str(html or ""), flags=re.I)
    return re.sub(r"<[^>]+>", "", t).strip()


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
    listing = {"title": str(content.get("title") or ""), "summary": _text(content.get("short_description")),
               "description": _text(content.get("long_description")),
               "guest_access": _text(content.get("guest_access")),
               "amenities": [str(a) for a in content.get("amenities") or [] if a],
               # Live on Airbnb now; AirROI's copy of these can lag (measured 13 vs 16 reviews).
               "rating_overall": content.get("rating"), "num_reviews": content.get("reviews_count")}
    return {"provider": "rankbreeze", "room_id": str(room_id),
            "fetched_at": content.get("fetched_at") or _now(),
            "returned": len(photos), "reported": len(photos), "complete": True, "photos": photos,
            "listing": listing}


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
    # IntelliHost returns the whole description as one field and no amenity list.
    listing = {"title": str(d.get("title") or ""), "summary": "", "description": _text(d.get("description")),
               "amenities": None}
    return {"provider": "intellihost", "room_id": str(room_id), "fetched_at": _now(),
            "returned": len(photos), "reported": reported,
            "complete": len(photos) >= reported, "photos": photos, "listing": listing}


def from_airroi_subject(subject: dict, pms_count: int | None = None) -> dict | None:
    """The live listing as AirROI saw it, from the subject's own row in the comps pool (no
    extra call). AirROI can hold just Airbnb's 5-photo top grid (measured: photos_count 5 on
    a 47-photo listing, with nothing saying more exist), so a short list against a larger
    PMS gallery is marked incomplete instead of being ranked as the whole gallery."""
    if not isinstance(subject, dict):
        return None
    urls = [_clean_url(u) for u in subject.get("photo_urls") or [] if AIRBNB_IMG.match(str(u or ""))]
    reported = subject.get("photos_count") if isinstance(subject.get("photos_count"), int) else len(urls)
    reason = ""
    if len(urls) < reported:
        reason = f"AirROI returned {len(urls)} of the {reported} photos it counted"
    elif len(urls) <= 5 and (pms_count or 0) > len(urls):
        reason = (f"AirROI saw only Airbnb's top grid ({len(urls)} photos) while the PMS holds {pms_count}; "
                  f"the rest of the live gallery was not visible to it")
    # AirROI's "description" is the Airbnb summary followed by the full description, so it
    # is stored as the full text; the digest checks the summary against its opening.
    listing = {"title": str(subject.get("title") or ""), "summary": "",
               "description": _text(subject.get("description")),
               "amenities": [str(a) for a in subject.get("amenities") or []],
               "rating_overall": subject.get("rating_overall"), "num_reviews": subject.get("num_reviews"),
               "guest_favorite": subject.get("guest_favorite"), "superhost": subject.get("superhost")}
    return {"provider": "airroi", "room_id": str(subject.get("listing_id") or ""),
            "fetched_at": subject.get("fetched_at") or "AirROI comps pool (see comps.json fetch age)",
            "returned": len(urls), "reported": max(reported, pms_count or 0) if reason else reported,
            "complete": not reason, "incomplete_reason": reason,
            "photos": [{"position": i + 1, "url": u, "caption": ""} for i, u in enumerate(urls)],
            "listing": listing}


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
    if g.get("provider") not in ("rankbreeze", "intellihost", "airroi"):
        raise ValueError("live gallery provider must be rankbreeze, intellihost or airroi")
    listing = g.get("listing")
    if listing is not None and (not isinstance(listing, dict)
                                or not all(isinstance(listing.get(k, ""), str) for k in ("title", "summary", "description"))
                                or not isinstance(listing.get("amenities") or [], list)):
        raise ValueError("live listing must hold text title/summary/description and an amenity list")
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
