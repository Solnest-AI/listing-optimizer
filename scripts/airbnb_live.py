#!/usr/bin/env python3
"""
airbnb_live.py — read the listing guests see NOW from its public Airbnb page.

This is the only source of "live Airbnb" facts about the listing being optimized. Provider
copies (RankBreeze, IntelliHost, AirROI) are stored snapshots and are never used for it:
measured 2026-09-28, RankBreeze and AirROI both served last summer's Après Arcade
(title "Walk to Bike Park & Golf", 79 photos) and RankBreeze's amenity lists for two
different Sun Peaks houses carried the same wrong boxes ("Shared hot tub", "TEKA stainless
steel oven", "Indoor fireplace: wood-burning"). The live pages showed neither.

One free GET of the public page (no key, no login) returns the title, summary, The space,
Guest access, Other things to note, the full amenity list and the photo gallery in order
with the host's captions. Prices on the page are never read. A page that cannot be read or
parsed completely yields NOTHING: the pipeline then uses the PMS copy and says it is
unverified. Nothing is ever partially filled in from elsewhere.

Usage:
  python scripts/airbnb_live.py --room-id 1492274390479279422 --out live_gallery.json
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Accept-Language": "en-US,en;q=0.9"}
# airbnb.com answers some countries with a small form that forwards to the local domain
# (measured from Canada: airbnb.ca). Follow it once rather than guessing the domain.
_DOMAIN_SWITCH = re.compile(r'action="https://(www\.airbnb\.[a-z.]+)/v2/domain_switch')
_OG_DESCRIPTION = re.compile(r'<meta property="og:description" content="([^"]*)"')
AIRBNB_IMG = re.compile(r"^https://a\d\.muscache\.com/im/pictures/\S+$")
# Description sections as the page titles them -> our field names. The untitled one is the summary.
SECTIONS = {None: "summary", "The space": "description", "Guest access": "guest_access",
            "Other things to note": "other_notes"}
_DECODER = json.JSONDecoder()


class PageUnreadable(RuntimeError):
    """The public page could not be fetched or did not contain a complete listing."""


def fetch_page(room_id: str, get=None) -> str:
    if not str(room_id or "").isdigit():
        raise PageUnreadable("invalid Airbnb listing id")
    get = get or (lambda url: httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=30))
    host = "www.airbnb.com"
    try:
        for _ in range(2):
            r = get(f"https://{host}/rooms/{room_id}")
            if r.status_code != 200:
                raise PageUnreadable(f"Airbnb answered HTTP {r.status_code}")
            switch = _DOMAIN_SWITCH.search(r.text)
            if not switch:
                return r.text
            host = switch.group(1)
    except httpx.HTTPError as e:
        raise PageUnreadable(f"could not reach Airbnb ({type(e).__name__})") from e
    raise PageUnreadable("Airbnb kept redirecting between country sites")


def _objects(page: str, typename: str):
    """Every embedded JSON object of one GraphQL type, decoded in place (bracket-safe)."""
    for m in re.finditer(r'\{"__typename":"' + re.escape(typename) + '"', page):
        try:
            yield _DECODER.raw_decode(page, m.start())[0]
        except ValueError:
            continue


def text(value) -> str:
    """Airbnb returns listing copy as HTML with <br /> line breaks."""
    t = re.sub(r"<br\s*/?>", "\n", str(value or ""), flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", t)).strip()


def parse(page: str, room_id: str) -> dict:
    """The complete live listing, or PageUnreadable. Never a partial record."""
    if str(room_id) not in page:
        raise PageUnreadable("the page does not belong to this listing")
    m = _OG_DESCRIPTION.search(page)
    title = html.unescape(m.group(1)).strip() if m else ""
    if not title:
        raise PageUnreadable("no listing title on the page")

    listing = {"title": title, "summary": "", "description": "", "guest_access": "", "other_notes": ""}
    seen = set()
    for item in _objects(page, "BasicListItem"):
        body = (item.get("html") or {}).get("htmlText") if isinstance(item.get("html"), dict) else None
        field = SECTIONS.get(item.get("title"))
        if body and field and field not in seen:
            listing[field] = text(body)
            seen.add(field)
    if not listing["description"] and not listing["summary"]:
        raise PageUnreadable("no listing description on the page")

    details = next(_objects(page, "StaysPdpAmenitiesDetails"), None)
    groups = (details or {}).get("seeAllAmenitiesGroups") or []
    offered, not_included = [], []
    for g in groups:
        for a in g.get("amenities") or [] if isinstance(g, dict) else []:
            label = str(a.get("title") or "").strip() if isinstance(a, dict) else ""
            if label:
                (offered if a.get("available") else not_included).append(label)
    if not offered:
        raise PageUnreadable("no amenity list on the page")
    listing["amenities"] = list(dict.fromkeys(offered))
    listing["not_included"] = list(dict.fromkeys(not_included))

    tour = next(_objects(page, "PhotoTourModalSection"), None)
    images = [i for i in (tour or {}).get("mediaItems") or [] if isinstance(i, dict) and i.get("__typename") == "Image"]
    photos = []
    for i in images:
        url = str(i.get("baseUrl") or "").split("?")[0]
        if not AIRBNB_IMG.match(url) or any(p["url"] == url for p in photos):
            continue
        # The host's caption. accessibilityLabel is "Listing image N" when there is none.
        caption = str((i.get("imageMetadata") or {}).get("caption") or "").strip()
        photos.append({"position": len(photos) + 1, "url": url, "caption": caption})
    if not photos:
        raise PageUnreadable("no photo gallery on the page")

    return {"provider": "airbnb", "room_id": str(room_id),
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "complete": True, "returned": len(photos), "reported": len(photos),
            "photos": photos, "listing": listing}


def read(room_id: str, get=None) -> dict:
    return parse(fetch_page(room_id, get), str(room_id))


def validate(g: dict) -> dict:
    """Shape check for live_gallery.json. Only a live page read (provider "airbnb") is
    accepted: provider snapshots are never the live listing. Raises ValueError."""
    if not isinstance(g, dict) or g.get("provider") != "airbnb":
        raise ValueError("live_gallery.json must come from the live Airbnb page (provider 'airbnb')")
    photos = g.get("photos")
    if not isinstance(photos, list) or not photos:
        raise ValueError("live gallery has no photos")
    seen = set()
    for p in photos:
        if (not isinstance(p, dict) or type(p.get("position")) is not int or p["position"] < 1
                or p["position"] in seen or not AIRBNB_IMG.match(str(p.get("url") or ""))):
            raise ValueError("live gallery photos need unique positions >= 1 and Airbnb image URLs")
        seen.add(p["position"])
    listing = g.get("listing")
    if (not isinstance(listing, dict)
            or not all(isinstance(listing.get(k, ""), str) for k in ("title", "summary", "description"))
            or not isinstance(listing.get("amenities"), list)):
        raise ValueError("live listing must hold text title/summary/description and an amenity list")
    return g


def to_images(g: dict) -> dict:
    """The pipeline's images.json contract, keyed by Airbnb position (1 = cover)."""
    return {"data": [{"url": p["url"] + "?im_w=1200", "thumbnail_url": p["url"] + "?im_w=480",
                      "caption": p.get("caption") or "", "order": p["position"]} for p in g["photos"]],
            "_source": {"kind": "live_airbnb", "provider": "airbnb", "fetched_at": g.get("fetched_at"),
                        "complete": True, "returned": len(g["photos"]), "reported": len(g["photos"])}}


def main():
    ap = argparse.ArgumentParser(description="Read the live listing from its public Airbnb page.")
    ap.add_argument("--room-id", required=True, help="Airbnb listing id")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    try:
        g = read(args.room_id)
    except PageUnreadable as e:
        sys.exit(f"[airbnb_live] {e}")
    Path(args.out).write_text(json.dumps(g, indent=2, ensure_ascii=False), encoding="utf-8")
    lst = g["listing"]
    print(f"[airbnb_live] live page: \"{lst['title']}\", {g['returned']} photos "
          f"({sum(1 for p in g['photos'] if p['caption'])} captioned), {len(lst['amenities'])} amenities")


if __name__ == "__main__":
    import console
    console.utf8_stdio()
    main()
