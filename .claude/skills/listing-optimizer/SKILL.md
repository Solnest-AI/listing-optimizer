---
name: listing-optimizer
description: Optimize a short-term-rental listing against the ALE framework. Use when the user says "optimize [listing]", "run the listing optimizer", "refresh my [listing]", or wants paste-ready title/summary/"The Space"/photo-caption + comp-gap + photo-plan output for an Airbnb listing. ALWAYS starts by asking which season they're getting ready for (unless stated) and gears all copy + the photo plan to it. Discovers the user's own listings from THEIR connected PMS (any PMS — Hospitable supported out of the box; Hostaway/Guesty/OwnerRez/Lodgify/Smoobu/etc. via their MCP or REST API per the data contract in CLAUDE.md), pulls the subject free from the PMS, comps from AirROI, scores photos with Gemini, and writes HTML+Markdown+paste-block to the Desktop. Never touches pricing. Paste-ready by default; on PMSs that support content writes it can apply the approved copy (opt-in, content-only — Hospitable is always paste-only).
---

# Listing Optimizer

Optimizes one short-term-rental listing against the **ALE framework** (the engine). Photo eval + comp eval are **inputs that feed ALE**. StoryBrand SB7 shapes the copy. Output is **paste-ready** by default — the user enters it into their PMS, which syncs to the channels. On PMSs with content-write APIs, Step 8 can apply it for them after explicit approval.

## ⛔ Hard rules (non-negotiable)
1. **No pricing. Ever.** Never read, recommend, surface, or write price / ADR / RevPAR / revenue / min-stay. `render_report.py` scans every deliverable and refuses to write if any appear.
2. **Write-back is OPT-IN, content-only, and explicitly confirmed.** Default = read-only + paste-ready output. If the user's PMS supports listing-content writes (Hostaway, Guesty, OwnerRez, Lodgify, Smoobu, … — **Hospitable does NOT**: its listing endpoints are read-only, so Hospitable is always paste-only), you MAY apply the optimized content for them — only under ALL of these conditions:
   - The user **explicitly approves in this session** ("apply it") *after* seeing the exact content. Never auto-push; setup consent or a previous run's approval does not carry over.
   - **Before-snapshot first:** save the current live content to the working dir as `before-writeback.json` and tell the user it exists (their undo).
   - **Content fields only:** title, summary/description, "The Space", photo captions/order. Build the update payload from scratch with ONLY those keys — **never** send rates, calendar, availability, min-stay, fees, or policies, and never PUT back a full listing object (it can carry pricing).
   - **Verify after writing:** re-read the listing, confirm the change landed, and report exactly which fields were written.
   - **Calendar and pricing endpoints remain forbidden on every PMS, always** — reading availability for occupancy is fine; writing anything to a calendar is not.
3. **ALE is the engine.** Photos + comps feed it; SB7 lives inside the copy.
4. **Subject = Hospitable (free).** AirROI is used **only** for competitor comps — never for our own listing.

## Reference rubrics (read before optimizing)
- `references/ale-rubric.md` — the engine + the scorecard you produce
- `references/storybrand-sb7-rubric.md` — emotional layer for the copy
- `references/photo-rubric.md` — what photos are scored against

## Environment (fully self-contained — nothing outside this folder but keys)
- Python: `.venv/bin/python` (project venv; on Windows use `.venv\Scripts\python` — substitute throughout). Scripts live in `scripts/`. If no `.venv` exists yet, create it first: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
- **Keys: project `.env`** (gitignored; see `.env.example`). `AIRROI_API_KEY` (comps) + `GEMINI_API_KEY` (photos). Both `airroi_client.py` and `analyze_photos.py` auto-load it.
- **AirROI: vendored** in `scripts/airroi_client.py` — direct API calls, no external repo.
- **Listings: discovered live from the user's connected PMS** — ANY PMS. Nothing hardcoded. This file names **Hospitable tools as the reference implementation**; for any other PMS (Hostaway, Guesty, OwnerRez, Lodgify, Smoobu, Uplisting, Hostfully, …) substitute its MCP read-tools or its REST API (read-only, `PMS_TOKEN` in `.env`), mapped onto the **PMS data contract in CLAUDE.md** (listings → subject content → photos → reviews → calendar availability → reservations). Optional per-user overrides in `config/properties.json` (gitignored; see `config/properties.example.json`).
- **Hospitable without an MCP: bundled REST fallback** — `scripts/hospitable_api.py` (`properties`/`property`/`images`/`reviews`/`calendar`/`reservations`, needs `HOSPITABLE_TOKEN` in `.env`). Same read-only data as the MCP tools; its `calendar` subcommand strips price/min-stay at the source. Wherever this file names a `hospitable_*` MCP tool, the matching subcommand is an equivalent substitute.
- **No PMS at all (Airbnb only): external mode** — AirROI's `/listings?id=<airbnb_id>` returns the subject's live content AND `photo_urls` (full gallery), so comps + photo scoring + copy still run. No occupancy/calendar and shallow reviews without a PMS — omit those sections and say so in the report.
- **Branding:** `branding.json` (gitignored, per-user; falls back to `branding.example.json`).
- Output base (while testing): `~/Desktop/Listing Optimizer/<listing-slug>/<YYYY-MM-DD>/` (override `--out-base`).
- Working dir for intermediates: `output/<YYYY-MM-DD>/<listing-slug>/`.

---

## Pipeline (per run)

Set `SLUG`, `DATE` (today, YYYY-MM-DD), and the property's IDs/coords from **PMS discovery** (see "The listings" section below). Steps 1/1.6 name Hospitable tools as the reference — **substitute the user's own PMS source** (its MCP tools or REST API) per the CLAUDE.md data contract.
Make the working dir: `output/<DATE>/<SLUG>/`.

### 0. Ask which season they're getting ready for (ALWAYS, before pulling anything)
Unless the user already said it in their request ("optimize X **for summer**"), ask first:
> **"What season are you getting ready for — summer, winter, spring, fall, or year-round?"**

If seasonal, also ask one follow-up: *"What are the big draws near you that season?"* (e.g. bike park, golf, lake, ski hill, festivals) — or infer them from the listing's location + comp titles and confirm. Then thread the season through the whole run:
- **Copy (Step 4):** title/summary/"The Space"/captions lead with that season's draws and guests; the off-season becomes a one-line footnote, never the lead.
- **Photos (Step 3 output):** prefer season-credible shots for hero + top order; if the gallery is the WRONG season (e.g. snow photos for a summer push), flag the seasonal reshoot as the **#1 action** and pick the most season-neutral shots as placeholders.
- **Comps (Step 2):** read comp titles/amenities through the seasonal lens (what do winners in this market lead with in that season?).
- **Cadence (Step 5):** mark `seasonal_swap` refreshed when the season changed the copy.
- **Year-round:** skip the seasonal framing; optimize evergreen.

### 1. Pull subject — FREE, from the user's PMS (read-only; Hospitable shown as reference)
Call these MCP tools and save each raw response into the working dir:
- `hospitable_get_property` (propertyId, `include: "listings"`) → `subject.json` (title=`public_name`, `summary`, `description`="The Space", `amenities`, `capacity`, `room_details`, `house_rules`, `address.coordinates`).
- `hospitable_get_property_images` (propertyId) → `images.json` (URLs + existing captions + order).
- `hospitable_list_reviews` (property_id) → `reviews.json` (full text + per-category `detailed_ratings` + which are unanswered). Page through `meta.last_page`.

### 1.5. Pull funnel — RankBreeze (OPTIONAL diagnostic)
RankBreeze gives **ALE Layer-3 funnel data nothing else has**: search-rank position + 1st-page impressions + CTR + booking rate, all **vs similar listings**. **Optional — never a hard dependency:**
- `mcp__rankbreeze__health_check` → if not `authenticated`, the RankBreeze MCP's session cookie expired — tell the user to refresh it (RankBreeze is an optional, separately-installed connector). If still down, **skip this step and continue** — the rest of the pipeline runs without it.
- If the listing has no `rankbreeze_id` (untracked), **skip silently**.
- Else: `mcp__rankbreeze__get_metrics <RB_ID>` (PRIMARY) + `mcp__rankbreeze__get_rankings <RB_ID>` (rank + directional context) → save `funnel.json`.
- **Trust the right number.** `get_metrics` monthly cards are the clean, trustworthy signal — **`Listing views` = real traffic (visits)**, plus `Booking rate` and `Airbnb Occupancy`. `get_rankings`' `first_page_impressions` = **search *appearances*, not visits**, over a ~90-day window, pulled by a brittle regex — use it only as directional. Lead any traffic read with **views**, never impressions.
- **Zero-pricing fence:** do NOT call `get_competitor_rates`; ignore the comp-price half of `get_calendar_rankings`. Use only `city_rank`, `listing_views`, `click_through_rate`, `booking_rate`, `occupancy`, `wishlist_additions`. Never read ADR/revenue/rates.
- RankBreeze ID = the listing's `rankbreeze_id` in `config/properties.json` (`null` → not tracked, skip this step).

### 1.6. Occupancy — the PMS calendar (SOURCE OF TRUTH) + RankBreeze cross-check
The PMS's live calendar is the authoritative occupancy record; RankBreeze occupancy is a scraped estimate kept only as a cross-check. (Hospitable tools shown — any PMS calendar works if you strip price/rates/min-stay at ingestion and keep only date + available/booked/blocked. No PMS → skip this step and omit the occupancy section.)
- `hospitable_get_property_calendar` (propertyId, start_date=today, end_date=+90d) — **read-only**. When saving `calendar.json`, **strip `price` + `min_stay`** (keep only `date` + `status.available` + `status.reason`). Price/min-stay must never land on disk — this is the zero-pricing wall at ingestion.
- `hospitable_list_reservations` (property_id) → upcoming/active count from `meta.total`.
- `.venv/bin/python scripts/occupancy.py --calendar calendar.json --reservations-count <N> --rankbreeze "Jun:0,Jul:0" --out occupancy.json`
- Output = Hospitable forward occupancy (source of truth) + monthly + a RankBreeze cross-check verdict (`agree` / `DIVERGE — trust Hospitable` / `no overlap — cross-check skipped`). **Use Hospitable occupancy everywhere; cite RankBreeze only as the cross-check.** Drop `occupancy.json`'s `report_block` straight into `result.json` → `occupancy` (no hand-transform). Check the console "non-AVAILABLE reason breakdown" if booked nights appear — confirm none were mis-classified.

### 2. Pull comps — AirROI (the ONLY paid call), price-free
```
.venv/bin/python scripts/pull_comps.py \
  --lat <LAT> --lng <LNG> --address "<ADDRESS>" --market "<MARKET>" \
  --bedrooms <BR> --baths <BA> --guests <G> \
  --out output/<DATE>/<SLUG>/comps.json
```
Gives top comps ranked by **demand** (nights booked / occupancy / reviews — never revenue) + market amenity frequency + comp title samples.

### 3. Score photos — Gemini vision
```
.venv/bin/python scripts/analyze_photos.py \
  --photos output/<DATE>/<SLUG>/images.json --limit 30 \
  --out output/<DATE>/<SLUG>/photo_scores.json
```
Gives per-photo scores + tags + `hero`, `recommended_top5_order`, `gaps`, `reshoot`, `restage`. (If no Gemini key, it writes a native-fallback manifest — then score the listed URLs yourself against `photo-rubric.md`.)

### 4. Optimize — ALE + SB7 (your reasoning)
Read `subject.json`, `comps.json`, `photo_scores.json`, `reviews.json`, and `funnel.json` (if it exists). Apply `ale-rubric.md` + `storybrand-sb7-rubric.md`. **If `funnel.json` exists, use it to TARGET levers** (ALE Layer 3): CTR low vs similar ⇒ fix **above-the-fold** (cover, title, hero); booking-after-click low ⇒ fix **below-the-fold** (top-5 depth, Summary, "The Space", reviews, amenities); don't over-rotate a lever the funnel shows already beats peers. Produce:
- **ALE scorecard** (each dimension 0–5 + gap + fix), grounded in comp gaps + photo findings.
- **New title** (~50 chars, Hero + #1 amenity; learn from comp title patterns).
- **New ~500-char Summary** doing its 4 jobs in order (who → why → distance/location → CTA), SB7-shaped. Count the characters.
- **New "The Space"** — keyword-loaded, bullets-first, Success painted, all real amenities surfaced.
- **Per-photo captions** for the recommended order (~2 sentences each, sell the moment, weave best review quotes).
- **Amenity gaps**: amenities common among winning comps but missing/buried here.
- **Season (from Step 0):** every copy element is geared to the season the user named — lead with that season's draws/guests; off-season gets one line at most. For `seasonal: true` properties, name the rotation explicitly.
- **Diagnostics & Handoff (B1 — qualitative, NO price numbers).** Synthesize content/CTR (RankBreeze) + traffic/views + occupancy (Hospitable = source of truth) + season into a *content-vs-rate/availability/seasonality* read. When content & CTR are strong but bookings/occupancy lag, say the booking gap is **not** a content problem and **hand off to a dedicated revenue/pricing tool** (e.g. a revenue-management skill or PriceLabs) for rate + availability. This section may use the words "pricing"/"revenue" (the report-only scan allows them) — but **never a price number and never a price recommendation**, and the paste content stays 100% price-free.
Assemble all of this into `output/<DATE>/<SLUG>/result.json` (shape below). **No price numbers anywhere; no price recommendations.**

### 5. Cadence — what's due
```
.venv/bin/python scripts/cadence.py --listing <SLUG> --check --today <DATE> \
  --out output/<DATE>/<SLUG>/cadence.json
```
Merge `cadence.json`'s `due` array into `result.json` under `cadence.due`. After rendering, mark what this run refreshed:
```
.venv/bin/python scripts/cadence.py --listing <SLUG> --mark title,photo_rotation,captions,the_space,conversion_audit --today <DATE>
```

### 6. Render — HTML + Markdown + paste block → Desktop
```
.venv/bin/python scripts/render_report.py \
  --data output/<DATE>/<SLUG>/result.json --listing-slug <SLUG> --date <DATE>
```
Writes `report.html`, `report.md`, `paste-block.txt` to `~/Desktop/Listing Optimizer/<SLUG>/<DATE>/`. **The render fails if any pricing/min-stay term is present** — fix the copy and re-run, never bypass with `--allow-pricing`.

### 7. Verify (every run)
- `render_report.py` **already enforces** the zero-pricing guardrail — it refuses to write if any deliverable contains a pricing/min-stay term (no bypass flag exists). A successful render means the deliverables are clean.
- Confirm the guardrail regex itself is intact: `.venv/bin/python tests/test_guardrail.py` → must print ✅.
- Belt-and-suspenders grep over the 3 Desktop files (must be 0):
  `grep -iEc 'pric(e|ed|ing)|adr|revpar|revenue|per[ -]?night|/night|nightly rate|daily rate|min(imum)?[ -]?stay|minimum night|[$€£¥][0-9]|(USD|CAD|EUR|GBP|AUD) ?[0-9]'`
- Confirm only **read** PMS calls were made during the pipeline (writes happen only in Step 8, if at all).
- Summarize for the user: ALE score, top 3 gaps, hero + top-5, occupancy (PMS vs RankBreeze), what's due, output path.

### 8. OPTIONAL — Apply to the PMS (write-back; supported PMSs only, explicit approval required)
Skip entirely unless the user asks (e.g. "apply it", "push it to my PMS") — and on **Hospitable, this step does not exist** (read-only API; the paste block is the path).
1. Confirm their PMS supports listing-content updates (Hostaway, Guesty, OwnerRez, Lodgify, Smoobu, … — check its MCP write tools or API docs). *(These paths are not yet end-to-end tested — say so, and verify in the PMS UI afterward.)*
2. Show the user the exact fields you intend to write (title / summary / description / captions) and get an explicit **"yes, apply"** in this session.
3. **Before-snapshot:** re-read the live listing and save it to `output/<DATE>/<SLUG>/before-writeback.json` — tell the user this is their undo.
4. Apply via the PMS's content-update tool/endpoint, sending a payload built from scratch containing **only** the approved content fields. Never include rates, calendar, availability, min-stay, fees, or policies; never PUT a merged/full listing object back.
5. Re-read the listing, confirm each field landed, and report exactly what changed. If anything looks wrong, offer to restore from the snapshot.
6. Mark the cadence items refreshed (already done in Step 5's flow) and remind them channel sync (Airbnb/VRBO) can take a little while.

---

## Modes — single vs multi-agent

**Default = single agent.** One listing runs the whole pipeline above in this one session. The expensive parallel work (photo scoring, comp fetch) is already concurrent *inside* the scripts, and the ALE optimization is sharper with subject + comps + photos + reviews all in one context. Do **not** split a single listing across subagents.

**Batch mode (multi-agent fan-out) — when the user says "optimize all my listings" / "refresh everything":**
Dispatch one subagent per discovered listing **in parallel** (Agent tool, `general-purpose`), each told: *"Run the listing-optimizer SKILL for slug `<SLUG>` only; follow every step and hard rule; write to the Desktop; report back ALE score + top 3 gaps + output path."* Each subagent is isolated, hits Hospitable read-only, makes exactly one AirROI call, and writes its own `~/Desktop/Listing Optimizer/<slug>/<date>/`. Collect the summaries. This is the real throughput win; routine single-listing refreshes stay single-agent.

**Optional adversarial QA pass — opt-in ("…with QA" / high-stakes listing):**
After building `result.json` (step 4) and **before** rendering, dispatch one independent subagent to critique the copy against `ale-rubric.md` + `storybrand-sb7-rubric.md` + the zero-pricing rule (prompt it to *find problems*, not to approve). Apply its fixes, then render. Catches the model rubber-stamping its own draft. Off by default to keep routine runs cheap.

---

## result.json shape (what render_report consumes)
```json
{
  "listing": {"name":"", "slug":"", "city":"", "airbnb_url":""},
  "run_date": "YYYY-MM-DD",
  "ale_scorecard": [{"dimension":"", "score":0, "gap":"", "fix":""}],
  "current": {"title":""},
  "optimized": {"title":"", "summary":"", "summary_char_count":0, "the_space":"",
                "captions":[{"order":0,"subject":"","caption":""}]},
  "photos": {"hero":0,"recommended_top5_order":[],"reshoot":[],"restage":[],"gaps":[],
             "scored":[{"order":0,"url":"","subject":"","avg":0}]},
  "comps": {"top":[{"name":"","airbnb_url":"","bedrooms":0,"guests":0,
                    "ratings":{"num_reviews":0,"rating_overall":0},
                    "performance":{"ttm_occupancy":0}}],
            "amenity_gaps":[], "title_patterns":[]},
  "funnel": {"source":"RankBreeze","rankbreeze_id":"",
             "city_rank":{"position":0,"of":0,"page":0},
             "views_monthly":{"May":"","Jun":"","Jul":""},
             "booking_rate_monthly":{"May":"","Jun":"","Jul":""},
             "occupancy_monthly":{"May":"","Jun":"","Jul":""},
             "ctr_vs_similar":{"you":"","similar":"","note":"directional — search-appearance CTR, not visits"},
             "lever_focus":"", "diagnosis":""},
  "occupancy": {"source":"Hospitable","forward_pct":0,"forward_days":90,
                "upcoming_reservations":0,"monthly":{"Jun":"0%"},
                "rankbreeze_crosscheck":"agree (max gap 0pts)"},
  "diagnostics": {"content_signal":"","traffic_signal":"","occupancy_signal":"",
                  "likely_lever":"","handoff":"… hand off to your revenue/pricing tool …"},
  "cadence": {"due":[{"item":"","last":"","due":"","status":""}]}
}
```

## The listings — DISCOVERED from the user's PMS (never hardcoded)
This tool ships with **no listings**. At run time, enumerate whatever the user has connected — these belong to whoever connected their PMS. Hospitable shown; for any other PMS use its equivalent list/detail reads (or its REST API) per the CLAUDE.md data contract:
1. `hospitable_list_properties(include="listings")` (or `scripts/hospitable_api.py properties`, or the other PMS's list endpoint) → the account's properties (id, name, public title, and the Airbnb `platform_id` where exposed).
2. The user names a listing (or "all"); match by name. Derive a **slug** = lowercase, hyphenated name (e.g. "The Beach House" → `beach-house`).
3. For the chosen property, pull full detail (`hospitable_get_property(id, include="listings")` or equivalent) → coordinates (lat/lng), capacity (bedrooms/beds/bathrooms/max), amenities, the Airbnb id. **Use these** for the subject + `pull_comps` coords/sizes — never a hardcoded registry.
4. **No PMS:** ask for the Airbnb listing URL/ID and use AirROI external mode (subject + `photo_urls` from `/listings?id=`).

### Optional per-user overrides — `config/properties.json`
For what the PMS can't tell us, the user MAY create `config/properties.json` (gitignored; see `config/properties.example.json`), keyed by slug. All fields optional:
- `rankbreeze_id` → enables the optional funnel step (Step 1.5). Absent → **skip RankBreeze** for that listing.
- `seasonal: true` → emphasize the seasonal rotation in the copy.
- `notes` → free-text steer for the optimizer.

- **Measurement loop:** the RankBreeze funnel (if configured) is re-pullable each refresh — compare rank/CTR/booking-rate to the prior run.
- **New-build mode:** if a property has no live listing/photos (unlisted, empty), build from scratch via the ALE rubric + comps; flag that photos/copy must be *created*, not refreshed.
