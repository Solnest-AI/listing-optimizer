"""
photo_dupes.py — find near-duplicate photos in a listing gallery.

Two checks, both required:
  1. dHash (16x16 gradient hash) within DHASH_MAX of each other: same composition.
  2. The worst 8x8 block of the brightness-normalized 64x64 image differs by less than
     BLOCK_MAX: nothing was added to one part of the frame.

Calibrated on olde-town-ambler 2026-09-24, every pair checked by eye. True duplicates
(same shot, sometimes re-edited) sat at dHash <= 0.098 and block <= 0.55, with one
re-edited-sky outlier at 1.19. "Same room, now with people" versions sat at block >= 0.82,
but two wide shots were at dHash 0.09, inside the duplicate range, because the people are
small. Check 2 is what keeps a lifestyle shot from being called a duplicate. The error we
accept is missing a duplicate; the one we refuse is telling a host to delete their best photo.

Pillow is optional: without it, the check is skipped and says so.
"""
from __future__ import annotations

import asyncio
import io

import httpx

try:
    from PIL import Image
except ImportError:  # pragma: no cover - exercised only on installs without Pillow
    Image = None

DHASH_SIZE = 16
DHASH_MAX = 0.10
GRID = 64
BLOCKS = 8
BLOCK_MAX = 0.70


def available() -> bool:
    return Image is not None


def signature(im) -> tuple[int, list[float]]:
    gray = im.convert("L")
    small = gray.resize((DHASH_SIZE + 1, DHASH_SIZE), Image.LANCZOS).tobytes()
    bits = 0
    for r in range(DHASH_SIZE):
        for c in range(DHASH_SIZE):
            i = r * (DHASH_SIZE + 1) + c
            bits = (bits << 1) | (small[i] > small[i + 1])
    px = list(gray.resize((GRID, GRID), Image.LANCZOS).tobytes())
    mean = sum(px) / len(px)
    sd = (sum((x - mean) ** 2 for x in px) / len(px)) ** 0.5 or 1.0
    return bits, [(x - mean) / sd for x in px]


def _worst_block(a: list[float], b: list[float]) -> float:
    size = GRID // BLOCKS
    worst = 0.0
    for by in range(BLOCKS):
        for bx in range(BLOCKS):
            total = sum(abs(a[y * GRID + x] - b[y * GRID + x])
                        for y in range(by * size, (by + 1) * size)
                        for x in range(bx * size, (bx + 1) * size))
            worst = max(worst, total / (size * size))
    return worst


def is_duplicate(sig_a, sig_b) -> bool:
    hash_dist = bin(sig_a[0] ^ sig_b[0]).count("1") / (DHASH_SIZE * DHASH_SIZE)
    return hash_dist <= DHASH_MAX and _worst_block(sig_a[1], sig_b[1]) < BLOCK_MAX


def find_pairs(sigs: dict) -> list[tuple[int, int]]:
    """(earlier order, later order) for every duplicate pair, in gallery order."""
    orders = sorted(sigs)
    return [(a, b) for i, a in enumerate(orders) for b in orders[i + 1:]
            if is_duplicate(sigs[a], sigs[b])]


async def _signatures(photos: list[dict], concurrency: int = 8) -> tuple[dict, int]:
    sem = asyncio.Semaphore(concurrency)
    sigs, failed = {}, 0
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        async def one(p):
            nonlocal failed
            async with sem:
                try:
                    r = await client.get(p.get("thumbnail_url") or p["url"])
                    r.raise_for_status()
                    sigs[p["order"]] = signature(Image.open(io.BytesIO(r.content)))
                except Exception:  # one bad image must not sink the check
                    failed += 1
        await asyncio.gather(*(one(p) for p in photos))
    return sigs, failed


def check_gallery(photos: list[dict]) -> dict:
    """Duplicate pairs across the WHOLE gallery (not just the scored photos)."""
    if not available():
        return {"pairs": [], "checked": 0, "note": "duplicate check skipped: Pillow not installed"}
    sigs, failed = asyncio.run(_signatures(photos))
    note = f"checked {len(sigs)} of {len(photos)} photos for duplicates"
    if failed:
        note += f"; {failed} could not be downloaded"
    return {"pairs": [list(p) for p in find_pairs(sigs)], "checked": len(sigs), "note": note}
