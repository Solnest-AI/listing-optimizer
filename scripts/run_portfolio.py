#!/usr/bin/env python3
"""Run the deterministic pipeline for every listed property, then summarize the portfolio.

    run_portfolio.py --date 2026-09-26                 # pipeline for every listed property
    run_portfolio.py --date 2026-09-26 --only a,b      # a subset (slugs)
    run_portfolio.py --date 2026-09-26 --resume        # skip listings already gathered today
    run_portfolio.py --date 2026-09-26 --summary       # after the writers: one portfolio report

One failed listing never stops the others. A Gemini daily quota stops new listings from
starting (they would all fall back to Claude-vision scoring, the most expensive path);
rerun with --resume after midnight Pacific and cached photo scores are kept.
Slugs are stable per property: state/slugs.json remembers them, config/properties.json can
pin one with "property_id", and a new property gets one from its internal name (the public
title is what this tool rewrites, so it cannot key history).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_usage  # noqa: E402
import console  # noqa: E402

PY = sys.executable
SLUGS = ROOT / "state" / "slugs.json"
CONFIG = ROOT / "config" / "properties.json"
WRITABLE = ("ready", "degraded")


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"^the\s+", "", text.strip())
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "listing"


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def assign_slugs(properties: list[dict], known: dict[str, str], config: dict) -> dict[str, str]:
    """property_id -> slug. Config pins win, then remembered slugs, then a new unique slug."""
    pinned = {v["property_id"]: k for k, v in config.items()
              if isinstance(v, dict) and isinstance(v.get("property_id"), str)}
    out = {}
    for p in properties:
        slug = pinned.get(p["id"]) or known.get(p["id"])
        if slug:
            out[p["id"]] = slug
    used = set(out.values())
    for p in properties:
        if p["id"] in out:
            continue
        base = slugify(p.get("name") or p.get("public_name") or "")
        slug, n = base, 2
        while slug in used:
            slug, n = f"{base}-{n}", n + 1
        out[p["id"]] = slug
        used.add(slug)
    return out


def discover(date: str, refresh: bool, runner=subprocess.run) -> list[dict]:
    """Every property on the account, saved once per date."""
    path = ROOT / "output" / date / "_portfolio" / "properties.json"
    if refresh or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        r = runner([PY, "scripts/hospitable_api.py", "properties", "--out", str(path)],
                   cwd=ROOT, capture_output=True, text=True, **console.TEXT)
        if r.returncode != 0 or not path.exists():
            raise SystemExit(f"[portfolio] could not list properties from Hospitable: "
                             f"{(r.stderr or r.stdout or '').strip()[-300:]}")
    data = _load(path, {})
    return data.get("data", []) if isinstance(data, dict) else []


def listing_facts(wd: Path) -> dict:
    """Measured cost and state of one listing's pipeline run, from the files it wrote."""
    st = _load(wd / "pipeline_status.json", {})
    comps = _load(wd / "comps.json", {})
    photos = _load(wd / "photo_scores.json", {})
    usage = photos.get("usage") or {}
    rec = {
        "status": st.get("status") or "not run",
        "failed_steps": [f"{s.get('name')}: {str(s.get('detail'))[:160]}" for s in st.get("steps") or []
                         if s.get("status") == "FAILED"],
        "airroi_calls": (comps.get("fetch") or {}).get("calls"),
        "gemini_requests": usage.get("api_calls"),
        "gemini_tokens": usage.get("totalTokenCount"),
        "photos_scored": photos.get("scored_count"),
        "quota_exhausted": usage.get("quota_exhausted"),
        "photos_pending": (wd / "photo_fallback.json").exists(),
        "has_digest": (wd / "digest.md").exists(),
        "owner_notes_in_digest": "# OWNER NOTES" in ((wd / "digest.md").read_text(encoding="utf-8-sig")
                                                     if (wd / "digest.md").exists() else ""),
    }
    rec["ready_to_write"] = rec["status"] in WRITABLE and rec["has_digest"] and not rec["photos_pending"]
    return rec


def run(args, runner=subprocess.run) -> dict:
    config = _load(CONFIG, {})
    config = config if isinstance(config, dict) else {}
    props = [p for p in discover(args.date, args.refresh, runner) if isinstance(p, dict) and p.get("id")]
    if not args.include_unlisted:
        props = [p for p in props if p.get("listed", True)]
    slugs = assign_slugs(props, _load(SLUGS, {}), config)
    _write_json(SLUGS, {**_load(SLUGS, {}), **slugs})
    only = {s.strip() for s in (args.only or "").split(",") if s.strip()}
    props = sorted((p for p in props if not only or slugs[p["id"]] in only), key=lambda p: slugs[p["id"]])
    if only - {slugs[p["id"]] for p in props}:
        print(f"[portfolio] not found: {', '.join(sorted(only - {slugs[p['id']] for p in props}))}")

    listings, stop = [], None
    for p in props:
        slug = slugs[p["id"]]
        wd = ROOT / "output" / args.date / slug
        cfg = config.get(slug) if isinstance(config.get(slug), dict) else {}
        rec = {"slug": slug, "property_id": p["id"], "name": p.get("name"),
               "season": cfg.get("season"), "owner_facts": len(cfg.get("owner_facts") or [])}
        before = listing_facts(wd)
        if args.resume and before["status"] in WRITABLE and not before["photos_pending"]:
            rec.update(before, action="resumed (already gathered today)", seconds=0)
        elif stop:
            rec.update(before, action=f"not started: {stop}", seconds=0)
        else:
            cmd = [PY, "scripts/run_pipeline.py", "--slug", slug, "--date", args.date, "--property-id", p["id"]]
            cmd += ["--refresh"] if args.refresh else []
            cmd += ["--no-cache"] if args.no_cache else []
            print(f"[portfolio] {slug} ...", flush=True)
            t0 = time.time()
            r = runner(cmd, cwd=ROOT, capture_output=True, text=True, **console.TEXT)
            log = ROOT / "output" / args.date / "_portfolio" / f"{slug}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text((r.stdout or "") + (r.stderr or ""), encoding="utf-8")
            rec.update(listing_facts(wd), action=f"pipeline exit {r.returncode}", seconds=round(time.time() - t0, 1))
            if rec.get("quota_exhausted"):
                stop = f"Gemini {rec['quota_exhausted']} quota exhausted during {slug}; rerun with --resume later"
        print(f"[portfolio] {slug}: {rec['status']}{' (photos pending)' if rec['photos_pending'] else ''} "
              f"| AirROI {rec['airroi_calls']} | Gemini {rec['gemini_requests']} req / {rec['gemini_tokens']} tok",
              flush=True)
        listings.append(rec)

    # A partial run (--only) updates its listings and keeps the rest of the day's portfolio.
    path = ROOT / "output" / args.date / "portfolio.json"
    earlier = [r for r in (_load(path, {}) or {}).get("listings", [])
               if isinstance(r, dict) and r.get("slug") not in {x["slug"] for x in listings}]
    every = sorted(earlier + listings, key=lambda r: r["slug"])
    totals = {k: sum(r.get(k) or 0 for r in every) for k in ("airroi_calls", "gemini_requests", "gemini_tokens")}
    report = {"date": args.date, "generated": datetime.now().isoformat(timespec="seconds"),
              "listings": every, "totals": totals, "stopped": stop}
    _write_json(path, report)
    queue = [r for r in listings if r["ready_to_write"]]
    print(f"\nWRITER QUEUE ({len(queue)} of {len(listings)}): one listing-writer agent per line")
    for r in queue:
        notes = ("in digest" if r["owner_notes_in_digest"] else
                 f"{r['owner_facts']} in config but NOT in this digest (built before them): pass them "
                 f"in the prompt" if r["owner_facts"] else "none")
        print(f"- {r['slug']} | season: {r['season'] or 'ASK or use the portfolio season'} | "
              f"owner notes: {notes} | output/{args.date}/{r['slug']}/digest.md")
    held = [r for r in listings if not r["ready_to_write"]]
    for r in held:
        why = ("photos pending (Gemini)" if r["photos_pending"] else r["action"] if "not started" in r["action"]
               else f"status {r['status']}")
        print(f"  held: {r['slug']}: {why}")
    print(f"\nTotals: AirROI {totals['airroi_calls']} calls | Gemini {totals['gemini_requests']} requests, "
          f"{totals['gemini_tokens']} tokens" + (f"\nSTOPPED: {stop}" if stop else ""))
    return report


def summary(args) -> Path:
    report = _load(ROOT / "output" / args.date / "portfolio.json", None)
    if not report:
        raise SystemExit(f"[portfolio] no output/{args.date}/portfolio.json; run without --summary first")
    slugs = [r["slug"] for r in report["listings"]]
    claude = claude_usage.writer_usage(slugs, args.date, ROOT)
    rows, written = [], 0
    for r in report["listings"]:
        rec = _load(ROOT / "output" / args.date / r["slug"] / "record.json", {})
        ok = bool(rec) and rec.get("run_date") == args.date
        written += ok
        c = claude.get(r["slug"]) or {}
        rows.append(f"| {r['slug']} | {r['status']} | {'yes' if ok else 'no'} | {rec.get('ale_total', '')} | "
                    f"{(rec.get('title') or '').replace('|', '/')} | {r.get('airroi_calls') or 0} | "
                    f"{r.get('gemini_requests') or 0} | {r.get('gemini_tokens') or 0} | "
                    f"{c.get('total_tokens', 'n/a')} | {c.get('turns', '')} |")
    claude_total = sum(c["total_tokens"] for c in claude.values())
    lines = [f"# Listing Optimizer portfolio: {args.date}", "",
             f"{written} of {len(rows)} listings written. Totals: AirROI {report['totals']['airroi_calls']} calls; "
             f"Gemini {report['totals']['gemini_requests']} requests, {report['totals']['gemini_tokens']} tokens; "
             f"Claude writers {claude_total} tokens ({len(claude)} listings metered from local transcripts).", "",
             "| Listing | Pipeline | Written | ALE | New title | AirROI calls | Gemini req | Gemini tokens | "
             "Claude tokens | Claude turns |",
             "|---|---|---|---|---|---|---|---|---|---|", *rows, "",
             "Reports are drafts. Nothing was changed on any live listing."]
    if report.get("stopped"):
        lines.insert(2, f"STOPPED EARLY: {report['stopped']}\n")
    out = console.desktop() / "Listing Optimizer" / "_portfolio" / args.date / "portfolio-summary.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n→ {out}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Listing Optimizer across every listed property.")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--only", default="", help="comma list of slugs")
    ap.add_argument("--resume", action="store_true", help="skip listings already gathered for this date")
    ap.add_argument("--refresh", action="store_true", help="re-pull Hospitable files and the property list")
    ap.add_argument("--no-cache", action="store_true", help="bypass paid caches (testing)")
    ap.add_argument("--include-unlisted", action="store_true")
    ap.add_argument("--summary", action="store_true", help="summarize after the writers finish")
    args = ap.parse_args(argv)
    if args.summary:
        summary(args)
    else:
        run(args)
    return 0


if __name__ == "__main__":
    console.utf8_stdio()
    raise SystemExit(main())
