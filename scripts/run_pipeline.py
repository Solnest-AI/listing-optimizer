#!/usr/bin/env python3
"""
run_pipeline.py — run every DETERMINISTIC step of the optimizer in ONE call.

Fetch, strip, score, summarise: subject, images, reviews, channels, calendar, occupancy,
prior runs, comps, photo scores, cadence, digest. None of it needs the model. It stops at
digest.md and hands off; it never writes copy, scores ALE, renders or writes to a PMS.

Hospitable is driven directly through the bundled read-only client. For any other PMS,
stage `subject.json` / `images.json` (and optional `reviews.json` / `calendar.json` /
`channels.json`) in the working dir first; any step whose file already exists is skipped.

Usage (Hospitable):
  .venv/bin/python scripts/run_pipeline.py --slug boho-bliss --date 2026-09-20 --property-id <UUID>
Usage (staged files from another PMS):
  .venv/bin/python scripts/run_pipeline.py --slug boho-bliss --date 2026-09-20
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import artifacts
import live_gallery

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
SCRIPTS = ROOT / "scripts"
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SKIPPABLE = {"reviews", "calendar", "comps", "photos", "memory", "channels"}
MANAGED = {"subject.json", "images.json", "live_gallery.json", "reviews.json", "channels.json", "calendar.json",
           "reservations.json", "occupancy.json", "prior_runs.json", "comps.json", "photo_scores.json", "cadence.json"}
CRITICAL = ("subject", "digest")
PHOTO_LIMIT_DEFAULT = 60  # keep equal to analyze_photos.DEFAULT_LIMIT (tested)


class Step:
    """One pipeline step and what happened to it, for the end-of-run summary."""

    def __init__(self, name):
        self.name, self.status, self.detail = name, "pending", ""

    def done(self, detail=""):
        self.status, self.detail = "ok", detail
        return self

    def skip(self, detail=""):
        self.status, self.detail = "skipped", detail
        return self

    def fail(self, detail=""):
        self.status, self.detail = "FAILED", detail
        return self


def run(cmd: list, label: str) -> tuple[bool, str]:
    """Run a child script. Returns (ok, last-meaningful-output-line)."""
    try:
        p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                           cwd=str(ROOT), timeout=600, check=False)
    except subprocess.TimeoutExpired:
        return False, f"{label}: timed out after 600s; rerun to resume from cache"
    except OSError as e:
        return False, f"{label}: could not start ({type(e).__name__})"
    out = (p.stdout or "").strip().splitlines()
    err = (p.stderr or "").strip().splitlines()
    if p.returncode != 0:
        return False, f"{label}: exit {p.returncode} — {(err or out or ['no output'])[-1][:200]}"
    return True, (out or err or [""])[-1][:200]


def hosp(sub: str, out: Path, pid: str, extra=()) -> list:
    return [PY, SCRIPTS / "hospitable_api.py", sub, "--property-id", pid, "--out", out, *extra]


def live_gallery_step(wd: Path, room_id, runner=None) -> tuple[str, str]:
    """Build images.json from the LIVE Airbnb gallery when a source can supply it.

    The PMS copy of a gallery can differ from what guests see (measured: PMS 54 photos with
    a collage cover, Airbnb 32 with a different cover). Order: a configured RankBreeze or
    IntelliHost key fetches fresh; otherwise a live_gallery.json the agent staged from its
    own connected MCP tools; otherwise the PMS gallery, and the report says so.
    Returns ("ok" | "skipped", detail). Only "ok" writes images.json.
    """
    runner = runner or run
    target = wd / "live_gallery.json"
    fallback = "photo plan uses the PMS gallery"
    if room_id and live_gallery.configured_sources():
        target.unlink(missing_ok=True)
        ok, msg = runner([PY, SCRIPTS / "live_gallery.py", "--room-id", room_id, "--out", target],
                         "live gallery")
        if not ok:
            return "skipped", f"{msg[:160]}; {fallback}"
        detail = msg
    elif target.exists():
        detail = "using the agent-staged live_gallery.json"
    else:
        why = "no RankBreeze or IntelliHost connection" if room_id else "no Airbnb listing id in subject.json"
        return "skipped", f"{why}; {fallback}"
    try:
        gallery = live_gallery.validate(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, ValueError) as e:
        return "skipped", f"live_gallery.json is invalid ({str(e)[:80]}); {fallback}"
    artifacts.write_json(wd / "images.json", live_gallery.to_images(gallery))
    return "ok", detail


def live_gallery_incomplete(wd: Path) -> bool:
    try:
        return json.loads((wd / "live_gallery.json").read_text(encoding="utf-8")).get("complete") is False
    except (OSError, ValueError, AttributeError):
        return False


def airroi_gallery_step(wd: Path) -> tuple[str, str]:
    """After comps: use AirROI's record of the listing (already in comps.json, no extra call)
    as the live gallery when nothing better supplied one. A top-grid-only list is not used as
    the gallery; the PMS copy keeps full coverage and AirROI still supplies copy/amenities."""
    try:
        comps = json.loads((wd / "comps.json").read_text(encoding="utf-8"))
        pms = json.loads((wd / "images.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "skipped", "no comps.json or images.json to compare"
    items = pms.get("data") if isinstance(pms, dict) else pms
    gallery = live_gallery.from_airroi_subject(comps.get("subject_listing"), len(items or []))
    if gallery is None or not gallery["photos"]:
        return "skipped", "AirROI has no record of this listing's photos; photo plan uses the PMS gallery"
    if not gallery["complete"]:
        return "skipped", (f"{gallery['incomplete_reason']} (top {gallery['returned']} only); photo plan "
                           f"uses the PMS gallery, AirROI still supplies the live copy and amenities")
    fetch = comps.get("fetch") or {}
    gallery["fetched_at"] = (f"AirROI data cached {fetch.get('cache_age_days')} days ago"
                             if fetch.get("path") == "cache" else "AirROI pull on the run date")
    try:
        live_gallery.validate(gallery)
    except ValueError as e:
        return "skipped", f"AirROI gallery invalid ({str(e)[:80]}); photo plan uses the PMS gallery"
    artifacts.write_json(wd / "live_gallery.json", gallery)
    artifacts.write_json(wd / "images.json", live_gallery.to_images(gallery))
    return "ok", f"AirROI: {gallery['returned']} live Airbnb photos from the comps pool (no extra call)"


def _tag_pms_images(path: Path, provider: str) -> None:
    """Label a PMS-sourced images.json so the digest and report can say which gallery the
    photo plan was built on."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(raw, dict) and not isinstance(raw.get("_source"), dict):
        raw["_source"] = {"kind": "pms", "provider": provider}
        artifacts.write_json(path, raw)


def subject_params(workdir: Path) -> dict:
    """Derive the comp query from subject.json so nobody retypes coordinates by hand.

    Fail-soft: a truncated or non-JSON subject.json returns a named `_error` instead of a
    traceback, so the run reports a failed subject step.
    """
    p = workdir / "subject.json"
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"_error": f"subject.json is not valid JSON ({str(e)[:90]}) — re-pull it "
                          f"with --refresh, or check what your PMS actually returned"}
    if not isinstance(raw, dict):
        return {"_error": f"subject.json holds a {type(raw).__name__}, expected an object "
                          f"with a 'data' key"}
    d = raw.get("data")
    if not isinstance(d, dict):
        return {"_error": "subject.json has no 'data' object — check the PMS response shape "
                          "against the data contract in the skill"}
    cap = d.get("capacity") if isinstance(d.get("capacity"), dict) else {}
    addr = d.get("address") if isinstance(d.get("address"), dict) else {}
    co = addr.get("coordinates") if isinstance(addr.get("coordinates"), dict) else {}
    listings = d.get("listings") if isinstance(d.get("listings"), list) else []

    def f(v):
        try:
            value = float(v)
            return value if math.isfinite(value) else None
        except (TypeError, ValueError):
            return None

    return {
        "lat": f(co.get("latitude")), "lng": f(co.get("longitude")),
        "address": addr.get("display") or ", ".join(
            str(addr[k]) for k in ("street", "city", "state") if addr.get(k)) or None,
        "market": addr.get("city"),
        "bedrooms": cap.get("bedrooms"), "baths": cap.get("bathrooms"), "guests": cap.get("max"),
        "name": d.get("public_name") or d.get("name"),
        "airbnb_id": next((str(x.get("platform_id")) for x in listings
                           if isinstance(x, dict) and x.get("platform") == "airbnb"
                           and x.get("platform_id")), None),
    }


def main():
    ap = argparse.ArgumentParser(description="Run the deterministic half of the optimizer in one call.")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--property-id", dest="pid", default=None,
                    help="Hospitable property UUID. Omit on other PMSs (stage the JSON files yourself).")
    ap.add_argument("--workdir", default=None, help="default output/<date>/<slug>")
    ap.add_argument("--refresh", action="store_true", help="re-pull Hospitable files that already exist")
    ap.add_argument("--photo-limit", type=int, default=PHOTO_LIMIT_DEFAULT)
    ap.add_argument("--calendar-days", type=int, default=90)
    ap.add_argument("--all-reviews", action="store_true",
                    help="pull the FULL review history (default: the 20 newest). Only needed for "
                         "lifetime category averages; the 20-review window tracks lifetime "
                         "within 0.11 on every category on a real 101-review listing")
    ap.add_argument("--review-limit", type=int, default=20, help="how many of the newest reviews to pull")
    ap.add_argument("--no-cache", action="store_true",
                    help="bypass the comp + photo-score caches (forces paid calls)")
    ap.add_argument("--rankbreeze", default=None,
                    help="occupancy cross-check, e.g. 'Jun:41,Jul:55' (RankBreeze is MCP-only)")
    ap.add_argument("--skip", default="", help="comma list: " + ",".join(sorted(SKIPPABLE)))
    args = ap.parse_args()

    if not SLUG_RE.fullmatch(args.slug):
        ap.error("--slug must contain lowercase letters, numbers and single hyphens")
    try:
        start_date = date.fromisoformat(args.date)
    except ValueError:
        ap.error("--date must be a valid YYYY-MM-DD date")
    if not 1 <= args.calendar_days <= 365 or not 1 <= args.photo_limit <= 100:
        ap.error("--calendar-days must be 1..365 and --photo-limit 1..100")
    if not 1 <= args.review_limit <= 50:
        ap.error("--review-limit must be 1..50 (use --all-reviews for more)")
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    if skip - SKIPPABLE:
        ap.error("unknown --skip step: " + ", ".join(sorted(skip - SKIPPABLE)))

    wd = Path(args.workdir) if args.workdir else ROOT / "output" / args.date / args.slug
    wd.mkdir(parents=True, exist_ok=True)
    steps: list[Step] = []
    usable: set[str] = set()
    try:
        previous_status = artifacts.run_status(wd)
    except (OSError, ValueError) as e:
        ap.error(str(e))
    prior_stamp = (wd / "pipeline_status.json").stat().st_mtime_ns if previous_status else 0
    if previous_status and (previous_status.get("listing_slug") != args.slug
                            or previous_status.get("run_date") != args.date
                            or (args.pid and previous_status.get("property_id") not in (None, args.pid))):
        ap.error("workdir belongs to another listing/date; use its original arguments or a new workdir")
    pid = args.pid or previous_status.get("property_id")

    def publish(status):
        artifacts.write_json(wd / "pipeline_status.json", {
            "listing_slug": args.slug, "run_date": args.date, "status": status,
            "property_id": pid, "excluded_files": sorted(MANAGED - usable),
            "steps": [vars(s) for s in steps],
        })

    def have(name):
        """A usable file already on disk. --refresh only re-pulls what we can fetch, so
        staged inputs on a non-Hospitable run stay valid. A file excluded by an earlier
        failed run is invalid until it is fetched again or restaged."""
        if args.refresh and args.pid:
            return False
        try:
            if (name in previous_status.get("excluded_files", [])
                    and (wd / name).stat().st_mtime_ns <= prior_stamp):
                return False
            value = json.loads((wd / name).read_text(encoding="utf-8"))
            return isinstance(value, (dict, list))
        except (OSError, ValueError):
            return False

    def gather(name, sub, fname, extra=(), optional=True):
        st = Step(name)
        steps.append(st)
        if name in skip:
            return st.skip("--skip")
        if have(fname):
            usable.add(fname)
            return st.skip(f"using existing {fname}")
        if not args.pid:
            return st.skip(f"{fname} not staged") if optional else st.fail(f"{fname} missing or not valid JSON")
        ok, msg = run(hosp(sub, wd / fname, args.pid, extra), name)
        if ok:
            usable.add(fname)
            return st.done(msg)
        return st.fail(msg)

    publish("running")

    # Fail before any paid work when the subject is missing or malformed.
    subject_step = gather("subject", "property", "subject.json", optional=False)
    params = subject_params(wd) if "subject.json" in usable else {}
    if subject_step.status == "FAILED" or params.get("_error") or not params.get("name"):
        if subject_step.status != "FAILED":
            subject_step.fail(params.get("_error") or "subject.json has no listing name")
        usable.discard("subject.json")
        publish("failed")
        print(f"[run_pipeline] 1 step(s) failed: subject: {subject_step.detail}")
        sys.exit(1)

    # ── Images: the live Airbnb gallery when a source can supply it, else the PMS copy ──
    if "photos" in skip:
        steps.append(Step("live gallery").skip("--skip photos"))
        steps.append(Step("images").skip("--skip photos"))
    else:
        live = Step("live gallery")
        steps.append(live)
        status, detail = live_gallery_step(wd, params.get("airbnb_id"))
        if status == "ok":
            live.done(detail)
            usable.update({"images.json", "live_gallery.json"})
            steps.append(Step("images").skip("live Airbnb gallery used instead of the PMS copy"))
        else:
            live.skip(detail)
            if gather("images", "images", "images.json").status != "FAILED":
                _tag_pms_images(wd / "images.json", "Hospitable" if args.pid else "staged PMS files")
    gather("reviews", "reviews", "reviews.json",
           ("--all-reviews",) if args.all_reviews else ("--review-limit", str(args.review_limit)))

    # ── Channels: which connected platforms never deliver review text (iCal, VRBO) ──
    st = Step("channels")
    steps.append(st)
    if "channels" in skip or "reviews" in skip:
        st.skip("review coverage not requested")
    elif have("channels.json"):
        usable.add("channels.json")
        st.skip("using existing channels.json")
    elif not args.pid:
        st.skip("no --property-id (Hospitable-only; report review coverage cautiously)")
    else:
        ok, msg = run([PY, SCRIPTS / "hospitable_api.py", "channels", "--out", wd / "channels.json"], "channels")
        if ok:
            usable.add("channels.json")
            try:
                ch = json.loads((wd / "channels.json").read_text(encoding="utf-8"))
                st.done(f"connected={ch.get('connected_platforms')} review-silent={ch.get('silent_channels')}")
            except (OSError, ValueError, AttributeError):
                st.done(msg)
        else:
            st.skip(f"could not read channels ({msg[:80]}) — not fatal")

    # ── Calendar + reservations → occupancy (read-only; price stripped at source) ──
    st = Step("calendar+occupancy")
    steps.append(st)
    if "calendar" in skip:
        st.skip("--skip")
    elif not args.pid and not have("calendar.json"):
        st.skip("no --property-id and no staged calendar.json")
    else:
        ok = True
        calendar_from_hospitable = False
        end = str(start_date + timedelta(days=args.calendar_days - 1))
        if not have("calendar.json"):
            ok, msg = run(hosp("calendar", wd / "calendar.json", args.pid,
                               ("--start", args.date, "--end", end)), "calendar")
            calendar_from_hospitable = ok
            if not ok:
                st.fail(msg)
        if ok:
            usable.add("calendar.json")
            # Stay count for the calendar's own window. Booked nights alone cannot tell two
            # long stays from seven short ones. Only pulled when WE pulled the calendar from
            # Hospitable: a staged calendar means another PMS whose reservations live elsewhere.
            n = None
            ok_r = have("reservations.json")
            if not ok_r and calendar_from_hospitable:
                ok_r, _ = run(hosp("reservations", wd / "reservations.json", args.pid,
                                   ("--start", args.date, "--end", end)), "reservations")
            if ok_r:
                try:
                    rj = json.loads((wd / "reservations.json").read_text(encoding="utf-8"))
                    pull = rj.get("_pull") or {}
                    # Only trust the count when the window was actually scoped.
                    if pull.get("scoped") or (pull.get("window_start") and pull.get("window_end")):
                        n = int(pull.get("count", len(rj.get("data") or [])))
                        usable.add("reservations.json")
                except (OSError, ValueError, TypeError, AttributeError):
                    n = None
            cmd = [PY, SCRIPTS / "occupancy.py", "--calendar", wd / "calendar.json",
                   "--source", "Hospitable" if pid else "Staged PMS calendar",
                   "--out", wd / "occupancy.json"]
            if n is not None:
                cmd += ["--reservations-count", str(n)]
            if args.rankbreeze:
                cmd += ["--rankbreeze", args.rankbreeze]
            ok, msg = run(cmd, "occupancy")
            if ok:
                usable.add("occupancy.json")
                st.done(msg)
            else:
                st.fail(msg)

    # ── Prior runs (the trend baseline) ──
    st = Step("memory/prior")
    steps.append(st)
    if "memory" in skip:
        st.skip("--skip")
    else:
        try:
            p = subprocess.run([PY, str(SCRIPTS / "memory.py"), "prior", "--listing", args.slug],
                               capture_output=True, text=True, cwd=str(ROOT), timeout=30, check=True)
            rows = json.loads((p.stdout or "").strip() or "[]")
            if not isinstance(rows, list):
                raise ValueError("history is not a list")
            rows = [r for r in rows if isinstance(r, dict) and str(r.get("run_date", "")) < args.date]
            artifacts.write_json(wd / "prior_runs.json", rows)
            usable.add("prior_runs.json")
            st.done(f"{len(rows)} prior run(s)" + (" — this run is the baseline" if not rows else ""))
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            st.skip(f"no usable history ({str(e)[:80]}) — treat this run as the baseline")

    # ── Comps: the only paid AirROI step (1 call, cached 14d) ──
    st = Step("comps (AirROI)")
    steps.append(st)
    if "comps" in skip:
        st.skip("--skip")
    elif params.get("bedrooms") is None or not params.get("guests"):
        st.fail("cannot derive bedrooms/guests from subject.json — pull the subject first, "
                "or run scripts/pull_comps.py by hand")
    else:
        cmd = [PY, SCRIPTS / "pull_comps.py",
               "--bedrooms", str(params["bedrooms"]), "--baths", str(params.get("baths") or 1),
               "--guests", str(params["guests"]), "--out", wd / "comps.json"]
        if params.get("lat") is not None and params.get("lng") is not None:
            cmd += ["--lat", str(params["lat"]), "--lng", str(params["lng"])]
        for flag, key in (("--address", "address"), ("--market", "market"),
                          ("--exclude-listing-id", "airbnb_id")):
            if params.get(key):
                cmd += [flag, params[key]]
        if args.no_cache:
            cmd += ["--no-cache"]
        ok, msg = run(cmd, "comps")
        if ok:
            usable.add("comps.json")
            st.done(msg)
        else:
            st.fail(msg)

    # ── Live gallery from AirROI when neither RankBreeze nor IntelliHost supplied one ──
    live_steps = [x for x in steps if x.name == "live gallery"]
    if (live_steps and (live_steps[0].status != "ok" or live_gallery_incomplete(wd))
            and "comps.json" in usable and "photos" not in skip):
        status, detail = airroi_gallery_step(wd)
        if status == "ok":
            live_steps[0].done(detail)
            usable.update({"images.json", "live_gallery.json"})
            for x in steps:
                if x.name == "images" and x.status == "ok":
                    x.done("PMS images replaced by the live AirROI gallery")
        elif live_steps[0].status != "ok":
            live_steps[0].skip(f"{live_steps[0].detail}; {detail}")

    # ── Photo scoring (Gemini; cached per photo URL) ──
    st = Step("photos (Gemini)")
    steps.append(st)
    if "photos" in skip:
        (wd / "photo_fallback.json").unlink(missing_ok=True)  # never show a stale fallback
        st.skip("--skip")
    elif "images.json" not in usable:
        st.fail("images.json missing — stage it from your PMS first")
    else:
        cmd = [PY, SCRIPTS / "analyze_photos.py", "--photos", wd / "images.json",
               "--limit", str(args.photo_limit), "--out", wd / "photo_scores.json"]
        if args.no_cache:
            cmd += ["--no-cache"]
        ok, msg = run(cmd, "photos")
        if not ok:
            st.fail(msg)
        else:
            try:
                scores = json.loads((wd / "photo_scores.json").read_text(encoding="utf-8"))
                by = scores.get("scored_by") or {}
                detail = (f"{scores.get('scored_count')}/{scores.get('gallery_count', scores.get('submitted_count'))} "
                          f"gallery photos ranked; {(scores.get('usage') or {}).get('api_calls', '?')} "
                          f"Gemini requests; {scores.get('from_cache_count', 0)} cached"
                          + (f"; {by['claude_vision']} Claude-vision" if by.get("claude_vision") else ""))
                usable.add("photo_scores.json")
                if scores.get("failed"):
                    st.fail(detail + "; partial scoring failure")
                else:
                    st.done(detail)
            except (OSError, ValueError, AttributeError):
                st.fail("photo scoring returned invalid output")

    st = Step("cadence")
    steps.append(st)
    ok, msg = run([PY, SCRIPTS / "cadence.py", "--listing", args.slug, "--check",
                   "--today", args.date, "--out", wd / "cadence.json"], "cadence")
    if ok:
        usable.add("cadence.json")
        st.done(msg)
    else:
        st.fail(msg)

    # ── Digest: the one file the model reads ──
    st = Step("digest")
    steps.append(st)
    publish("degraded" if any(s.status == "FAILED" for s in steps) else "ready")
    ok, msg = run([PY, SCRIPTS / "build_digest.py", wd], "digest")
    st.done(msg) if ok else st.fail(msg)

    # ── Summary (this is ALL the model needs to see) ──
    print(f"\n{'=' * 72}\n[run_pipeline] {args.slug} · {args.date} · {wd}")
    print(f"  subject: {params['name']} · {params.get('bedrooms')}BR/"
          f"{params.get('baths')}BA/{params.get('guests')}g · {params.get('market')}")
    for s in steps:
        mark = {"ok": "✅", "skipped": "⏭️ ", "FAILED": "❌"}.get(s.status, "  ")
        print(f"  {mark} {s.name:22} {s.detail}")

    failed = [s.name for s in steps if s.status == "FAILED"]
    # Exit non-zero only when there is nothing to optimize from. A failed optional step is
    # a degraded report the model must describe honestly.
    critical = [n for n in failed if n in CRITICAL]
    publish("failed" if critical else "degraded" if failed else "ready")
    print("=" * 72)
    if failed:
        print(f"  ⚠️  {len(failed)} step(s) failed: {', '.join(failed)} — the report must "
              f"say which sections are missing, not silently omit them.")
    if not critical:
        print(f"  NEXT: Read {wd / 'digest.md'} and NOTHING else. Then apply the ALE + SB7 "
              f"rubrics and write result.json.")
    sys.exit(1 if critical else 0)


if __name__ == "__main__":
    main()
