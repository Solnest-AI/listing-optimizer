"""verify_report.py — check the writer's factual claims against the run's own files.

The renderer calls check() before writing anything, so a wrong number fails the render and
the writer fixes it in the same turn it wrote it. apres-arcade 2026-09-28: a report said
"About 27 photos have no caption" when the live gallery had 22, and it was caught only by
hand afterwards. Every rule here checks a claim with a single right answer on disk; judgement
(is this the right cover, is this copy persuasive) is never second-guessed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import artifacts

# "22 of 78 live photos have no caption", "About 27 photos have no caption", "Only 2 of 31 photos".
_PHOTO_COUNT = re.compile(r"(?i)\b(\d+)\s+(?:of\s+(?:the\s+|all\s+)?(\d+)\s+)?(?:live\s+|gallery\s+|kept\s+|airbnb\s+)*"
                          r"photos?\b([^.;:]{0,40})")
_NO_CAPTION = re.compile(r"(?i)\b(?:no|without\s+(?:a\s+)?|missing)\s+captions?\b|\buncaptioned\b")
_PHOTO_REF = re.compile(r"(?<![\w&])#(\d{1,3})\b")
_LAST_REVIEWS = re.compile(r"(?i)\b(?:of\s+)?(?:the\s+)?last\s+(\d+)\s+reviews?\b")
_NO_GUEST_ACCESS = re.compile(r"(?i)\bno\s+guest\s+access\b|\bguest\s+access\s+(?:section\s+)?is\s+(?:empty|missing)\b")


def _load(workdir: Path, name: str, status: dict):
    path = workdir / name
    if not path.exists() or artifacts.excluded(workdir, name, status):
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _claims(data: dict) -> list[tuple[str, str]]:
    """(where, text) for every sentence the writer states as fact about the listing."""
    out = []
    for row in data.get("ale_scorecard") or []:
        if isinstance(row, dict):
            out += [(f"ale_scorecard {row.get('dimension')}.{k}", str(row.get(k) or "")) for k in ("gap", "fix")]
    for i, g in enumerate(data.get("listing_gaps") or []):
        if isinstance(g, dict):
            out += [(f"listing_gaps[{i}].{k}", str(g.get(k) or "")) for k in ("issue", "fix")]
    for k, v in (data.get("diagnostics") or {}).items():
        out.append((f"diagnostics.{k}", str(v or "")))
    return [(w, t) for w, t in out if t.strip()]


def check(data: dict, workdir: Path) -> list[str]:
    """Problems with the writer's factual claims; empty when every checkable claim holds."""
    status = artifacts.run_status(workdir)
    images = _load(workdir, "images.json", status)
    items = (images.get("data") if isinstance(images, dict) else images) or []
    orders = {p.get("order") for p in items if isinstance(p, dict) and type(p.get("order")) is int}
    uncaptioned = sum(1 for p in items if isinstance(p, dict) and not str(p.get("caption") or "").strip())
    reviews = _load(workdir, "reviews.json", status) or {}
    pulled = len(reviews.get("data") or []) if isinstance(reviews, dict) else None
    live = _load(workdir, "live_gallery.json", status) or {}
    listing = live.get("listing") if isinstance(live, dict) and live.get("provider") == "airbnb" else None
    new_photos = {c.get("order") for c in (data.get("optimized") or {}).get("captions") or []
                  if isinstance(c, dict) and c.get("new_photo")}

    problems = []
    for where, text in _claims(data):
        if orders:
            for m in _PHOTO_COUNT.finditer(text):
                n, total, tail = int(m.group(1)), m.group(2), m.group(3)
                if total is not None and int(total) != len(orders):
                    problems.append(f"{where}: says {m.group(0).strip()!r}, but the gallery has {len(orders)} photos")
                if _NO_CAPTION.search(tail) and n != uncaptioned:
                    problems.append(f"{where}: says {n} photos have no caption, but {uncaptioned} of "
                                    f"{len(orders)} have none")
            for m in _PHOTO_REF.finditer(text):
                ref = int(m.group(1))
                if ref not in orders and ref not in new_photos:
                    problems.append(f"{where}: photo #{ref} is not in this gallery "
                                    f"(photos are #{min(orders)} to #{max(orders)})")
        if pulled:
            for m in _LAST_REVIEWS.finditer(text):
                if int(m.group(1)) != pulled:
                    problems.append(f"{where}: says the last {m.group(1)} reviews, but {pulled} were pulled")
        if listing is not None and _NO_GUEST_ACCESS.search(text):
            access = " ".join(str(listing.get("guest_access") or "").split())
            if len(access) >= 80:
                problems.append(f"{where}: says there is no Guest access, but the live listing has one "
                                f"({len(access)} characters)")
    return list(dict.fromkeys(problems))
