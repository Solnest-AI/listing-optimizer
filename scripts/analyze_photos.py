#!/usr/bin/env python3
"""
analyze_photos.py — score listing photos against the ALE photo rubric with Gemini.

Input  : Hospitable get_property_images JSON ({"data":[{url,thumbnail_url,caption,order}]})
         or a plain list of {url, caption, order}.
Vision : Gemini (REST generateContent, structured JSON). Key from GEMINI_API_KEY
         (env or project .env). If no key, writes a native-fallback manifest so the
         Claude Code session can score the photos with its own vision.
Output : aggregate JSON per references/photo-rubric.md — per-photo scores + tags +
         recommended top-5 order + hero + gaps + reshoot/restage flags.

ZERO-PRICING: photo analysis never references price/value-for-money.

Usage:
  python scripts/analyze_photos.py --photos images.json --limit 30 \
      --out output/2026-06-06/my-listing/photo_scores.json
"""
from __future__ import annotations

import argparse
import asyncio
import base64
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

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

RUBRIC = """You are scoring ONE short-term-rental listing photo for an Airbnb optimization
using the ALE framework (Amenities, Location, Experiences). Score each 0-5 (integers):
- technical: sharpness, exposure, level horizon, resolution (5 = pro quality)
- lighting: 5 = warm golden-hour or bright/airy; 0 = flat/dark/yellow
- staging: 5 = styled (textures, drinks, fire lit, made beds); 0 = clutter/cords/black TVs/empty
- composition: 5 = leading lines, depth, rule-of-thirds; 0 = awkward crop/dead space
- emotion: 5 = sells a MOMENT you want to be in; 0 = empty room
- ale_fit: 5 = clearly sells an Amenity, Location, or Experience; 0 = generic
Also tag:
- subject: short label (e.g. "hot tub", "arcade", "primary bedroom", "exterior", "map", "kitchen")
- season: one of winter|summer|shoulder|interior
- has_people: true if a person is visibly in the shot
- is_map: true ONLY if it is a map/location graphic with pins or drive-times
- flags: any of ["reshoot" (technically bad), "restage" (good room, bad styling: black TV/clutter/cords)]
- caption_note: one short phrase on what the caption should lead with (sell the moment; NEVER mention price)
Return ONLY the JSON object."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "season": {"type": "string", "enum": ["winter", "summer", "shoulder", "interior"]},
        "has_people": {"type": "boolean"},
        "is_map": {"type": "boolean"},
        "technical": {"type": "integer"},
        "lighting": {"type": "integer"},
        "staging": {"type": "integer"},
        "composition": {"type": "integer"},
        "emotion": {"type": "integer"},
        "ale_fit": {"type": "integer"},
        "flags": {"type": "array", "items": {"type": "string"}},
        "caption_note": {"type": "string"},
    },
    "required": ["subject", "season", "has_people", "is_map", "technical", "lighting",
                 "staging", "composition", "emotion", "ale_fit", "flags", "caption_note"],
}

_SCORE_KEYS = ("technical", "lighting", "staging", "composition", "emotion", "ale_fit")


# ── Key loading ───────────────────────────────────────────────────────
def load_gemini_key() -> str | None:
    # Project .env is loaded into os.environ at import — single, in-folder source.
    # No ~/.env read (that would be a hidden outside-the-folder dependency).
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY"):
        # Defensive: strip inline "# comment" remnants some .env editors leave in values.
        v = (os.environ.get(var) or "").split("#")[0].strip()
        if v:
            return v
    return None


# ── Photo input normalization ─────────────────────────────────────────
def load_photos(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("data") if isinstance(raw, dict) else raw
    photos = []
    for i, p in enumerate(items or []):
        photos.append({
            "order": p.get("order", i),
            "url": p.get("url"),
            "thumbnail_url": p.get("thumbnail_url"),
            "caption": p.get("caption", "") or "",
        })
    photos.sort(key=lambda x: x["order"])
    return photos


# ── Gemini scoring ────────────────────────────────────────────────────
async def _fetch_image(client: httpx.AsyncClient, url: str) -> tuple[str, str] | None:
    r = await client.get(url, timeout=30, follow_redirects=True)
    r.raise_for_status()
    ct = r.headers.get("content-type", "").split(";")[0].strip()
    if ct not in ("image/jpeg", "image/png", "image/webp"):
        ct = "image/png" if url.lower().endswith(".png") else "image/jpeg"
    return ct, base64.b64encode(r.content).decode()


async def _score_one(client, model, key, photo, sem) -> dict:
    out = {**photo, "scored": False}
    async with sem:
        try:
            img = await _fetch_image(client, photo["url"])
            if not img:
                out["error"] = "image fetch failed"
                return out
            mime, b64 = img
            body = {
                "contents": [{"parts": [
                    {"text": RUBRIC},
                    {"inline_data": {"mime_type": mime, "data": b64}},
                ]}],
                "generationConfig": {
                    "response_mime_type": "application/json",
                    "response_schema": _SCHEMA,
                    "temperature": 0.2,
                },
            }
            # Retry transient overload/rate-limit/5xx with exponential backoff.
            r = None
            for attempt in range(4):
                r = await client.post(
                    GEMINI_URL.format(model=model),
                    params={"key": key}, json=body, timeout=90,
                )
                if r.status_code not in (429, 500, 502, 503, 504):
                    break
                if attempt < 3:
                    await asyncio.sleep(2 * (2 ** attempt))  # 2, 4, 8s
            if r.status_code >= 400:
                out["error"] = f"gemini {r.status_code}: {r.text[:120]}"
                return out
            jr = r.json()
            cands = jr.get("candidates") or []
            if not cands:
                out["error"] = f"no candidates: {str(jr.get('promptFeedback'))[:120]}"
                return out
            fr = cands[0].get("finishReason")
            if fr in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"):
                out["error"] = f"blocked: {fr}"
                return out
            parts = (cands[0].get("content") or {}).get("parts") or []
            if not parts or "text" not in parts[0]:
                out["error"] = f"empty response (finishReason={fr})"
                return out
            data = json.loads(parts[0]["text"])
            data["avg"] = round(sum(data.get(k, 0) for k in _SCORE_KEYS) / len(_SCORE_KEYS), 2)
            out.update(data)
            out["scored"] = True
        except Exception as e:
            out["error"] = str(e)[:160]
    return out


async def score_photos(photos, model, key, concurrency=5) -> list[dict]:
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[_score_one(client, model, key, p, sem) for p in photos],
            return_exceptions=True,
        )
    out = []
    for p, r in zip(photos, results):
        if isinstance(r, Exception):
            out.append({**p, "scored": False, "error": f"unexpected: {str(r)[:140]}"})
        else:
            out.append(r)
    return out


# ── Aggregation: hero, top-5, gaps, flags ─────────────────────────────
def aggregate(scored: list[dict]) -> dict:
    ok = [p for p in scored if p.get("scored")]
    ok.sort(key=lambda p: p.get("avg", 0), reverse=True)

    # Hero = highest-avg shot that is NOT a map (maps belong in the top 10, never the cover).
    hero = next((p["order"] for p in ok if not p.get("is_map")), ok[0]["order"] if ok else None)

    # Top 5 covering distinct ALE beats: greedily take highest avg with a NEW subject,
    # and guarantee a people shot in the 5 if one exists.
    top5, seen_subjects = [], set()
    for p in ok:
        subj = (p.get("subject") or "").lower()
        if subj not in seen_subjects:
            top5.append(p["order"]); seen_subjects.add(subj)
        if len(top5) == 5:
            break
    if len(top5) < 5:
        for p in ok:
            if p["order"] not in top5:
                top5.append(p["order"])
            if len(top5) == 5:
                break
    # Experiences rule: a person in the top 5. If none, swap the WEAKEST-scored slot
    # for the best people shot (top5 is score-desc, but replace by score to be safe).
    people = [p for p in ok if p.get("has_people")]
    top5_has_people = any(
        next((q for q in ok if q["order"] == o), {}).get("has_people") for o in top5
    )
    if people and not top5_has_people and people[0]["order"] not in top5:
        order_avg = {p["order"]: p.get("avg", 0) for p in ok}
        weakest = min(top5, key=lambda o: order_avg.get(o, 0))
        top5 = [o for o in top5 if o != weakest] + [people[0]["order"]]

    gaps = []
    if not any(p.get("is_map") for p in ok):
        gaps.append("No map photo with pins + drive-times — create one (ALE Location, belongs in top 10).")
    if not any(next((q for q in ok if q["order"] == o), {}).get("has_people") for o in top5):
        gaps.append("No person in the top 5 — stage an Experience moment with people at the signature amenity/space.")
    seasons = {p.get("season") for p in ok}
    if "winter" in seasons and "summer" not in seasons:
        gaps.append("No summer photos — a seasonal property needs all-seasons coverage.")

    reshoot = sorted(p["order"] for p in ok if "reshoot" in (p.get("flags") or []))
    restage = sorted(p["order"] for p in ok if "restage" in (p.get("flags") or []))

    return {
        "photos": scored,
        "scored_count": len(ok),
        "hero": hero,
        "recommended_top5_order": top5,
        "gaps": gaps,
        "reshoot": reshoot,
        "restage": restage,
    }


def native_fallback_manifest(photos, out_path, reason):
    manifest = {
        "mode": "native_vision_fallback",
        "reason": reason,
        "instructions": ("No Gemini key found. The Claude Code session should score each "
                         "photo URL below against references/photo-rubric.md using its own "
                         "vision, then assemble the same output shape (photos/hero/"
                         "recommended_top5_order/gaps/reshoot/restage)."),
        "photos": [{"order": p["order"], "url": p["url"], "caption": p["caption"]} for p in photos],
    }
    out_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Score listing photos with Gemini (ALE rubric).")
    ap.add_argument("--photos", required=True, help="Hospitable images JSON or list")
    ap.add_argument("--limit", type=int, default=30, help="score the top-N photos by order")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--concurrency", type=int, default=3,
                    help="parallel Gemini calls (default 3 — raise carefully; free-tier keys rate-limit)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    photos = load_photos(Path(args.photos))[: args.limit]
    if not photos:
        sys.exit("[analyze_photos] no photos found in input")

    out_path = Path(args.out) if args.out else None
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)

    key = load_gemini_key()
    if not key:
        if not out_path:
            sys.exit("[analyze_photos] no GEMINI_API_KEY and no --out for fallback manifest")
        native_fallback_manifest(photos, out_path, "GEMINI_API_KEY not found in env or project .env (see .env.example)")
        print(f"[analyze_photos] no Gemini key — wrote native-fallback manifest ({len(photos)} photos) → {out_path}")
        return

    scored = asyncio.run(score_photos(photos, args.model, key, args.concurrency))
    if not any(p.get("scored") for p in scored):
        # All photos failed — do NOT emit a success-looking result the optimizer would trust.
        if out_path:
            out_path.write_text(json.dumps({"error": "all photos failed scoring", "photos": scored},
                                           indent=2, ensure_ascii=False), encoding="utf-8")
        sys.exit(f"[analyze_photos] ERROR: 0/{len(photos)} photos scored — "
                 f"see {out_path or '(no --out)'}; check the Gemini key / network.")
    result = aggregate(scored)
    result["model"] = args.model
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if out_path:
        out_path.write_text(text, encoding="utf-8")
        errs = [p for p in scored if not p.get("scored")]
        print(f"[analyze_photos] scored {result['scored_count']}/{len(photos)} "
              f"({len(errs)} errors) → {out_path}")
        print(f"  hero=#{result['hero']}  top5={result['recommended_top5_order']}  gaps={len(result['gaps'])}")
        if errs:
            print(f"  hint: {len(errs)} photo(s) failed (often transient rate limits) — "
                  f"re-run with --concurrency 2 to fill them in.")
    else:
        print(text)


if __name__ == "__main__":
    main()
