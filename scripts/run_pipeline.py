#!/usr/bin/env python3
"""
run_pipeline.py — run every DETERMINISTIC step of the optimizer in ONE call.

Why this exists: the pipeline's data-gathering half (subject, images, reviews, calendar,
reservations, occupancy, prior runs, comps, photo scores, digest) used to be ~13 separate
tool calls. Every tool call is a turn, and every turn re-sends the whole conversation, so
those 13 turns cost 13x the accumulated context while making exactly zero decisions. None
of it needs the model: it is fetch, strip, score, summarise. This collapses it to one call
whose only output is a short summary plus digest.md.

WHAT IT DOES NOT DO: any judgment. It never writes copy, never scores ALE, never renders,
never writes back to a PMS. It stops at digest.md and hands off.

WORKS ON ANY PMS. It only drives Hospitable itself (via the bundled read-only REST
client). For any other PMS, the agent drops `subject.json` / `images.json` / `reviews.json`
/ `calendar.json` into the working dir from that PMS's MCP tools first; this script SKIPS
any step whose file already exists, then does everything downstream. --refresh re-pulls.

ZERO-PRICING: it shells out to the same scripts as before, so the calendar price strip and
the comps price whitelist are unchanged. It adds no new data path of its own.

Usage (Hospitable, full run):
  .venv/bin/python scripts/run_pipeline.py --slug boho-bliss --date 2026-09-20 \
      --property-id <UUID>

Usage (another PMS — files already staged by MCP):
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

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
SCRIPTS = ROOT / "scripts"


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


def run(cmd: list[str], label: str) -> tuple[bool, str]:
    """Run a child script. Returns (ok, last-meaningful-output-line)."""
    try:
        p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, cwd=str(ROOT), timeout=600)
    except subprocess.TimeoutExpired:
        return False, f"{label}: timed out after 600s; rerun to resume from cache"
    except Exception as e:
        return False, f"{label}: could not start ({type(e).__name__})"
    out = (p.stdout or "").strip().splitlines()
    err = (p.stderr or "").strip().splitlines()
    if p.returncode != 0:
        return False, f"{label}: exit {p.returncode} — {(err or out or ['no output'])[-1][:200]}"
    return True, (out or err or [""])[-1][:200]


def hosp(sub: str, out: Path, pid: str, extra=()) -> list[str]:
    cmd = [PY, SCRIPTS / "hospitable_api.py", sub, "--property-id", pid, "--out", out]
    return cmd + list(extra)


def subject_params(workdir: Path) -> dict:
    """Derive the comp query from subject.json instead of making the agent read the file
    and retype lat/lng/bedrooms/baths/guests into a command (a read, a retype, and a
    chance to fat-finger a coordinate — I did exactly that once while measuring this).

    Fail-soft: a truncated or non-JSON subject.json (a PMS returning an HTML error page, a
    write interrupted mid-flight) must produce a named step failure, never a traceback that
    takes the whole run down before anything downstream has run.
    """
    p = workdir / "subject.json"
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"_error": f"subject.json is not valid JSON ({str(e)[:90]}) — re-pull it "
                          f"with --refresh, or check what your PMS actually returned"}
    if not isinstance(raw, dict):
        return {"_error": f"subject.json holds a {type(raw).__name__}, expected an object "
                          f"with a 'data' key"}
    d = raw.get("data")
    if not isinstance(d, dict):
        return {"_error": "subject.json has no 'data' object — check the PMS response shape "
                          "against the data contract in CLAUDE.md"}
    cap = d.get("capacity") if isinstance(d.get("capacity"), dict) else {}
    addr = d.get("address") if isinstance(d.get("address"), dict) else {}
    co = addr.get("coordinates") if isinstance(addr.get("coordinates"), dict) else {}

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
        "airbnb_id": next((str(x.get("platform_id")) for x in d.get("listings", [])
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
    ap.add_argument("--refresh", action="store_true", help="re-pull PMS files that already exist")
    ap.add_argument("--photo-limit", type=int, default=30)
    ap.add_argument("--calendar-days", type=int, default=90)
    ap.add_argument("--all-reviews", action="store_true",
                    help="pull the FULL review history (default: the 20 newest). Only needed for "
                         "LIFETIME category averages / unanswered count; measured on a real "
                         "101-review listing, the 20-review window tracks lifetime within 0.11 "
                         "on every category, so the copy never needs it")
    ap.add_argument("--review-limit", type=int, default=20,
                    help="how many of the newest reviews to pull (default 20 = what the digest reads)")
    ap.add_argument("--no-cache", action="store_true",
                    help="bypass the comp + photo-score caches (forces paid calls)")
    ap.add_argument("--rankbreeze", default=None,
                    help="occupancy cross-check, e.g. 'Jun:41,Jul:55' (RankBreeze is MCP-only)")
    ap.add_argument("--skip", default="", help="comma list: reviews,calendar,comps,photos,memory")
    args = ap.parse_args()

    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.slug):
        ap.error("--slug must contain lowercase letters, numbers and single hyphens")
    try:
        start_date = date.fromisoformat(args.date)
    except ValueError:
        ap.error("--date must be a valid YYYY-MM-DD date")
    if not 1 <= args.calendar_days <= 365 or not 1 <= args.photo_limit <= 100:
        ap.error("--calendar-days must be 1..365 and --photo-limit 1..100")
    if not 1 <= args.review_limit <= 50:
        ap.error("--review-limit must be 1..50 (use --all-reviews for more)")

    wd = Path(args.workdir) if args.workdir else ROOT / "output" / args.date / args.slug
    wd.mkdir(parents=True, exist_ok=True)
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    if skip - {"reviews", "calendar", "comps", "photos", "memory", "channels"}:
        ap.error("unknown --skip step: " + ", ".join(sorted(skip)))
    steps: list[Step] = []
    managed = {"subject.json", "images.json", "reviews.json", "channels.json", "calendar.json",
               "reservations.json", "occupancy.json", "prior_runs.json", "comps.json",
               "photo_scores.json", "cadence.json"}
    usable = set()
    try:
        previous_status = artifacts.run_status(wd)
    except (OSError, ValueError) as e:
        ap.error(str(e))
    prior_stamp = (wd / "pipeline_status.json").stat().st_mtime_ns if previous_status else 0
    if previous_status and (previous_status.get("listing_slug") != args.slug
                            or previous_status.get("run_date") != args.date
                            or (args.pid and previous_status.get("property_id") not in (None, args.pid))):
        ap.error("workdir belongs to another listing/date; use its original arguments or a new workdir")

    def publish(status):
        artifacts.write_json(wd / "pipeline_status.json", {
            "listing_slug": args.slug, "run_date": args.date, "status": status,
            "property_id": args.pid or previous_status.get("property_id"),
            "excluded_files": sorted(managed - usable),
            "steps": [vars(s) for s in steps],
        })

    def have(name):
        # --refresh only re-pulls a source we can actually fetch. Staged PMS inputs
        # remain valid on a non-Hospitable run.
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
            return st.skip(f"using staged {fname}")
        if not args.pid:
            return st.skip(f"{fname} not staged") if optional else st.fail(f"{fname} missing or not valid JSON")
        ok, msg = run(hosp(sub, wd / fname, args.pid, extra), name)
        if ok:
            usable.add(fname)
            st.done(msg)
        else:
            st.fail(msg)
        return st

    publish("running")

    # Fail before any paid work when the subject is missing, malformed, or stale
    # after a failed refresh. A file from an older attempt is never a success.
    subject_step = gather("subject", "property", "subject.json", optional=False)
    params = subject_params(wd) if "subject.json" in usable else {}
    if subject_step.status == "FAILED" or params.get("_error") or not params.get("name"):
        if subject_step.status != "FAILED":
            subject_step.fail(params.get("_error") or "subject.json has no listing name")
        usable.discard("subject.json")
        publish("failed")
        print(f"[run_pipeline] 1 step(s) failed: subject: {subject_step.detail}")
        sys.exit(1)

    # ── 1. Subject / images / reviews (Hospitable only; other PMSs stage these) ──
    pulls = [("images", "images", "images.json", ()),
             ("reviews", "reviews", "reviews.json",
              ("--all-reviews",) if args.all_reviews
              else ("--review-limit", str(args.review_limit)))]
    for name, sub, fname, extra in pulls:
        if name == "images" and "photos" in skip:
            steps.append(Step(name).skip("--skip photos"))
        else:
            gather(name, sub, fname, extra)

    # ── 1.4 Connected channels → which ones are SILENT on reviews ──
    # Cheap (one unpaginated request) and it closes a real blind spot: VRBO (`homeaway`) and
    # any iCal feed take bookings but deliver no review text, so the report must not imply
    # the guest language it quotes covers every channel.
    st = Step("channels")
    steps.append(st)
    if "channels" in skip or "reviews" in skip:
        st.skip("review coverage not requested")
    elif have("channels.json"):
        st.skip("channels.json already present")
        usable.add("channels.json")
    elif not args.pid:
        st.skip("no --property-id (Hospitable-only; report review coverage cautiously)")
    else:
        ok, msg = run([PY, SCRIPTS / "hospitable_api.py", "channels",
                       "--out", wd / "channels.json"], "channels")
        if ok:
            usable.add("channels.json")
            try:
                ch = json.loads((wd / "channels.json").read_text())
                st.done(f"connected={ch.get('connected_platforms')} "
                        f"review-silent={ch.get('silent_channels')}")
            except Exception:
                st.done(msg)
        else:
            st.skip(f"could not read channels ({msg[:80]}) — not fatal")

    # ── 1.6 Calendar + reservations → occupancy (read-only; price stripped at source) ──
    st = Step("calendar+occupancy")
    steps.append(st)
    if "calendar" in skip:
        st.skip("--skip")
    elif not args.pid and not have("calendar.json"):
        st.skip("no --property-id and no staged calendar.json")
    else:
        ok = True
        calendar_from_hospitable = False
        if not have("calendar.json"):
            start = args.date
            end = str(start_date + timedelta(days=args.calendar_days - 1))
            ok, msg = run(hosp("calendar", wd / "calendar.json", args.pid,
                               ("--start", start, "--end", end)), "calendar")
            calendar_from_hospitable = ok
            if not ok:
                st.fail(msg)
        if ok:
            usable.add("calendar.json")
            # Reservations are optional context, and the count only means something if the
            # WINDOW is explicit. Measured 2026-09-20 on a live property: an unfiltered
            # /reservations call returned 1 while the same property had 2 stays inside the
            # next 90 days, so the unscoped count UNDERCOUNTS and calling it "upcoming" is a
            # guess. A past window returned 0, proving the filter is honoured. So: ask for
            # exactly the calendar's window and report "reservations in the next N days".
            # If the pull fails we pass nothing and occupancy.py reports n/a — an honest
            # unknown beats a confident zero.
            n = None
            ok_r = have("reservations.json")
            # ONLY pull reservations when WE pulled the calendar from Hospitable. If the
            # calendar was staged, this is another PMS and its reservations do not live in
            # Hospitable — reaching for them would be both wrong and a surprise API call on
            # a run the user staged themselves. (Locked by
            # tests/test_mvp_pipeline.py::test_staged_calendar_never_calls_hospitable.)
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
                except Exception:
                    n = None
            cmd = [PY, SCRIPTS / "occupancy.py", "--calendar", wd / "calendar.json",
                   "--source", "Hospitable" if args.pid or previous_status.get("property_id") else "Staged PMS calendar",
                   "--out", wd / "occupancy.json"]
            if n is not None:
                cmd += ["--reservations-count", str(n)]
            if args.rankbreeze:
                cmd += ["--rankbreeze", args.rankbreeze]
            ok, msg = run(cmd, "occupancy")
            st.done(msg) if ok else st.fail(msg)
            if ok:
                usable.add("occupancy.json")

    # ── 1.7 Prior runs (the trend baseline) ──
    st = Step("memory/prior")
    steps.append(st)
    if "memory" in skip:
        st.skip("--skip")
    else:
        try:
            p = subprocess.run([PY, str(SCRIPTS / "memory.py"), "prior", "--listing", args.slug],
                               capture_output=True, text=True, cwd=str(ROOT), timeout=30, check=True)
            body = (p.stdout or "").strip()
            rows = json.loads(body or "[]")
            if not isinstance(rows, list):
                raise ValueError("history is not a list")
            rows = [r for r in rows if isinstance(r, dict) and str(r.get("run_date", "")) < args.date]
            artifacts.write_json(wd / "prior_runs.json", rows)
            usable.add("prior_runs.json")
            n = len(rows)
            st.done(f"{n} prior run(s)" + (" — this run is the baseline" if not n else ""))
        except Exception as e:
            st.skip(f"no usable history ({str(e)[:80]}) — treat this run as the baseline")

    # ── 2. Comps — the only paid AirROI step (1 call, cached 14d) ──
    st = Step("comps (AirROI)")
    steps.append(st)
    if "comps" in skip:
        st.skip("--skip")
    elif params.get("bedrooms") is None or not params.get("guests"):
        st.fail("cannot derive bedrooms/guests from subject.json — pull the subject first, "
                "or run scripts/pull_comps.py by hand")
    else:
        cmd = [PY, SCRIPTS / "pull_comps.py",
               "--bedrooms", str(params["bedrooms"]),
               "--baths", str(params.get("baths") or 1),
               "--guests", str(params["guests"]),
               "--out", wd / "comps.json"]
        if params.get("lat") is not None and params.get("lng") is not None:
            cmd += ["--lat", str(params["lat"]), "--lng", str(params["lng"])]
        if params.get("address"):
            cmd += ["--address", params["address"]]
        if params.get("market"):
            cmd += ["--market", params["market"]]
        if params.get("airbnb_id"):
            cmd += ["--exclude-listing-id", params["airbnb_id"]]
        if args.no_cache:
            cmd += ["--no-cache"]
        ok, msg = run(cmd, "comps")
        st.done(msg) if ok else st.fail(msg)
        if ok:
            usable.add("comps.json")

    # ── 3. Photo scoring (Gemini; cached per photo URL) ──
    st = Step("photos (Gemini)")
    steps.append(st)
    if "photos" in skip:
        st.skip("--skip")
    elif "images.json" not in usable:
        st.fail("images.json missing — stage it from your PMS first")
    else:
        cmd = [PY, SCRIPTS / "analyze_photos.py", "--photos", wd / "images.json",
               "--limit", str(args.photo_limit), "--out", wd / "photo_scores.json"]
        if args.no_cache:
            cmd += ["--no-cache"]
        ok, msg = run(cmd, "photos")
        st.done(msg) if ok else st.fail(msg)
        if ok:
            usable.add("photo_scores.json")
            try:
                scores = json.loads((wd / "photo_scores.json").read_text(encoding="utf-8"))
                st.detail = (f"{scores.get('scored_count')}/{scores.get('gallery_count', scores.get('submitted_count'))} gallery photos ranked; "
                             f"{(scores.get('usage') or {}).get('api_calls', '?')} Gemini requests; "
                             f"{scores.get('from_cache_count', 0)} cached")
                if scores.get("failed"):
                    st.fail(st.detail + "; partial scoring failure")
            except (OSError, ValueError, AttributeError):
                usable.discard("photo_scores.json")
                st.fail("photo scoring returned invalid output")

    st = Step("cadence")
    steps.append(st)
    ok, msg = run([PY, SCRIPTS / "cadence.py", "--listing", args.slug, "--check",
                   "--today", args.date, "--out", wd / "cadence.json"], "cadence")
    st.done(msg) if ok else st.fail(msg)
    if ok:
        usable.add("cadence.json")

    # ── 4. Digest — the one file the model reads ──
    st = Step("digest")
    steps.append(st)
    publish("degraded" if any(s.status == "FAILED" for s in steps) else "ready")
    ok, msg = run([PY, SCRIPTS / "build_digest.py", wd], "digest")
    st.done(msg) if ok else st.fail(msg)

    # ── Summary (this is ALL the model needs to see) ──
    print(f"\n{'='*72}\n[run_pipeline] {args.slug} · {args.date} · {wd}")
    if params.get("name"):
        print(f"  subject: {params['name']} · {params.get('bedrooms')}BR/"
              f"{params.get('baths')}BA/{params.get('guests')}g · {params.get('market')}")
    for s in steps:
        mark = {"ok": "✅", "skipped": "⏭️ ", "FAILED": "❌"}.get(s.status, "  ")
        print(f"  {mark} {s.name:22} {s.detail}")

    failed = [s.name for s in steps if s.status == "FAILED"]
    # Exit non-zero only when the run cannot continue at all. A missing funnel or a failed
    # photo is a degraded report the model should describe honestly; an unreadable subject
    # or a broken digest means there is nothing to optimize from.
    critical = [n for n in failed if n in ("subject", "subject parse", "digest")]
    publish("failed" if critical else "degraded" if failed else "ready")
    print(f"{'='*72}")
    if failed:
        print(f"  ⚠️  {len(failed)} step(s) failed: {', '.join(failed)} — the report must "
              f"say which sections are missing, not silently omit them.")
    if not critical:
        print(f"  NEXT: Read {wd / 'digest.md'} and NOTHING else. Then apply the ALE + SB7 "
              f"rubrics and write result.json.")
    sys.exit(1 if critical else 0)


if __name__ == "__main__":
    main()
