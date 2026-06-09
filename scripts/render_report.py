#!/usr/bin/env python3
"""
render_report.py — render the optimized listing into HTML + Markdown + paste block.

Input  : a result JSON (produced by the optimizer/SKILL) — see SKILL.md for the shape.
Output : <out-base>/<listing-slug>/<date>/{report.html, report.md, paste-block.txt}
         Default out-base = ~/Desktop/Listing Optimizer  (local-only while testing).

Templates: .claude/skills/listing-optimizer/output-templates/{report.html.j2, report.md.j2}
Branding : branding.json (project root) unless --branding given.

ZERO-PRICING GUARDRAIL: after rendering, every output is scanned for price/ADR/
min-stay terms. If any are found, the render FAILS — nothing leaves with pricing in it.

Usage:
  python scripts/render_report.py --data result.json --listing-slug my-listing \
      --date 2026-06-06
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / ".claude" / "skills" / "listing-optimizer" / "output-templates"
DEFAULT_OUT_BASE = Path.home() / "Desktop" / "Listing Optimizer"

# Words/patterns that must NEVER appear in a deliverable (zero-pricing rule).
# Deliberately does NOT match bare "rate"/"rates"/"nightly"/"per" — the funnel
# section legitimately uses "Booking rate", "Click-through rate", "occupancy".
# Only price-bearing forms match. Tested in tests/test_guardrail.py.
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

# Report-only scan: blocks actual price DATA (numbers/currency/rates) but ALLOWS the
# bare meta-words "pricing"/"revenue"/"rate" so the qualitative Diagnostics & Handoff
# section can say e.g. "this is a pricing/availability lever — run revenue-manager".
# The paste content (paste-block.txt) is always scanned with the strict PRICING_RE.
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


def build_paste_block(data: dict) -> str:
    o = data.get("optimized", {})
    lines = []
    lines.append(f"=== {data.get('listing', {}).get('name', 'Listing')} — Optimized Content ===")
    lines.append(f"(Generated {data.get('run_date', '')} · paste into your PMS; it syncs to your channels)\n")
    lines.append("--- TITLE ---")
    lines.append(o.get("title", "").strip() + "\n")
    sc = o.get("summary_char_count")
    lines.append(f"--- SUMMARY ({sc} chars) ---" if sc else "--- SUMMARY ---")
    lines.append(o.get("summary", "").strip() + "\n")
    lines.append("--- THE SPACE ---")
    lines.append(o.get("the_space", "").strip() + "\n")
    caps = o.get("captions", [])
    if caps:
        lines.append("--- PHOTO CAPTIONS (recommended order) ---")
        for c in caps:
            order = c.get("order")
            subj = c.get("subject", "")
            tag = f"[#{order} {subj}]" if order is not None else f"[{subj}]"
            lines.append(f"{tag} {c.get('caption', '').strip()}")
    return "\n".join(lines).rstrip() + "\n"


def guardrail_scan(name: str, text: str, rx=PRICING_RE) -> list[str]:
    return [f"{name}: '{m.group(0)}'" for m in rx.finditer(text)]


def main():
    ap = argparse.ArgumentParser(description="Render optimized listing to HTML+MD+paste block.")
    ap.add_argument("--data", required=True, help="result JSON from the optimizer")
    ap.add_argument("--listing-slug", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD (run date)")
    ap.add_argument("--out-base", default=str(DEFAULT_OUT_BASE))
    ap.add_argument("--branding", default=str(ROOT / "branding.json"))
    args = ap.parse_args()

    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    # branding.json is per-user (gitignored); fall back to the shipped template.
    bpath = Path(args.branding)
    if not bpath.exists():
        bpath = ROOT / "branding.example.json"
    branding = json.loads(bpath.read_text(encoding="utf-8"))
    data["branding"] = branding

    # autoescape must fire for the HTML template (files end in .html.j2, which
    # select_autoescape(["html"]) does NOT match) — escape HTML, not Markdown.
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=lambda name: bool(name) and name.endswith((".html.j2", ".html", ".xml.j2", ".xml")),
        trim_blocks=True, lstrip_blocks=True,
    )
    html = env.get_template("report.html.j2").render(data=data)
    md = env.get_template("report.md.j2").render(data=data)
    paste = build_paste_block(data)

    # ── Guardrail ──
    # Paste content goes verbatim into the PMS → word-STRICT (no price words at all).
    # The report carries a qualitative diagnostics/handoff section → scan for price
    # NUMBERS only (the meta-words "pricing"/"revenue-manager" are allowed there).
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
