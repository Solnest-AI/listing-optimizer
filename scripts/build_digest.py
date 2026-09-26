#!/usr/bin/env python3
"""
build_digest.py — collapse the raw pipeline files into the ONE file the optimizer reads.

The raw files total ~115-350KB on a real listing. This collapses them to ~25KB with no loss
the ALE / SB7 rubrics care about.

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

import ale
import amenities
import artifacts

REVIEW_CAP = 20
REVIEW_CHARS = 700
SUBJECT_TRUNC = 6000
RAW_FILES = ("subject.json", "comps.json", "photo_scores.json", "reviews.json")


def _status(d: Path) -> dict:
    try:
        return artifacts.run_status(d)
    except (OSError, ValueError) as e:
        print(f"[digest] WARNING: pipeline_status.json is unreadable ({e}); treating every "
              f"file as usable", file=sys.stderr)
        return {}


def _read(d: Path, name: str):
    status = _status(d)
    if status.get("status") in ("running", "failed") or name in status.get("excluded_files", []):
        return None
    p = d / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"[digest] WARNING: {name} is unreadable ({e}) — section omitted", file=sys.stderr)
        return None


def _lit(s):
    """Hospitable nests review bodies as python-repr strings in some payloads."""
    if isinstance(s, dict):
        return s
    try:
        return ast.literal_eval(s) if isinstance(s, str) else {}
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return {}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _scale_divisor(review: dict) -> float:
    """How much to divide this review's category ratings by to land on a 0-5 scale.

    Channels do NOT share a scale: airbnb and direct rate categories 1-5, booking 1-10
    (rating_platform_original / rating = 2.0 exactly). Averaging them raw produced a
    cleanliness of 5.5/5. The divisor comes from the review's OWN payload, with a >5
    category as the fallback signal; anything else trips the SCALE WARNING below.

    Hospitable zero-fills every category key a channel does not use, which is why a 0
    is treated as unrated. VRBO (`homeaway`) is connected but its reviews never reach
    this API, so VRBO guest language is a real gap, not an absent channel.
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
    status = _status(d)
    problems = [s for s in status.get("steps", []) if s.get("status") == "FAILED"]
    if problems:
        A("DATA GAPS: " + "; ".join(f"{s['name']}: {s['detail']}" for s in problems))
    addr = s.get("address") if isinstance(s.get("address"), dict) else {}
    A("address: " + (addr.get("display") or ", ".join(
        str(addr[k]) for k in ("street", "city", "state", "country") if addr.get(k)) or "unknown"))

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
    if c.get("comp_title_samples"):
        A("title_samples (competitor evidence, verbatim):")
        for t in c["comp_title_samples"][:10]:
            A(f"  - {' '.join(str(t).split())[:120]}")
    # Computed diff, not a frequency dump: the most common comp amenities are universal
    # basics, and PMS keys do not match AirROI labels, so the model cannot diff them by eye.
    if c.get("market_amenity_frequency"):
        gap = amenities.compare(s.get("amenities"), s.get("house_rules"),
                                c.get("market_amenity_frequency"), c.get("top_comps"),
                                f"{s.get('public_name') or ''} {s.get('summary') or ''}")

        def fmt(rows):
            return "; ".join(f"{g['amenity']} {g['pct']}% ({g['top_hits']}/{g['top_n']} top)"
                             for g in rows) or "none"

        A(f"missing_from_pms (in >={amenities.MISSING_MIN_PCT}% of comps, not in the PMS amenity "
          f"list or house rules): {fmt(gap['missing'][:15])}")
        A(f"have_but_title_summary_never_say (in <={amenities.DIFFERENTIATOR_MAX_PCT}% of comps): "
          f"{fmt(gap['unsurfaced'][:10])}")
        if gap["subject_list_empty"]:
            A("⚠️ The PMS amenity list is EMPTY, so every comp amenity reads as missing. "
              "Do not report amenity gaps from this; say the amenity list could not be read.")
        A("amenity note: missing_from_pms means unticked OR absent. Check the description "
          "before calling something missing; if it exists, the fix is ticking the checkbox "
          "(Airbnb search filters read checkboxes). Literal-text check on title+summary only.")

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
    if (p.get("duplicates") or {}).get("note"):
        A(f"duplicate check: {p['duplicates']['note']}")
    if p.get("distinct_beats"):
        A(f"distinct_beats_in_gallery: {p['distinct_beats']}")
    # The noise warning travels WITH the numbers: the model only ever reads this digest.
    if p.get("score_band"):
        A(f"score_band: {p['score_band']} — avgs are noisy (measured drift ~0.2, max 0.66). "
          f"A gap smaller than one band is NOT a real difference. Never tell the user one "
          f"photo beats another on a sub-band gap; rank on the band, not the decimal.")
    fb = d / "photo_fallback.json"
    if fb.exists():
        try:
            n = len(json.loads(fb.read_text(encoding="utf-8")).get("photos") or [])
        except (OSError, ValueError, AttributeError):
            n = "?"
        A(f"⚠️ PHOTO FALLBACK REQUIRED: {n} photo(s) have no score (Gemini unavailable or "
          f"failed). Score them yourself from {fb} (SKILL.md section 2b), write "
          f"agent_photo_scores.json, then rerun the same run_pipeline command BEFORE writing "
          f"result.json. Do not recommend a cover or gallery order from an incomplete ranking.")
    A("gaps: " + json.dumps(p.get("gaps") or [], ensure_ascii=False))
    for ph in (p.get("photos") or []):
        if not ph.get("scored"):
            continue
        A(f"- #{ph.get('order')} avg={ph.get('avg')} [{ph.get('subject_kind') or '?'}] "
          f"{str(ph.get('subject'))[:60]} | flags={ph.get('flags')} | "
          f"season={ph.get('season')} people={ph.get('has_people')} | "
          f"cap={str(ph.get('caption') or '')[:60]}")

    # ── Reviews (aggregates span what was PULLED, labelled with the real window) ──
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
            if v is None or v == 0:  # 0 / None means NOT RATED, not a score of zero
                continue
            cat.setdefault(dr.get("type"), []).append(v / div)
        if not r.get("responded_at"):
            unanswered += 1
    shown = sorted(rv, key=lambda x: str(x.get("reviewed_at")), reverse=True)[:review_cap]
    for r in shown:
        pub = _lit(r.get("public"))
        # 700, not 320: complaints sit at the END of a review ("Only downside was...").
        A(f"- {str(r.get('reviewed_at'))[:10]} {pub.get('rating')}* "
          f"{' '.join(str(pub.get('review') or '').split())[:REVIEW_CHARS]}")
    private = [(str(r.get("reviewed_at"))[:10], " ".join(str(_lit(r.get("private")).get("feedback")).split()))
               for r in shown if _lit(r.get("private")).get("feedback")]
    if private:
        A("PRIVATE FEEDBACK (guest-to-host only: use it to find fixes and expectation gaps, "
          "NEVER quote or paraphrase it in public copy):")
        for when, text in private:
            A(f"- {when} {text[:400]}")
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
    # An average above 5 on a 0-5 scale means a channel arrived on a scale we could not
    # derive. Print the problem rather than a confident impossible number.
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
    # Formatted whole, never sliced: a fixed-length cut of serialized JSON drops whole
    # records (a 365-day calendar lost its later months at 1,500 chars).
    funnel = _read(d, "funnel.json")
    if funnel:
        A("\n# FUNNEL\n" + json.dumps(funnel, ensure_ascii=False))
    occ = _read(d, "occupancy.json")
    if isinstance(occ, dict) and occ:
        fw = occ.get("forward_window") or {}
        A(f"\n# OCCUPANCY (source: {occ.get('source')}; on the books from the run date, "
          f"not a finished month)")
        A(f"forward {fw.get('days')}d: {fw.get('occupancy_pct')}% ({fw.get('booked')} booked / "
          f"{fw.get('available')} open / {fw.get('blocked')} blocked / {fw.get('unknown', 0)} unknown); "
          f"reservations in window: {occ.get('upcoming_reservations', 'n/a')}")
        for month, m in (occ.get("monthly") or {}).items():
            A(f"- {month}: {m.get('occupancy_pct')}% ({m.get('booked')} booked / "
              f"{m.get('available')} open / {m.get('blocked')} blocked)")
        if occ.get("rankbreeze_crosscheck"):
            A("rankbreeze_crosscheck: " + json.dumps(occ["rankbreeze_crosscheck"], ensure_ascii=False))
    # One compact line per prior run. Raw JSON was cut mid-record at 1,500 chars, which
    # hid every run after the first and half of the first one's amenity gaps.
    prior = _read(d, "prior_runs.json")
    if isinstance(prior, list) and prior:
        A("\n# PRIOR RUNS (most recent first)")
        for r in prior[:3]:
            if not isinstance(r, dict):
                continue
            scores = ", ".join(f"{ale.canonical_dimension(x.get('dimension'))}={x.get('score')}"
                               for x in r.get("ale_scores") or [])
            gaps = " | ".join(str(g)[:140] for g in (r.get("amenity_gaps") or [])[:5])
            A(f"- {r.get('run_date')} season={r.get('season')} applied={r.get('applied')} "
              f"ale_total={r.get('ale_total')} title={json.dumps(r.get('title'), ensure_ascii=False)} "
              f"hero={r.get('photo_hero')} top5={r.get('photo_top5')} "
              f"occ_forward={r.get('occupancy_forward_pct')}")
            if scores:
                A(f"  scores: {scores}")
            if gaps:
                A(f"  amenity_gaps: {gaps}")

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
