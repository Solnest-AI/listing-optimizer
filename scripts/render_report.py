#!/usr/bin/env python3
"""
render_report.py — render the optimized listing into HTML + Markdown + paste block.

Input  : a result JSON (authored by the agent) — see SKILL.md for the shape.
Output : <out-base>/<listing-slug>/<date>/{report.html, report.md, paste-block.txt}
         Default out-base = ~/Desktop/Listing Optimizer

Templates: .claude/skills/listing-optimizer/output-templates/{report.html.j2, report.md.j2}
Branding : branding.json (project root) unless --branding given.

ZERO-PRICING GUARDRAIL: every output is scanned for price/ADR/min-stay terms after
rendering. Any hit fails the render — nothing leaves with pricing in it.

Usage:
  python scripts/render_report.py --data result.json --workdir output/<date>/<slug> \
      --listing-slug my-listing --date 2026-06-06
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

import ale
import artifacts

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / ".claude" / "skills" / "listing-optimizer" / "output-templates"
DEFAULT_OUT_BASE = Path.home() / "Desktop" / "Listing Optimizer"
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

# Strict scan for paste content: no price words at all. Deliberately does NOT match bare
# "rate"/"nightly"/"per" — the funnel section legitimately says "Booking rate".
PRICING_RE = re.compile(r"""(?ix)
    (?:
        \bpriced?\b | \bpricing\b | \badr\b | \brevpar\b | \brevenue\b
      | \bper[\s-]?night\b | /\s*night\b
      | \b(?:nightly|daily|average\s+daily)\s+rate\b | \brate\s*/\s*night\b
      | \bmin(?:imum)?[\s-]?stay\b
      | \bmin(?:imum)?\s+(?:\d+[\s-]?)?nights?\b
      | \b\d+[\s-]?nights?\s+min(?:imum)?\b
      | [$€£¥₹]\s?\d
      | \b(?:USD|CAD|EUR|GBP|AUD|NZD|MXN)\s*\d
      | \b\d[\d,.]*\s*/\s*night\b
      | \b\d[\d,.]*\s+(?:a|per)\s+(?:night|stay|nt)\b
      | \b\d[\d,.]*\s*(?:-|–|to)\s*\d[\d,.]*\s+(?:a\s+|per\s+|/\s*)?(?:night|stay|nightly)\b
      | \bdollars?\b
    )
""")

# Report scan: blocks price DATA (numbers/currency/rates) but allows the meta-words
# "pricing"/"revenue" so the qualitative Diagnostics & Handoff section can name a
# pricing lever. memory.py imports this same pattern for the history record.
PRICE_NUMBER_RE = re.compile(r"""(?ix)
    (?:
        [$€£¥₹]\s?\d
      | \b(?:USD|CAD|EUR|GBP|AUD|NZD|MXN)\s*\d
      | \b\d[\d,.]*\s*/\s*night\b
      | \b\d[\d,.]*\s+per\s+night\b
      | \b(?:nightly|daily|average\s+daily)\s+rate\b
      | \b\d+[\s-]?nights?\s+min(?:imum)?\b
      | \bmin(?:imum)?\s+(?:\d+[\s-]?)?nights?\b
      | \b\d[\d,.]*\s+(?:a|per)\s+(?:night|stay|nt)\b
      | \b\d[\d,.]*\s*(?:-|–|to)\s*\d[\d,.]*\s+(?:a\s+|per\s+|/\s*)?(?:night|stay|nightly)\b
      | \bdollars?\b
    )
""")


# Paste copy rules (references/airbnb-field-limits.md): contact details break Airbnb sync
# and policy; the title guidelines ban emojis, ALL CAPS and repeated special characters.
CONTACT_RE = re.compile(r"""(?ix)
    (?P<email>[\w.+-]+@[\w-]+\.[\w.-]+)
  | (?P<url>https?://\S+ | \bwww\.\S+ | \b[a-z0-9-]+\.(?:com|net|org|ca|co|io|us|info|biz|rentals|house|homes)\b)
  | (?P<phone>(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b)
""")
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")
REPEATED_RE = re.compile(r"([!?*~#$%^&+=|•·★_-])\1")


def _check_contact(name: str, text: str) -> None:
    m = CONTACT_RE.search(text)
    if m:
        kind = "phone" if m.group("phone") else "email" if m.group("email") else "URL"
        raise ValueError(f"optimized.{name} contains a {kind} ('{m.group(0)}'); Airbnb rejects "
                         f"contact details in listing copy")


def _check_title(title: str) -> None:
    letters = [c for c in title if c.isalpha()]
    if EMOJI_RE.search(title):
        raise ValueError("optimized.title contains an emoji or symbol; Airbnb titles ban them")
    # Share of capitals, not per-word: acronyms like UHNBC or YVR are allowed.
    if len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.6:
        raise ValueError("optimized.title uses ALL CAPS; Airbnb asks for sentence case")
    if REPEATED_RE.search(title):
        raise ValueError("optimized.title repeats a special character; Airbnb titles ban that")


def check_caption_orders(data: dict, workdir: Path) -> list:
    """Mark captions whose photo order is not in this run's gallery as new photos to
    create (e.g. the map the report asks for). A mistyped order surfaces the same way,
    labelled in the paste block, instead of silently captioning the wrong photo."""
    src = workdir / "images.json"
    if not src.exists() or artifacts.excluded(workdir, "images.json"):
        return []
    raw = json.loads(src.read_text(encoding="utf-8"))
    items = raw.get("data") if isinstance(raw, dict) else raw
    orders = {p.get("order") for p in items or [] if isinstance(p, dict)}
    new = []
    for c in (data.get("optimized") or {}).get("captions") or []:
        if c.get("order") not in orders:
            c["new_photo"] = True
            new.append(c["order"])
    return sorted(new)


def build_paste_block(data: dict) -> str:
    o = data.get("optimized", {})
    lines = [f"=== {data.get('listing', {}).get('name', 'Listing')}: Optimized Content ===",
             f"(Generated {data.get('run_date', '')} · paste into your PMS; it syncs to your channels)\n",
             "--- TITLE ---", o.get("title", "").strip() + "\n"]
    sc = o.get("summary_char_count")
    lines.append(f"--- SUMMARY ({sc} chars) ---" if sc else "--- SUMMARY ---")
    lines.append(o.get("summary", "").strip() + "\n")
    lines.append("--- THE SPACE ---")
    lines.append(o.get("the_space", "").strip() + "\n")
    caps = o.get("captions", [])
    if caps:
        lines.append("--- PHOTO CAPTIONS (recommended order) ---")
        for c in caps:
            order, subj = c.get("order"), c.get("subject", "")
            if c.get("new_photo"):
                tag = f"[NEW PHOTO to create: {subj}]"
            else:
                tag = f"[#{order} {subj}]" if order is not None else f"[{subj}]"
            lines.append(f"{tag} {c.get('caption', '').strip()}")
    return "\n".join(lines).rstrip() + "\n"


def guardrail_scan(name: str, text: str, rx=PRICING_RE) -> list[str]:
    return [f"{name}: '{m.group(0)}'" for m in rx.finditer(text)]


# ── Machine blocks: assembled from disk, never retyped by the agent ───
# Measured on two real runs, ~47% of result.json was the agent hand-copying numbers that
# already sat in photo_scores.json / comps.json / occupancy.json / cadence.json. Every
# retyped number is a chance to miscopy. The agent authors only what it reasoned about.
def _photos_block(ps: dict) -> dict:
    return {
        "hero": ps.get("hero"),
        "recommended_top5_order": ps.get("recommended_top5_order") or [],
        "top5_beats": ps.get("top5_beats") or [],
        "reshoot": ps.get("reshoot") or [],
        "restage": ps.get("restage") or [],
        "gaps": ps.get("gaps") or [],
        "coverage_note": ps.get("coverage_note"),
        "unranked": [f.get("order") for f in (ps.get("failed") or [])],
        "scored": [{"order": p.get("order"), "url": p.get("url"),
                    "subject": p.get("subject"), "subject_kind": p.get("subject_kind"),
                    "avg": p.get("avg")}
                   for p in (ps.get("photos") or []) if p.get("scored")],
    }


def _comps_block(c: dict) -> dict:
    # amenity_gaps stays the agent's call: it needs the subject's amenity list to decide
    # what is genuinely missing vs. present-but-buried.
    return {
        "comp_count": c.get("comp_count"),
        "ranking_basis": c.get("ranking_basis"),
        "top": [{"name": t.get("name"), "airbnb_url": t.get("airbnb_url"),
                 "bedrooms": t.get("bedrooms"), "baths": t.get("baths"),
                 "guests": t.get("guests"), "ratings": t.get("ratings") or {},
                 "performance": t.get("performance") or {}}
                for t in (c.get("top_comps") or [])],
        "title_patterns": c.get("comp_title_samples") or [],
    }


# `funnel` is deliberately NOT here: it is the agent's normalized view of an optional
# RankBreeze pull, and no raw funnel.json shape has been verified to auto-merge from.
# Blocks in VERBATIM_BLOCKS carry third-party evidence (competitor titles) and skip the
# writing-rule pass; every other block is our own tooling's prose and gets it.
VERBATIM_BLOCKS = {"comps"}
MACHINE_BLOCKS = {
    "photos": ("photo_scores.json", _photos_block),
    "comps": ("comps.json", _comps_block),
    "occupancy": ("occupancy.json", lambda x: x.get("report_block") or x),
    "cadence": ("cadence.json", lambda x: {"due": x.get("due") or []}),
}


def merge_machine_blocks(data: dict, workdir: Path) -> list[str]:
    """Fill any MISSING machine block from the working dir. Never overwrites the agent's
    own value. Returns the names filled, for reporting."""
    filled = []
    status = artifacts.run_status(workdir)
    if status.get("status") in ("running", "failed"):
        raise ValueError("pipeline is incomplete or failed; rerun before rendering")
    for key, expected in (("listing_slug", (data.get("listing") or {}).get("slug")),
                          ("run_date", data.get("run_date"))):
        if status.get(key) is not None and status[key] != expected:
            raise ValueError(f"pipeline {key} does not match result; use the correct workdir")
    if status:
        data["data_gaps"] = [f"{s['name']}: {s['detail']}" for s in status.get("steps", [])
                             if s.get("status") == "FAILED"]
    for key, (fname, shape) in MACHINE_BLOCKS.items():
        if artifacts.excluded(workdir, fname):
            data.pop(key, None)
            continue
        src = workdir / fname
        if not src.exists():
            continue
        try:
            block = shape(json.loads(src.read_text(encoding="utf-8")))
            if key not in VERBATIM_BLOCKS:
                block = normalize_prose(block)
        except (OSError, ValueError, AttributeError, TypeError) as e:
            sys.stderr.write(f"[render_report] WARNING: {fname} unreadable or unexpected shape ({e}) — skipped\n")
            continue
        existing = data.get(key)
        if not isinstance(existing, dict):
            data[key] = block
            filled.append(key)
        else:
            # Merge key-by-key so the agent can author one field of a block (comps.
            # amenity_gaps) and still inherit the machine-generated rest.
            added = [k for k, v in block.items() if k not in existing or existing[k] in (None, [], {}, "")]
            for k in added:
                existing[k] = block[k]
            if added:
                filled.append(f"{key}({','.join(added)})")
    return filled


def validate_result(data: dict) -> None:
    """Reject unusable paste copy before creating any deliverables."""
    if not isinstance(data, dict) or not isinstance(data.get("listing"), dict):
        raise ValueError("result must contain a listing object")
    if not isinstance(data["listing"].get("name"), str) or not data["listing"]["name"].strip():
        raise ValueError("listing.name is required")
    optimized = data.get("optimized")
    if not isinstance(optimized, dict):
        raise ValueError("optimized copy is required")
    for key, limit in (("title", 50), ("summary", 500), ("the_space", None)):
        value = optimized.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"optimized.{key} must be nonempty text")
        optimized[key] = value.strip()
        if limit and len(optimized[key]) > limit:
            raise ValueError(f"optimized.{key} exceeds {limit} characters")
    captions = optimized.get("captions", [])
    if not isinstance(captions, list):
        raise ValueError("optimized.captions must be a list")
    orders = set()
    for c in captions:
        if (not isinstance(c, dict) or type(c.get("order")) is not int
                or c["order"] < 0 or c["order"] in orders
                or not isinstance(c.get("caption"), str) or not c["caption"].strip()
                or len(c["caption"]) > 250):
            raise ValueError("captions need unique photo orders and 1..250 characters")
        orders.add(c["order"])
    for key in ("title", "summary", "the_space"):
        _check_contact(key, optimized[key])
    for c in captions:
        _check_contact(f"captions[#{c['order']}]", c["caption"])
    _check_title(optimized["title"])
    optimized["summary_char_count"] = len(optimized["summary"])
    card = data.get("ale_scorecard")
    if card is not None:
        if not isinstance(card, list) or not all(isinstance(r, dict) for r in card):
            raise ValueError("ale_scorecard must be a list of rows")
        for row in card:
            row["dimension"] = ale.canonical_dimension(row.get("dimension"))
            score = row.get("score")
            if type(score) not in (int, float) or not 0 <= score <= 5:
                raise ValueError(f"ale_scorecard {row['dimension']}: score must be a number 0..5")
        if sorted(r["dimension"] for r in card) != sorted(ale.DIMENSIONS):
            raise ValueError("ale_scorecard must cover each of the 7 dimensions exactly once: "
                             + ", ".join(ale.DIMENSIONS))
        card.sort(key=lambda r: ale.DIMENSIONS.index(r["dimension"]))
        # Derived, never retyped: memory.py stores the same mean for the trend line.
        data["ale_total"] = round(sum(r["score"] for r in card) / len(card), 2)


def normalize_prose(value, key=""):
    """Apply the no-em-dash writing rule to AGENT-AUTHORED text, preserving URLs.

    Only ever run on result.json before machine blocks are merged: competitor titles and
    other evidence pulled from disk must stay verbatim."""
    if isinstance(value, dict):
        return {k: normalize_prose(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_prose(v, key) for v in value]
    if isinstance(value, str) and not key.endswith("url"):
        return _replace_em_dashes(value)
    return value


def _replace_em_dashes(text: str) -> str:
    """'Sleeps 6 — hot tub' -> 'Sleeps 6. Hot tub'. The old ". " swap left a lowercase
    fragment after the period, a doubled period after existing punctuation, and a dangling
    ". " at the end of a string."""
    parts = re.split(r"\s*—\s*", text)
    out = parts[0]
    for nxt in parts[1:]:
        out = out.rstrip()
        if not nxt:                       # dash at the very end
            out = out if out.endswith((".", "!", "?")) or not out else out + "."
            continue
        joiner = " " if out.endswith((".", "!", "?", ":", ";", ",")) or not out else ". "
        out += joiner + nxt[0].upper() + nxt[1:]
    return out


def main():
    ap = argparse.ArgumentParser(description="Render optimized listing to HTML+MD+paste block.")
    ap.add_argument("--data", required=True, help="result JSON from the optimizer")
    ap.add_argument("--listing-slug", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD (run date)")
    ap.add_argument("--out-base", default=str(DEFAULT_OUT_BASE))
    ap.add_argument("--branding", default=str(ROOT / "branding.json"))
    ap.add_argument("--workdir", default=None,
                    help="output/<DATE>/<SLUG> — merge photos/comps/occupancy/cadence from "
                         "disk. Anything already in --data wins.")
    args = ap.parse_args()
    try:
        date.fromisoformat(args.date)
        if not SLUG_RE.fullmatch(args.listing_slug):
            raise ValueError("invalid listing slug")
        data = normalize_prose(json.loads(Path(args.data).read_text(encoding="utf-8")))
        validate_result(data)
        if data.get("run_date") != args.date or data["listing"].get("slug", args.listing_slug) != args.listing_slug:
            raise ValueError("result listing/date do not match the requested output")
        if args.workdir:
            new_photos = check_caption_orders(data, Path(args.workdir))
            if new_photos:
                print(f"[render_report] captions for photos not in the gallery, labelled NEW PHOTO: "
                      f"{new_photos} (check the order if that was not intended)")
            merged = merge_machine_blocks(data, Path(args.workdir))
            if merged:
                print(f"[render_report] merged from disk: {', '.join(merged)}")
    except (OSError, ValueError) as e:
        sys.exit(f"[render_report] invalid result: {e}")
    # branding.json is per-user (gitignored); fall back to the shipped example.
    bpath = Path(args.branding)
    if not bpath.exists():
        bpath = ROOT / "branding.example.json"
    data["branding"] = json.loads(bpath.read_text(encoding="utf-8"))

    # Templates end in .html.j2, which select_autoescape(["html"]) does NOT match, so
    # decide autoescape by suffix: escape HTML, not Markdown.
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=lambda name: bool(name) and name.endswith((".html.j2", ".html")),
        trim_blocks=True, lstrip_blocks=True,
    )
    html = env.get_template("report.html.j2").render(data=data)
    md = env.get_template("report.md.j2").render(data=data)
    paste = build_paste_block(data)

    # Paste content goes verbatim into the PMS: word-strict. Reports carry a qualitative
    # handoff section: numbers-only scan.
    hits = (guardrail_scan("paste-block.txt", paste, PRICING_RE)
            + guardrail_scan("report.html", html, PRICE_NUMBER_RE)
            + guardrail_scan("report.md", md, PRICE_NUMBER_RE))
    if hits:
        sys.stderr.write("[render_report] ❌ ZERO-PRICING GUARDRAIL TRIPPED — pricing/min-stay found:\n")
        for h in hits[:20]:
            sys.stderr.write(f"   - {h}\n")
        sys.exit("[render_report] refusing to write deliverables with pricing in them. "
                 "(No bypass — fix the copy and re-run.)")

    out_dir = Path(args.out_base).expanduser() / args.listing_slug / args.date
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.html").write_text(html, encoding="utf-8")
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    (out_dir / "paste-block.txt").write_text(paste, encoding="utf-8")

    print(f"[render_report] ✅ wrote 3 deliverables → {out_dir}")
    print(f"   report.html ({len(html)} B) · report.md ({len(md)} B) · paste-block.txt ({len(paste)} B)")
    print("   zero-pricing guardrail: PASSED (0 hits)")


if __name__ == "__main__":
    main()
