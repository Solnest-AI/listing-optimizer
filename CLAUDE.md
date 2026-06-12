# Listing Optimizer — setup & usage guide for Claude

This folder is the **Listing Optimizer**: a Claude Code skill that optimizes short-term-rental
listings against the ALE framework (see `README.md` for the human overview, and
`.claude/skills/listing-optimizer/SKILL.md` for the full pipeline).

**Hard rules: it NEVER touches pricing, calendars, or availability.** Output is paste-ready
content by default. On PMSs that support listing-content writes, it can apply the approved
copy for the user — opt-in, content-only, with a before-snapshot (see the write-back rules
below). Hospitable is always paste-only (its listing API is read-only).

---

## When the user says "set this up" (or similar), walk them through ALL of this — one step at a time, conversationally. Don't dump every step at once.

### Step 1 — Work in this folder
If the session was started elsewhere (the user dragged this folder in, or you just cloned this
repo for them from the setup prompt), work at this folder's path — and tell them: *"For the
skill to auto-load in future sessions, open Claude Code in this folder."*

### Step 2 — Python environment
Create the venv + install dependencies (skip anything that already exists):
- macOS/Linux: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Windows: `py -m venv .venv` then `.venv\Scripts\pip install -r requirements.txt`
- If Python ≥3.10 isn't installed, point them to https://www.python.org/downloads first.

Then create their secrets file: `cp .env.example .env` (Windows: `copy .env.example .env`).

### Step 3 — AirROI API key (competitor comps — required)
Walk the user through it:
1. Open **https://www.airroi.com/api/developer/activate** in a browser.
2. Sign up (or log in) to AirROI, and activate **developer / API access** on that page —
   look for an "activate" or "API key" button. (If the page layout differs, anything labeled
   "API key" in their account/developer settings is what we want.)
3. Copy the API key and paste it into this chat.
4. You (Claude) write it into `.env` as `AIRROI_API_KEY=<key>` — directly after the `=`,
   nothing else on the line. Never echo the key back into chat and never commit it.

### Step 4 — Google Gemini API key (photo scoring — required)
1. Open **https://aistudio.google.com/apikey** and sign in with any Google account.
2. Click **"Create API key"** (in a new or existing project — either is fine).
3. Copy the key (starts with `AIza…`) and paste it into this chat.
4. You write it into `.env` as `GEMINI_API_KEY=<key>`. The **free tier works** — photo scoring
   just runs slower (the script already retries rate limits; `--concurrency 2` if needed).

### Step 5 — Connect THEIR property management system (PMS)
Ask: **"Which PMS do you use?"** and branch:

**A. Hospitable** — fully built in, no MCP server needed:
1. Open **my.hospitable.com** → **Apps** → **API access** → create a **Platform token**.
2. They paste it; you write it into `.env` as `HOSPITABLE_TOKEN=<token>`.
3. Verify: `.venv/bin/python scripts/hospitable_api.py properties` → should list their
   properties. That script is the bundled read-only Hospitable client
   (`properties / property / images / reviews / calendar / reservations`).

**B. Another PMS** (Hostaway, Guesty, OwnerRez, Lodgify, Smoobu, Uplisting, Hostfully, …):
1. **If they have an MCP server for their PMS** (or can install one): confirm its read tools
   appear in the session, then at run time map those tools onto the **PMS data contract**
   below. Done.
2. **No MCP?** Ask for their PMS's API credentials (most PMSs issue an API key/token in
   account/developer settings — help them find it on their PMS's website). Save to `.env` as:
   `PMS_NAME=<pms>`, `PMS_TOKEN=<token>` (plus `PMS_BASE_URL=` / account id if that PMS
   needs one). At run time, you call that PMS's **public REST API directly** (look up its
   docs), using read endpoints for the pipeline, and map the responses onto the data contract
   below. Strip any price/rate/min-stay fields at ingestion, same as the Hospitable client does.
3. **Bonus on these PMSs:** most support listing-content writes — after a run, the user can
   say "apply it" and you push the approved copy for them (see the write-back rules below).
   Mention this during setup so they know it's available.

**C. No PMS at all (Airbnb only):** external mode still works — given their Airbnb listing
URL/ID, AirROI supplies the subject content AND the photo gallery (`/listings?id=`), so comps +
photo scoring + copy all run. Be honest about the limits: no live calendar/occupancy and
shallower review data without a PMS.

### Step 6 — Verify the install
`.venv/bin/python -m pytest -q` → expect all tests passing. If the user set up Hospitable,
the step-5 properties check is the live verification.

### Step 7 — Optional extras (mention briefly, don't push)
- `cp branding.example.json branding.json` and edit → their brand on the reports.
- `config/properties.example.json` → `config/properties.json` for per-listing extras
  (e.g. a RankBreeze id, `seasonal: true`).

### Step 8 — Confirm + teach usage
Tell them setup is done and they can now say **"optimize my [listing name]"** or
**"run the listing optimizer."** (First thing the optimizer does is ask **which season they're
getting ready for** — copy and photo plan get geared to it.) Reports land in
`~/Desktop/Listing Optimizer/<listing>/<date>/`
as HTML + Markdown + a paste-ready block. On PMSs that support content writes, they can also
say **"apply it"** after reviewing a run to push the new copy (Hospitable users paste manually
— its API is read-only).

---

## The PMS data contract (what the pipeline needs from ANY PMS)

| # | Data | Used for | Required? |
|---|---|---|---|
| 1 | List of their properties (names + ids) | discovery / picking the listing | yes |
| 2 | Subject content: title, summary, description, amenities, capacity, lat/lng | the ALE optimization + comps query | yes |
| 3 | Photos: URLs + captions + current order | Gemini photo scoring | yes |
| 4 | Reviews (text + ratings + responded?) | Reviews channel + copy quotes | nice-to-have |
| 5 | Calendar availability (dates + available/booked/blocked ONLY) | occupancy (source of truth) | nice-to-have |
| 6 | Upcoming reservation count | occupancy context | nice-to-have |
| 7 | Listing-content UPDATE (title/description/captions) | optional write-back (Step 8) | optional — Hospitable doesn't offer it |

Rules when mapping any PMS onto this: the **pipeline (steps 1–7) uses read-only endpoints/tools
only**; **strip price, rates, and min-stay** from calendar data at ingestion; missing
nice-to-haves degrade gracefully (the report simply omits those sections).

**Write-back rules (row 7 — the ONLY permitted writes, ever):**
- Only after the user sees the exact content and explicitly says to apply it, in that session.
- Save a `before-writeback.json` snapshot of the live content first (their undo).
- Send ONLY content fields (title, summary/description, captions/photo order) in a payload
  built from scratch — never rates, calendar, availability, min-stay, fees, policies, and
  never a merged/full listing object.
- Re-read afterward and report exactly what changed.
- Calendar/pricing endpoints are forbidden on every PMS, always.
- Non-Hospitable write paths are not yet end-to-end tested — say so, and have the user verify
  in their PMS UI after the first write.

## Running the optimizer

Follow `.claude/skills/listing-optimizer/SKILL.md` exactly — it is the pipeline of record.
It names Hospitable tools as the reference implementation; substitute the user's PMS source
(MCP tools, the bundled `scripts/hospitable_api.py`, or their PMS's REST API) per the
data contract above.

## Invariants (do not bend these)

- **Zero pricing** in any output — `render_report.py` enforces it and has no bypass.
- **Pricing/calendar/availability writes: never, on any PMS.** The only permitted writes are
  the Step-8 content write-back, under its rules (explicit approval, snapshot, content-only).
- Keys live only in `.env` (gitignored). Never print or commit them.
