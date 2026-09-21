#!/usr/bin/env python3
"""
build_digest.py — collapse the raw pipeline files into the ONE file the optimizer reads.

This used to be a ~100-line Python heredoc pasted inside SKILL.md, which meant the model
read it AND re-emitted it verbatim on every single run (~1.2k tokens in, ~1.2k out, plus
the standing risk of a transcription slip silently changing the digest). It is a script
now; the skill calls it in one line.

The raw files total ~350KB (~89k tokens) on a real listing and would be re-sent on every
remaining turn of the run. This collapses them to ~18KB (~4.5k tokens) with no loss the
ALE / SB7 rubrics care about.

Usage:
  python scripts/build_digest.py output/2026-09-20/my-listing
  python scripts/build_digest.py output/2026-09-20/my-listing --review-cap 20
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

import artifacts

REVIEW_CAP = 20
SUBJECT_TRUNC = 6000
RAW_FILES = ("subject.json", "comps.json", "photo_scores.json", "reviews.json")


def _read(d: Path, name: str):
    if artifacts.excluded(d, name):
        return None
    p = d / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[digest] WARNING: {name} is unreadable ({e}) — section omitted", file=sys.stderr)
        return None


def _lit(s):
    """Hospitable nests review bodies as python-repr strings in some payloads."""
    if isinstance(s, dict):
        return s
    try:
        return ast.literal_eval(s) if isinstance(s, str) else {}
    except Exception:
        return {}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _scale_divisor(review: dict) -> float:
    """How much to divide this review's category ratings by to land on a 0-5 scale.

    Channels do NOT share a scale, and averaging them raw produced impossible numbers: the
    boho-bliss report showed cleanliness 5.5/5 and location 5.45/5, while staff read 1.38
    and services 0.0 — a fabricated facilities crisis on a listing whose Airbnb cleanliness
    is 4-5. Measured across a real 101-review history:
        airbnb  (92) categories 1-5,  rating_platform_original / rating = 1.0
        booking  (8) categories 1-10, rating_platform_original / rating = 2.0 exactly
        direct   (1) categories 1-5,  = 1.0
    So the divisor comes from the review's OWN payload rather than a hardcoded channel
    table. Those three are the ONLY channels that reach us. Verified 2026-09-20 across all 8
    properties, every page: 334 reviews = airbnb 322 / booking 11 / direct 1.

    VRBO IS CONNECTED BUT ITS REVIEWS NEVER ARRIVE — do not read the absence as "no VRBO".
    Hospitable exposes VRBO as `homeaway`, and this account has a real homeaway API channel
    on 7 properties producing 35 reservations, plus 4 `vrbo.com/icalendar/*.ics` feeds on
    `platform: "ical"`. iCal is a calendar protocol: dates and blocks only, so those four can
    never carry a review. The homeaway API channel delivers reservations but returns no
    reviews from /properties/{id}/reviews (confirmed independently via the Hospitable MCP's
    own aggregation: the strings "vrbo"/"homeaway" appear 0 times in the full review dump),
    and reservation objects carry no review/rating field. So VRBO guest language is a REAL
    GAP in the optimizer's inputs, not an absent channel.

    If VRBO reviews ever start arriving: VRBO rates 1-5, so the derived factor should be 1.0
    — but that is UNVERIFIED, so check it against a real payload before trusting an average.
    A channel on some other scale hits the >5 fallback, and if even that fails
    `build_digest.py` prints a SCALE WARNING rather than a confident impossible number.

    Also note Hospitable emits ALL nine category keys on EVERY review and zero-fills the
    ones the channel does not use, which is why unrated must be dropped: `staff`,
    `facilities` and `services` were hard 0 on all 264 Airbnb reviews.
    """
    pub = _lit(review.get("public"))
    rating, original = _num(pub.get("rating")), _num(pub.get("rating_platform_original"))
    if rating and original and rating > 0:
        factor = original / rating
        # Only trust a clean 1x or 2x; anything else means the pair isn't a scale signal.
        if 0.95 <= factor <= 1.05:
            return 1.0
        if 1.95 <= factor <= 2.05:
            return 2.0
    # Fallback when the pair is missing: a category above 5 can only be a 10-scale.
    vals = [_num(dr.get("rating")) for dr in (_lit(review.get("private")).get("detailed_ratings") or [])]
    return 2.0 if any(v and v > 5 for v in vals) else 1.0


def build(d: Path, review_cap: int = REVIEW_CAP) -> str:
    out: list[str] = []
    A = out.append

    # ── Subject ───────────────────────────────────────────────────────
    s = (_read(d, "subject.json") or {}).get("data") or {}
    keys = ("name", "public_name", "summary", "description", "amenities",
            "capacity", "room_details", "house_rules")
    subject = {k: s.get(k) for k in keys}
    # Bound only prose. Never truncate serialized JSON and silently lose facts
    # appearing after a long description (capacity, amenities and house rules).
    for key, limit in (("description", SUBJECT_TRUNC), ("summary", 1200)):
        if isinstance(subject[key], str) and len(subject[key]) > limit:
            subject[key] = subject[key][:limit] + " [truncated; remaining text available in subject.json]"
    A("# SUBJECT\n" + json.dumps(subject, ensure_ascii=False))
    status = artifacts.run_status(d)
    problems = [s for s in status.get("steps", []) if s.get("status") == "FAILED"]
    if problems:
        A("DATA GAPS: " + "; ".join(f"{s['name']}: {s['detail']}" for s in problems))
    A("address: " + json.dumps(s.get("address") or {}, ensure_ascii=False)[:400])

    # ── Comps (demand-ranked, price-free) ─────────────────────────────
    c = _read(d, "comps.json") or {}
    A("\n# COMPS (ranked by demand; no pricing)")
    if c.get("fetch"):
        A(f"source: {c['fetch'].get('path')} ({c['fetch'].get('calls')} paid call(s)), "
          f"pool={c.get('comp_count')}")
    for t in (c.get("top_comps") or [])[:10]:
        r = t.get("ratings") or {}
        p = t.get("performance") or {}
        A(f"- {str(t.get('name') or '')[:70]} | {t.get('bedrooms')}br/{t.get('baths')}ba/"
          f"{t.get('guests')}g | {r.get('rating_overall')}* ({r.get('num_reviews')}rev) | "
          f"occ {p.get('ttm_occupancy')}")
    A("title_samples: " + json.dumps(c.get("comp_title_samples") or [], ensure_ascii=False)[:900])
    A("market_amenity_freq(top30): "
      + ", ".join(f"{a['amenity']}={a['pct']}%" for a in (c.get("market_amenity_frequency") or [])[:30]))

    # ── Photos ────────────────────────────────────────────────────────
    p = _read(d, "photo_scores.json") or {}
    A(f"\n# PHOTOS hero={p.get('hero')} top5={p.get('recommended_top5_order')} "
      f"reshoot={p.get('reshoot')} restage={p.get('restage')}")
    if p.get("top5_beats"):
        A(f"top5_beats: {p['top5_beats']}   (each slot must be a DIFFERENT beat)")
    if p.get("coverage_note"):
        # Surfaced deliberately: a photo that failed scoring is absent from the ranking,
        # so the report must not imply the whole gallery was ranked.
        A(f"coverage: {p['coverage_note']}")
    if p.get("usage"):
        A("Gemini usage: " + json.dumps(p["usage"]) + f"; cached photos={p.get('from_cache_count', 0)}")
    if p.get("failed"):
        A(f"UNRANKED (failed scoring): {[f.get('order') for f in p['failed']]}")
    if p.get("distinct_beats"):
        A(f"distinct_beats_in_gallery: {p['distinct_beats']}")
    # The per-photo avgs below are NOISY. Measured on identical input: mean drift 0.21,
    # max 0.66 on the 0-5 scale, and neither temperature 0 nor a seed removes it. Ranking
    # is done on the avg banded to `score_band`, so a sub-band gap is not a finding. This
    # instruction has to travel WITH the numbers: the model only ever reads this digest, so
    # a warning that lives only in photo_scores.json is invisible to it.
    if p.get("score_band"):
        A(f"score_band: {p['score_band']} — avgs are noisy (measured drift ~0.2, max 0.66). "
          f"A gap smaller than one band is NOT a real difference. Never tell the user one "
          f"photo beats another on a sub-band gap; rank on the band, not the decimal.")
    A("gaps: " + json.dumps(p.get("gaps") or [], ensure_ascii=False))
    for ph in (p.get("photos") or []):
        if not ph.get("scored"):
            continue
        A(f"- #{ph.get('order')} avg={ph.get('avg')} [{ph.get('subject_kind') or '?'}] "
          f"{str(ph.get('subject'))[:60]} | flags={ph.get('flags')} | "
          f"season={ph.get('season')} people={ph.get('has_people')} | "
          f"cap={str(ph.get('caption') or '')[:60]}")

    # ── Reviews ───────────────────────────────────────────────────────
    # The aggregates below span WHAT WAS PULLED, not necessarily the lifetime history. The
    # pull defaults to the 20 newest, so they must be labelled with their real window —
    # calling a 20-review average "(all)" would overstate it, and the unanswered count is
    # the one a host might act on.
    rev = _read(d, "reviews.json") or {}
    rv = rev.get("data") or []
    pull = rev.get("_pull") or {}
    lifetime = pull.get("total_available")
    complete = bool(pull.get("complete_history")) or (
        lifetime is not None and len(rv) >= int(lifetime))
    denom = lifetime if isinstance(lifetime, int) else len(rv)
    A(f"\n# REVIEWS ({min(len(rv), review_cap)} shown; {len(rv)} pulled of "
      f"{denom if denom else 0} lifetime)")
    cat: dict[str, list] = {}
    platforms: dict[str, int] = {}
    unanswered = 0
    for r in rv:
        platforms[str(r.get("platform") or "?")] = platforms.get(str(r.get("platform") or "?"), 0) + 1
        div = _scale_divisor(r)
        for dr in (_lit(r.get("private")).get("detailed_ratings") or []):
            v = _num(dr.get("rating"))
            # 0 / None means NOT RATED, not a score of zero. Counting them dragged every
            # average toward the floor (services read 0.0 purely because no channel uses it).
            if v is None or v == 0:
                continue
            cat.setdefault(dr.get("type"), []).append(v / div)
        if not r.get("responded_at"):
            unanswered += 1
    for r in sorted(rv, key=lambda x: str(x.get("reviewed_at")), reverse=True)[:review_cap]:
        pub = _lit(r.get("public"))
        A(f"- {str(r.get('reviewed_at'))[:10]} {pub.get('rating')}* "
          f"{str(pub.get('review') or '')[:320]}")
    scope = "all" if complete else f"last {len(rv)} only"
    # n= is part of the value: a category rated by 3 guests is not evidence of the same
    # weight as one rated by 19, and an unrated category is absent rather than zero.
    avgs = {k: {"avg": round(sum(v) / len(v), 2), "n": len(v)}
            for k, v in sorted(cat.items()) if v}
    A(f"category_avgs_0to5({scope}, unrated excluded): " + json.dumps(avgs))
    A(f"review_platforms: {platforms}   (category scales normalised to 0-5 per platform)")
    # Channel inventory is account-level; a capped property review sample cannot establish
    # which channels this listing uses or which reviews the provider can deliver.
    missing = [c for c in (_read(d, "channels.json") or {}).get("silent_channels", [])
               if c not in platforms]
    if missing:
        A(f"CHANNELS WITH NO REVIEW DATA: {missing} (account-level inventory). None appear "
          f"in the retrieved review sample. This is NOT the whole picture of account "
          f"channel coverage. Account connections do not prove this property uses each "
          f"channel; absence from this sample does not prove the PMS cannot supply those "
          f"reviews. Do not describe review counts or themes as covering every channel.")
    # Loud self-check on the exact bug that shipped for months: an average above 5 on a
    # 0-5 scale means a channel arrived on a scale we could not derive. Measured channels
    # are airbnb (1-5) and booking (1-10); anything else is unproven. Print the problem
    # rather than a confident impossible number.
    bad = {k: v["avg"] for k, v in avgs.items() if v["avg"] > 5}
    if bad:
        A(f"⚠️ SCALE WARNING: {bad} exceed the 0-5 scale, so at least one channel in "
          f"{list(platforms)} uses a rating scale this code cannot derive. DO NOT cite any "
          f"category average in the report or the ALE scorecard until that is fixed — quote "
          f"the review text instead. (Verified scales: airbnb 1-5, booking 1-10, direct 1-5.)")
    A(f"unanswered_reviews({scope}): {unanswered}")
    if not complete:
        A(f"NOTE: aggregates above cover the {len(rv)} most recent reviews, NOT all {denom}. "
          f"Report them as recent-window figures (e.g. \"{unanswered} of the last {len(rv)} "
          f"unanswered\"), never as a lifetime total. Re-pull with --all-reviews if the user "
          f"asks for the lifetime number.")

    # ── Funnel / occupancy / prior run ────────────────────────────────
    for name, title in (("funnel.json", "FUNNEL"), ("occupancy.json", "OCCUPANCY"),
                        ("prior_runs.json", "PRIOR RUNS")):
        x = _read(d, name)
        if x:
            A(f"\n# {title}\n" + json.dumps(x, ensure_ascii=False)[:1500])

    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Build the optimizer digest from the working dir.")
    ap.add_argument("workdir", help="output/<DATE>/<SLUG>")
    ap.add_argument("--review-cap", type=int, default=REVIEW_CAP)
    ap.add_argument("--out", default=None, help="default: <workdir>/digest.md")
    args = ap.parse_args()

    d = Path(args.workdir)
    if not d.is_dir():
        sys.exit(f"[digest] not a directory: {d}")
    text = build(d, args.review_cap)
    out = Path(args.out) if args.out else d / "digest.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    raw = sum((d / f).stat().st_size for f in RAW_FILES if (d / f).exists())
    size = out.stat().st_size
    ratio = f"{raw / size:.1f}x" if size else "n/a"
    print(f"[digest] {out} ({size} B from {raw} B raw — {ratio} smaller). Read ONLY this file.")


if __name__ == "__main__":
    main()
