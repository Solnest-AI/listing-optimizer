"""Photo numbers follow the listing's real order, not the room-by-room photo tour (S2-005).

Bert Perez, 2026-09-29: the report called a living-room photo the cover when his real cover was
the hot tub. airbnb_live read PhotoTourModalSection.mediaItems, which Airbnb groups by room.
Re-read 2026-10-10 on room 1492274390479279422: the real cover (og:image) is tour photo #2 and
the listing order, in tour numbers, starts [2, 3, 1, 5, 6, 4, 8, 7, 9, 11, 12, 10]. 5 of 6 live
listings checked that day had a tour order that differs from the listing order somewhere. The
listing order is the page's heroMedia list; on all 6, og:image was its first photo.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import airbnb_live as al
import analyze_photos as ap

FX = json.loads((Path(__file__).parent / "fixtures" / "live_photo_order_1492274390479279422.json")
                .read_text(encoding="utf-8"))
ROOM = FX["room_id"]


def _js(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _hero(urls, captions=None):
    captions = captions or {}
    return {"__typename": "HeroItemConnection", "edges": [
        {"__typename": "HeroItemEdge", "node": {"__typename": "HeroImageItem", "image": {
            "__typename": "ImageEntity", "uri": u,
            "caption": {"__typename": "MediaText", "user": {"__typename": "UGCText",
                                                            "source": captions.get(u, "")}}}}}
        for u in urls]}


def _page(listing=None, tour=None, og=FX["og_image"], hero=True, hero_captions=None):
    listing = FX["listing_order"] if listing is None else listing
    tour = FX["tour_order"] if tour is None else tour
    parts = ['<meta property="og:description" content="Ski-in/out retreat">']
    if og:
        parts.append(f'<meta property="og:image" content="{og}">')
    parts += [_js({"__typename": "BasicListItem", "title": None,
                   "html": {"__typename": "Html", "htmlText": "For families."}}),
              _js({"__typename": "StaysPdpAmenitiesDetails", "seeAllAmenitiesGroups": [
                  {"__typename": "AmenityItemsGroup", "amenities": [
                      {"__typename": "AmenityItem", "available": True, "title": "Hot tub"}]}]})]
    if hero:
        parts.append('{"heroMedia":' + _js(_hero(listing, hero_captions)) + "}")
    parts.append(_js({"__typename": "PhotoTourModalSection", "mediaItems": [
        {"__typename": "Image", "baseUrl": t["url"], "imageMetadata": {"caption": t["caption"]}} for t in tour]}))
    return f"<html><script>{ROOM} " + " ".join(parts) + "</script></html>"


def test_photo_one_is_the_real_cover_and_numbers_follow_the_listing_order():
    g = al.parse(_page(), ROOM)
    assert g["photos"][0]["url"] == FX["og_image"].split("?")[0], "#1 must be the cover guests see"
    assert [p["url"] for p in g["photos"]] == FX["listing_order"]
    assert [p["position"] for p in g["photos"]] == list(range(1, 13))


def test_each_photo_keeps_its_own_caption():
    by_url = {t["url"]: t["caption"] for t in FX["tour_order"]}
    for p in al.parse(_page(), ROOM)["photos"]:
        assert p["caption"] == by_url[p["url"]]


def test_the_scorer_is_told_the_real_cover():
    """images.json order 1 is what aggregate() treats as the current cover."""
    images = al.to_images(al.validate(al.parse(_page(), ROOM)))
    first = min(images["data"], key=lambda p: p["order"])
    assert first["url"].split("?")[0] == FX["og_image"].split("?")[0]


def test_a_cover_that_is_not_the_first_listing_photo_is_unreadable():
    """If the page's cover and its listing order disagree, the order is not trusted."""
    with pytest.raises(al.PageUnreadable, match="photo order"):
        al.parse(_page(og=FX["listing_order"][3]), ROOM)


def test_a_page_without_the_listing_order_is_unreadable_not_tour_ordered():
    with pytest.raises(al.PageUnreadable, match="photo order"):
        al.parse(_page(hero=False), ROOM)


def test_without_og_image_the_listing_order_still_wins():
    g = al.parse(_page(og=None), ROOM)
    assert [p["url"] for p in g["photos"]] == FX["listing_order"]


def test_a_photo_only_in_the_tour_is_reported_as_not_assessed():
    g = al.parse(_page(listing=FX["listing_order"][:11]), ROOM)
    assert g["returned"] == 11 and g["reported"] == 12 and g["complete"] is False
    src = al.to_images(g)["_source"]
    assert src["complete"] is False and src["reported"] == 12
    assert "the last 1 were not assessed" in ap.source_note(src)


def test_a_caption_missing_from_the_tour_comes_from_the_listing_list():
    tour = [t if t["url"] != FX["listing_order"][0] else {**t, "caption": ""} for t in FX["tour_order"]]
    g = al.parse(_page(tour=tour, hero_captions={FX["listing_order"][0]: "Private hot tub at dusk"}), ROOM)
    assert g["photos"][0]["caption"] == "Private hot tub at dusk"
