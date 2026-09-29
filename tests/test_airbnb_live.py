"""Reading the live listing from its public Airbnb page (apres-arcade, 2026-09-28).

RankBreeze and AirROI served last summer's Après Arcade ("Walk to Bike Park & Golf", 79
photos) and RankBreeze gave two different Sun Peaks houses the same wrong amenity boxes
("Shared hot tub", "TEKA stainless steel oven", "Indoor fireplace: wood-burning"). The live
pages showed a private hot tub and none of those boxes. The page is now the only source, and
a page that is not complete yields nothing rather than a partial record.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import airbnb_live as al
import render_report as rr

ROOM = "1492274390479279422"
MUS = f"https://a0.muscache.com/im/pictures/hosting/Hosting-{ROOM}/original/"


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


def _js(obj):
    """Compact, like the JSON Airbnb embeds in the page."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _item(title, html):
    return {"__typename": "BasicListItem", "title": title, "html": {"__typename": "Html", "htmlText": html}}


def _page(title="Ski-in/out retreat | Hot tub &amp; arcade", sections=True, amenities=True, photos=True):
    parts = [f'<meta property="og:description" content="{title}">']
    if sections:
        parts += [_js(_item(None, "For families seeking winter adventure.")),
                  _js(_item("The space", "WINTER AT SUN PEAKS<br />Private hot tub.")),
                  _js(_item("Registration details", "Municipal registration number: 1158"))]
    if amenities:
        parts.append(_js({"__typename": "StaysPdpAmenitiesDetails", "seeAllAmenitiesGroups": [
            {"__typename": "AmenityItemsGroup", "title": "Outdoor", "amenities": [
                {"__typename": "AmenityItem", "id": f"pdp_{ROOM}-0", "available": True,
                 "title": "Private hot tub – open 24 hours"}]},
            {"__typename": "AmenityItemsGroup", "title": "Not included", "amenities": [
                {"__typename": "AmenityItem", "available": False, "title": "Air conditioning"}]}]}))
    if photos:
        parts.append(_js({"__typename": "PhotoTourModalSection", "mediaItems": [
            {"__typename": "Image", "baseUrl": MUS + "a.jpeg", "accessibilityLabel": "Arcade lounge",
             "imageMetadata": {"caption": "Arcade lounge"}},
            {"__typename": "Image", "baseUrl": MUS + "b.jpeg", "accessibilityLabel": "Listing image 2",
             "imageMetadata": {"caption": ""}},
            {"__typename": "Image", "baseUrl": MUS + "a.jpeg?im_w=720", "imageMetadata": {"caption": "dup"}}]}))
    return f"<html><script>{ROOM} " + " ".join(parts) + "</script></html>"


def test_the_live_listing_is_read_completely():
    g = al.parse(_page(), ROOM)
    lst = g["listing"]
    assert g["provider"] == "airbnb" and lst["title"] == "Ski-in/out retreat | Hot tub & arcade"
    assert lst["summary"] == "For families seeking winter adventure."
    assert lst["description"] == "WINTER AT SUN PEAKS\nPrivate hot tub."
    assert lst["guest_access"] == "", "no Guest access section on the page means none, never a guess"
    assert lst["amenities"] == ["Private hot tub – open 24 hours"] and lst["not_included"] == ["Air conditioning"]
    al.validate(g)


def test_photos_keep_order_and_the_hosts_caption_not_airbnbs_placeholder():
    photos = al.parse(_page(), ROOM)["photos"]
    assert [p["position"] for p in photos] == [1, 2], "a repeated image URL is one photo"
    assert photos[0]["caption"] == "Arcade lounge"
    assert photos[1]["caption"] == "", "'Listing image 2' is Airbnb's placeholder, not a caption"


@pytest.mark.parametrize("kw, why", [
    ({"title": ""}, "title"), ({"sections": False}, "description"),
    ({"amenities": False}, "amenity"), ({"photos": False}, "photo"),
])
def test_an_incomplete_page_yields_nothing(kw, why):
    with pytest.raises(al.PageUnreadable, match=why):
        al.parse(_page(**kw), ROOM)


def test_a_page_for_another_listing_is_rejected():
    with pytest.raises(al.PageUnreadable, match="does not belong"):
        al.parse(_page().replace(ROOM, "999"), ROOM)


def test_the_country_redirect_is_followed_once():
    switch = ('<form method="POST" action="https://www.airbnb.ca/v2/domain_switch/handoff">'
              '<input type="hidden" name="payload" value="x"></form>')
    seen = []

    def get(url):
        seen.append(url)
        return _Resp(switch if "airbnb.com" in url else _page())

    assert al.read(ROOM, get)["listing"]["title"].startswith("Ski-in/out retreat")
    assert seen == [f"https://www.airbnb.com/rooms/{ROOM}", f"https://www.airbnb.ca/rooms/{ROOM}"]


def test_an_unreachable_page_is_a_named_failure():
    with pytest.raises(al.PageUnreadable, match="403"):
        al.read(ROOM, lambda url: _Resp("", 403))
    with pytest.raises(al.PageUnreadable, match="invalid"):
        al.read("not-an-id")


def _result(remove, repeats=(), reshoot=()):
    return {"optimized": {"remove_orders": list(remove)},
            "photos": {"duplicate_repeats": list(repeats), "reshoot": list(reshoot)}}


def test_only_duplicates_and_reshoots_may_be_deleted():
    """The same run marked 28 of 79 photos for deletion with no duplicate detected."""
    rr.check_removals(_result([4, 14], repeats=[4], reshoot=[14]))
    rr.check_removals(_result([]))
    with pytest.raises(ValueError, match=r"\[5, 12\]"):
        rr.check_removals(_result([4, 5, 12], repeats=[4]))
