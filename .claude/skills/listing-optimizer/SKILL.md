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
5. **The memory record is price-free.** `memory.py` re-runs the same guardrail and refuses any record with a price/rate/min-stay term. And **NEVER hardcode a personal Supabase project ref / MCP name in the repo** — those live only in the gitignored `config/memory.json` (see `config/memory.example.json`).

## Reference rubrics (read before optimizing)
- `references/ale-rubric.md` — the engine + the scorecard you produce
- `references/storybrand-sb7-rubric.md` — emotional layer for the copy
- `references/photo-rubric.md` — what photos are scored against
- `references/airbnb-field-limits.md` — **hard character caps + photo specs** every output must respect

## Environment (fully self-contained — nothing outside this folder but keys)
- Python: `.venv/bin/python` (project venv; on Windows use `.venv\Scripts\python` — substitute throughout). Scripts live in `scripts/`. If no `.venv` exists yet, create it first: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
- **Keys: project `.env`** (gitignored; see `.env.example`). `AIRROI_API_KEY` (comps) + `GEMINI_API_KEY` (photos). Both `airroi_client.py` and `analyze_photos.py` auto-load it.
- **AirROI: vendored** in `scripts/airroi_client.py` — direct API calls, no external repo.
- **Listings: discovered live from the user's connected PMS** — ANY PMS. Nothing hardcoded. This file names **Hospitable tools as the reference implementation**; for any other PMS (Hostaway, Guesty, OwnerRez, Lodgify, Smoobu, Uplisting, Hostfully, …) substitute its MCP read-tools or its REST API (read-only, `PMS_TOKEN` in `.env`), mapped onto the **PMS data contract in CLAUDE.md** (listings → subject content → photos → reviews → calendar availability → reservations). Optional per-user overrides in `config/properties.json` (gitignored; see `config/properties.example.json`).
- **Hospitable without an MCP: bundled REST fallback** — `scripts/hospitable_api.py` (`properties`/`property`/`images`/`reviews`/`calendar`/`reservations`, needs `HOSPITABLE_TOKEN` in `.env`). Same read-only data as the MCP tools; its `calendar` subcommand strips price/min-stay at the source. Wherever this file names a `hospitable_*` MCP tool, the matching subcommand is an equivalent substitute.
- **No PMS at all (Airbnb only): external mode** — AirROI's `/listings?id=<airbnb_id>` returns the subject's live content AND `photo_urls` (full gallery), so comps + photo scoring + copy still run. No occupancy/calendar and shallow reviews without a PMS — omit those sections and say so in the report.
- **Branding:** `branding.json` (gitignored, per-user; falls back to `branding.example.json`).
- **Run-history memory (optional bonus):** `scripts/memory.py` records a compact PRICE-FREE summary of every run so the next one can compare (ALE trend, "title last changed N weeks ago", views/CTR movement). **Local layer is always on, zero setup** → appends to `state/history.jsonl` (gitignored). **Supabase layer is optional** — detect, in order: (1) `config/memory.json` naming a `supabase_mcp`; (2) else a single writable Supabase MCP in the session; (3) else `SUPABASE_URL`+`SUPABASE_SERVICE_KEY` in `.env` (REST path); (4) else skip with a friendly note (local history still saved). Same guardrail as the renderer — `memory.py` refuses any record with a price/rate/min-stay term.
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
Save each raw response into the working dir as a **file**. These files are pipeline inputs, not reading material — Step 4 reads a digest of them, never the raw JSON.

> ⚠️ **Token wall — read this before you pull anything.** Every raw JSON payload you pull through an MCP tool lands in context and is then re-sent on **every remaining turn** of the run. A single `list_properties` response can be 120k+ characters; landing it once at turn 3 of a 90-turn run costs ~30k tokens ninety times over. **Prefer the bundled script path** (`scripts/hospitable_api.py … --out <file>`), which writes the identical JSON straight to disk and prints one summary line. Use MCP tools only for what the script cannot do, and never paste, echo, or `cat` a raw pipeline JSON file into the conversation.

- `subject.json` — `.venv/bin/python scripts/hospitable_api.py property --property-id <UUID> --out output/<DATE>/<SLUG>/subject.json` (or `hospitable_get_property` with `include: "listings"`). Carries title=`public_name`, `summary`, `description`="The Space", `amenities`, `capacity`, `room_details`, `house_rules`, `address.coordinates`.
- `images.json` — `… hospitable_api.py images --property-id <UUID> --out output/<DATE>/<SLUG>/images.json` (or `hospitable_get_property_images`). URLs + existing captions + order.
- `reviews.json` — `… hospitable_api.py reviews --property-id <UUID> --out output/<DATE>/<SLUG>/reviews.json` (or `hospitable_list_reviews`). **Cap at the 20 most recent. Do NOT page through `meta.last_page`.** Full review history is 150+ reviews / ~285KB on an established listing, and none of it past the recent window changes the copy. If you are on the MCP path, request page 1 only and stop; Step 4's digest enforces the 20-review cap regardless of what landed on disk.

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

### 1.7. Memory — read prior runs (baseline for the trend)
Pull the last run(s) so Step 4 can compare and Step 7 can report a trend. Local is always available; Supabase is authoritative if connected.
- **Local (always):** `.venv/bin/python scripts/memory.py prior --listing <SLUG>` → most-recent prior run(s) from `state/history.jsonl`. First-ever run returns `[]` — say "no prior run yet, this is the baseline" and continue.
- **Supabase (if connected, per the detection ladder):** read authoritative history too — emit the SQL with `.venv/bin/python scripts/memory.py sql-prior --listing <SLUG>` and run it via the Supabase MCP's `execute_sql` (or, for the REST path, `.venv/bin/python scripts/memory.py prior --listing <SLUG> --rest`).
- Surface from whichever you read: **prior ALE total, prior title, and prior funnel views/CTR** — these feed the comparison below.

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
**First, build the digest — then read ONLY the digest.** The raw pipeline files total ~350KB (~89k tokens) on a real listing and would be re-sent on every remaining turn. This collapses them to ~18KB (~4.5k tokens), a 20x cut, with no loss the rubrics care about. Run it verbatim from the project root:

```bash
.venv/bin/python - output/<DATE>/<SLUG> <<'PYEOF'
import json,sys,ast,pathlib
REVIEW_CAP=20
d=pathlib.Path(sys.argv[1]); R=lambda n:(json.loads((d/n).read_text()) if (d/n).exists() else None)
def lit(s):
    if isinstance(s,dict): return s
    try: return ast.literal_eval(s) if isinstance(s,str) else {}
    except Exception: return {}
o=[]; A=o.append
s=(R("subject.json") or {}).get("data") or {}
A("# SUBJECT\n"+json.dumps({k:s.get(k) for k in("name","public_name","summary","description","amenities","capacity","room_details","house_rules")},ensure_ascii=False)[:6000])
A("address: "+json.dumps(s.get("address") or {},ensure_ascii=False)[:400])
c=R("comps.json") or {}
A("\n# COMPS (ranked by demand; no pricing)")
for t in (c.get("top_comps") or [])[:10]:
    r=t.get("ratings") or {}; p=t.get("performance") or {}
    A(f"- {t.get('name','')[:70]} | {t.get('bedrooms')}br/{t.get('baths')}ba/{t.get('guests')}g | {r.get('rating_overall')}* ({r.get('num_reviews')}rev) | occ {p.get('ttm_occupancy')}")
A("title_samples: "+json.dumps(c.get("comp_title_samples") or [],ensure_ascii=False)[:900])
A("market_amenity_freq(top30): "+", ".join(f"{a['amenity']}={a['pct']}%" for a in (c.get("market_amenity_frequency") or [])[:30]))
p=R("photo_scores.json") or {}
A(f"\n# PHOTOS hero={p.get('hero')} top5={p.get('recommended_top5_order')} reshoot={p.get('reshoot')} restage={p.get('restage')}")
A("gaps: "+json.dumps(p.get("gaps") or [],ensure_ascii=False))
for ph in (p.get("photos") or []):
    A(f"- #{ph.get('order')} avg={ph.get('avg')} {str(ph.get('subject'))[:60]} | flags={ph.get('flags')} | cap={str(ph.get('caption') or '')[:60]}")
rv=(R("reviews.json") or {}).get("data") or []
A(f"\n# REVIEWS ({min(len(rv),REVIEW_CAP)} most recent of {len(rv)})")
cat={}; un=0
for r in rv:
    for dr in (lit(r.get("private")).get("detailed_ratings") or []): cat.setdefault(dr.get("type"),[]).append(dr.get("rating"))
    if not r.get("responded_at"): un+=1
for r in sorted(rv,key=lambda x:str(x.get("reviewed_at")),reverse=True)[:REVIEW_CAP]:
    pub=lit(r.get("public"))
    A(f"- {str(r.get('reviewed_at'))[:10]} {pub.get('rating')}* {str(pub.get('review') or '')[:320]}")
A("category_avgs(all): "+json.dumps({k:round(sum(v)/len(v),2) for k,v in cat.items() if v}))
A(f"unanswered_reviews(all): {un}")
for n,t in (("funnel.json","FUNNEL"),("occupancy.json","OCCUPANCY")):
    x=R(n)
    if x: A(f"\n# {t}\n"+json.dumps(x,ensure_ascii=False)[:1500])
out=d/"digest.md"; out.write_text("\n".join(o),encoding="utf-8")
raw=sum((d/f).stat().st_size for f in ("subject.json","comps.json","photo_scores.json","reviews.json") if (d/f).exists())
print(f"[digest] {out} ({out.stat().st_size} B from {raw} B raw)")
PYEOF
```

Then **Read `output/<DATE>/<SLUG>/digest.md` and nothing else.** Do not also Read `subject.json`, `comps.json`, `photo_scores.json`, `reviews.json`, `images.json` or `calendar.json` — the digest already carries every field the rubrics score, review text is capped at the 20 most recent (category averages and the unanswered count are still computed across the full set), and re-reading a raw file undoes the entire saving. Only go back to a raw file if the digest is visibly missing something you need, and then read just that file. Apply `ale-rubric.md` + `storybrand-sb7-rubric.md`. **If `funnel.json` exists, use it to TARGET levers** (ALE Layer 3): CTR low vs similar ⇒ fix **above-the-fold** (cover, title, hero); booking-after-click low ⇒ fix **below-the-fold** (top-5 depth, Summary, "The Space", reviews, amenities); don't over-rotate a lever the funnel shows already beats peers. Produce:
- **ALE scorecard** (each dimension 0–5 + gap + fix), grounded in comp gaps + photo findings.
- **New title** — **hard cap 50 chars, aim 32–45** (mobile truncates ~32; front-load the hook). Sentence case, no emojis, no repeated symbols (per `airbnb-field-limits.md`). Learn from comp title patterns. **Count the characters.**
- **New Summary** — **hard cap 500 chars** doing its 4 jobs in order (who → why → distance/location → CTA), SB7-shaped, with the persuasive core in the **first ~295 chars** (the app's above-the-fold cut). **Count the characters** (`summary_char_count`).
- **New "The Space"** — keyword-loaded, bullets-first, Success painted, all real amenities surfaced. Lead with the strongest content in the **first ~1,000 chars**; keep total ~1,500–2,500 (proven safe via PMS sync). No phone/email/URLs in any description field.
- **Per-photo captions** for the recommended order (~2 sentences each, **≤250 chars per caption**, sell the moment, weave best review quotes).
- **Amenity gaps**: amenities common among winning comps but missing/buried here.
- **Season (from Step 0):** every copy element is geared to the season the user named — lead with that season's draws/guests; off-season gets one line at most. For `seasonal: true` properties, name the rotation explicitly.
- **Diagnostics & Handoff (B1 — qualitative, NO price numbers).** Synthesize content/CTR (RankBreeze) + traffic/views + occupancy (Hospitable = source of truth) + season into a *content-vs-rate/availability/seasonality* read. When content & CTR are strong but bookings/occupancy lag, say the booking gap is **not** a content problem and **hand off to a dedicated revenue/pricing tool** (e.g. a revenue-management skill or PriceLabs) for rate + availability. This section may use the words "pricing"/"revenue" (the report-only scan allows them) — but **never a price number and never a price recommendation**, and the paste content stays 100% price-free.
- **Compare to the prior run (from Step 1.7):** note ALE movement (e.g. up/down/flat) and whether the title or "The Space" actually changed since last time; **don't re-suggest copy identical to what's already live** from the prior run — push it forward or leave it. If no prior run, this is the baseline.
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

### 6.5. Memory — record this run
Always write local history; mirror to Supabase only if connected. `memory.py` re-runs the zero-pricing guardrail and refuses a record with any price/rate/min-stay term.
- **Local (always):**
  ```
  .venv/bin/python scripts/memory.py record \
    --result output/<DATE>/<SLUG>/result.json \
    --result-path output/<DATE>/<SLUG>/result.json \
    --season <SEASON> [--applied] \
    --cadence-marked title,photo_rotation,captions \
    --out output/<DATE>/<SLUG>/record.json
  ```
  Pass `--applied` only if Step 8 actually pushed content this run. `--cadence-marked` mirrors what Step 5 refreshed.
- **Supabase (if connected, per the detection ladder):**
  1. Ensure the table exists: Supabase MCP `list_tables`; if `listing_optimizer_runs` is missing, apply `migrations/001_listing_optimizer_runs.sql` via the MCP `apply_migration` (one-time, in the user's own project).
  2. `.venv/bin/python scripts/memory.py sql-upsert --record output/<DATE>/<SLUG>/record.json` → run the emitted SQL via the MCP `execute_sql` (or `.venv/bin/python scripts/memory.py rest-upsert --record output/<DATE>/<SLUG>/record.json` for the REST path).
- **No Supabase:** print a one-line note — "run history saved locally to `state/history.jsonl`; connect a Supabase MCP (e.g. via the Revenue Manager) to sync it across devices."

### 7. Verify (every run)
- `render_report.py` **already enforces** the zero-pricing guardrail — it refuses to write if any deliverable contains a pricing/min-stay term (no bypass flag exists). A successful render means the deliverables are clean.
- Confirm the guardrail regex itself is intact: `.venv/bin/python tests/test_guardrail.py` → must print ✅.
- Belt-and-suspenders grep over the 3 Desktop files (must be 0):
  `grep -iEc 'pric(e|ed|ing)|adr|revpar|revenue|per[ -]?night|/night|nightly rate|daily rate|min(imum)?[ -]?stay|minimum night|[$€£¥][0-9]|(USD|CAD|EUR|GBP|AUD) ?[0-9]'`
- Confirm only **read** PMS calls were made during the pipeline (writes happen only in Step 8, if at all).
- Summarize for the user: ALE score, top 3 gaps, hero + top-5, occupancy (PMS vs RankBreeze), what's due, output path.
- **Include a one-line trend vs the prior run** (from Step 1.7), e.g. "ALE 3.3 → 3.6 since 2026-06-08." First run: say it's the baseline.

### 8. OPTIONAL — Apply to the PMS (write-back; supported PMSs only, explicit approval required)
Skip entirely unless the user asks (e.g. "apply it", "push it to my PMS") — and on **Hospitable, this step does not exist** (read-only API; the paste block is the path).
1. Confirm their PMS supports listing-content updates (Hostaway, Guesty, OwnerRez, Lodgify, Smoobu, … — check its MCP write tools or API docs). *(These paths are not yet end-to-end tested — say so, and verify in the PMS UI afterward.)*
2. Show the user the exact fields you intend to write (title / summary / description / captions) and get an explicit **"yes, apply"** in this session.
3. **Before-snapshot:** re-read the live listing and save it to `output/<DATE>/<SLUG>/before-writeback.json` — tell the user this is their undo.
4. Apply via the PMS's content-update tool/endpoint, sending a payload built from scratch containing **only** the approved content fields. Never include rates, calendar, availability, min-stay, fees, or policies; never PUT a merged/full listing object back.
5. Re-read the listing, confirm each field landed, and report exactly what changed. If anything looks wrong, offer to restore from the snapshot. **If the write "succeeded" but the re-read shows OLD content:** Airbnb's *locked fields* feature is probably blocking API updates (happens when the host edited that field directly on Airbnb) — see `references/airbnb-field-limits.md`; the user must unlock the field in their Airbnb PMS settings or paste manually.
6. Mark the cadence items refreshed (already done in Step 5's flow) and remind them channel sync (Airbnb/VRBO) can take a little while.

---

## Modes — single vs multi-agent

**The rule is the listing COUNT, not the wording of the request. Count the listings first, before you pull anything:**

| Listings in this request | Mode |
|---|---|
| **exactly 1** | single agent, run the pipeline inline |
| **2 or more** | **fan out — one subagent per listing, always** |

**ONE listing = single agent.** The whole pipeline runs in this session. The expensive parallel work (photo scoring, comp fetch) is already concurrent *inside* the scripts, and the ALE optimization is sharper with subject + comps + photos + reviews in one context. Do **not** split a single listing across subagents.

**TWO OR MORE listings = batch mode, mandatory.** This is not opt-in and it is not gated on any particular phrasing. "Optimize all my listings", "refresh everything", "run it on these three", "do the cabin and the condo", or three slugs pasted in a row all trigger it identically. Dispatch one subagent per listing **in parallel** (Agent tool, `general-purpose`), each told: *"Run the listing-optimizer SKILL for slug `<SLUG>` only; follow every step and hard rule; write to the Desktop; report back ALE score + top 3 gaps + output path."* Each subagent is isolated, hits the PMS read-only, makes exactly one AirROI call, and writes its own `~/Desktop/Listing Optimizer/<slug>/<date>/`. Collect the summaries and report them together.

> ⚠️ **Never run multiple listings sequentially in one context.** Each listing costs roughly 30 turns. Run back-to-back in a single session, listing 2 carries all of listing 1's accumulated output on every one of its turns, and listing 3 carries both — the cost grows with the square of the listing count, not linearly. Fanning out keeps each listing's context private and flat. If the user explicitly tells you not to use subagents, do the listings in **separate sessions** instead and say so; do not silently chain them into one context.

**Optional adversarial QA pass — opt-in ("…with QA" / high-stakes listing):**
After building `result.json` (step 4) and **before** rendering, dispatch one independent subagent to critique the copy against `ale-rubric.md` + `storybrand-sb7-rubric.md` + the zero-pricing rule (prompt it to *find problems*, not to approve). Apply its fixes, then render. Catches the model rubber-stamping its own draft. Off by default to keep routine runs cheap.

---

## result.json shape (what render_report consumes)
```json
{
  "listing": {"name":"", "slug":"", "city":"", "airbnb_url":""},
  "run_date": "YYYY-MM-DD",
  "season": "",
  "applied": false,
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

### Optional memory override — `config/memory.json`
Only needed if Supabase auto-detection needs help (e.g. multiple Supabase MCPs and you must name the writable one). Gitignored; see `config/memory.example.json`. Fields (all optional, default = auto-detect): `supabase_mcp` (name of the writable Supabase MCP), `supabase_project_ref` (the project ref). **Never commit this file** — a personal project ref / MCP name stays local.

- **Measurement loop:** the RankBreeze funnel (if configured) is re-pullable each refresh — compare rank/CTR/booking-rate to the prior run.
- **New-build mode:** if a property has no live listing/photos (unlisted, empty), build from scratch via the ALE rubric + comps; flag that photos/copy must be *created*, not refreshed.
