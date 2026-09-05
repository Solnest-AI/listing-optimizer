# Listing Optimizer

A Claude Code skill that optimizes your short-term-rental listings against the **ALE framework**
(Amenities · Location · Experiences) + StoryBrand SB7. It discovers your listings from your
connected PMS, evaluates your photos, compares you to the top performers in your market, and
writes **paste-ready** copy — new title, 500-char summary, "The Space", and per-photo captions —
plus a photo plan and a competitor-gap report. A human pastes the result into the PMS.

**It never touches pricing, calendars, or availability.** Output is paste-ready by default;
on PMSs that support content updates (Hostaway, Guesty, OwnerRez, Lodgify, …) you can review a
run and say *"apply it"* — Claude pushes the new copy after taking a backup snapshot.
(Hospitable is paste-only: its listing API is read-only.)

---

## What it uses

| Step | Source | Notes |
|---|---|---|
| Your listings + subject content + reviews + occupancy | **Your PMS** | Free, read-only. Listings discovered live — nothing hardcoded. **Hospitable works out of the box** (just a token); Hostaway / Guesty / OwnerRez / Lodgify / Smoobu / others connect via their MCP server or API token. No PMS? Airbnb-only mode works from your listing URL. |
| Competitor comps | **AirROI API** | The only paid call. Needs `AIRROI_API_KEY`. |
| Photo scoring (hero + top-5) | **Google Gemini API** | Needs `GEMINI_API_KEY` (free tier OK). Scores each photo 0–5 on quality + ALE fit. |
| Funnel (rank/CTR/views) — optional | **RankBreeze MCP** | Optional connector; skipped if not configured. |

---

## Quick start (easiest)

Open **Claude Code** (in any folder) and paste this:

```text
Set up the Listing Optimizer from Solnest AI for me, one step at a time:
1. Make sure git and Python 3.10+ are installed — help me install whatever's missing.
2. Clone https://github.com/Solnest-AI/listing-optimizer.git into my home folder.
3. Read the CLAUDE.md inside the cloned folder — it is the full setup playbook — and follow
   it exactly: create the Python environment, install the dependencies, set up my .env,
   walk me through getting my AirROI API key and my free Google Gemini API key, and connect
   my property management system (or Airbnb-only mode if I don't have one).
4. Run the test suite to verify, then show me how to run my first optimization.
Do everything you can yourself instead of telling me to do it, and don't skip steps.
```

Claude handles the whole install — clone, Python environment, dependencies, API keys, PMS —
and verifies it at the end. Then just say *"optimize my [listing]."*

(Already cloned the repo yourself? Open Claude Code in that folder and say **"set this up."**)

**No git / prefer a zip?** Download
[the latest zip](https://github.com/Solnest-AI/listing-optimizer/archive/refs/heads/main.zip)
(no GitHub account needed), unzip it, open **Claude Code** in that folder, and say
**"set this up."** Updating a zip install is manual and easy to get wrong — see below.

## Updating

**If you cloned with git (recommended)** — from the project folder:

```bash
git pull
```

That is the whole update. Your setup is untouched: `.env` (your AirROI, Gemini and PMS
keys), `config/properties.json` (RankBreeze ids, seasonal flags, notes), `branding.json`,
`state/` (your cadence history) and `output/` (past runs) are all **gitignored**, so git
never overwrites, reverts or deletes them. Only the skill and scripts update. Your MCP
connections (PMS, RankBreeze) live in your Claude Code config outside this folder and are
not touched either.

If `git pull` complains that local changes would be overwritten, you edited a shipped file.
`git stash && git pull && git stash pop` keeps your edit; `git checkout -- <file> && git pull`
discards it. Neither can touch the gitignored files above.

**If you installed from the zip** — re-download, unzip to a *new* folder, then copy these
across from your old folder before deleting it:

```
.env                      your API keys (AirROI, Gemini, PMS token)
config/properties.json    RankBreeze ids, seasonal flags, per-listing notes
branding.json             your report branding
state/                    cadence history (what's due, what you last refreshed)
```

Then recreate the venv in the new folder (`python3 -m venv .venv && .venv/bin/pip install -r
requirements.txt`). Miss any of the four and you silently lose that setup. This is why git is
worth the one-time switch — ask Claude to "switch me to git" and it will move you over.

## Manual setup

1. **Python 3.10+** and a virtualenv:
   ```bash
   # macOS / Linux
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   .venv/bin/pip install -r requirements-dev.txt   # pytest, for the verify step below

   # Windows (PowerShell)
   py -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   .venv\Scripts\pip install -r requirements-dev.txt
   ```
   (On Windows, wherever this README or the skill says `.venv/bin/python`, use `.venv\Scripts\python`.)
2. **Keys** — copy the template and fill in your two keys:
   ```bash
   cp .env.example .env
   #   AIRROI_API_KEY=...   (https://www.airroi.com/api/developer/activate)
   #   GEMINI_API_KEY=...   (Google AI Studio)
   ```
3. **Branding** (optional) — your company name/colors on the report:
   ```bash
   cp branding.example.json branding.json   # then edit
   ```
4. **Connect your PMS:**
   - **Hospitable** (easiest): put a Platform token in `.env` as `HOSPITABLE_TOKEN`
     (my.hospitable.com → Apps → API access) — the bundled `scripts/hospitable_api.py`
     reads your listings directly. No MCP server needed.
   - **Any other PMS** (Hostaway, Guesty, OwnerRez, Lodgify, Smoobu, …): connect its MCP
     server to Claude Code, or put its API token in `.env` (`PMS_NAME=` / `PMS_TOKEN=`) and
     Claude reads it via that PMS's API — read-only either way.
   - **No PMS:** Airbnb-only mode — Claude pulls your listing + photos via AirROI from your
     Airbnb URL (no occupancy data in this mode).
   (RankBreeze MCP is optional, for funnel data.)

`.env`, `branding.json`, and `config/properties.json` are **gitignored** — they hold your keys/brand
and never get committed or shared.

---

## Usage

In Claude Code, just ask: **"optimize my [listing]"** or **"run the listing optimizer."** It will:
1. Ask **what season you're getting ready for** (summer / winter / spring / fall / year-round) —
   all copy and the photo plan get geared to it.
2. List your properties from your PMS and let you pick one (or "all").
3. Pull the subject + comps + score the photos.
4. Write the optimized copy + report to `~/Desktop/Listing Optimizer/<listing>/<date>/`
   (`report.html`, `report.md`, `paste-block.txt`).

Optional per-listing extras (e.g. a RankBreeze listing id) go in `config/properties.json`
(see `config/properties.example.json`).

---

## Memory (optional)

Every run is remembered locally — a compact, price-free summary lands in `state/history.jsonl`
(gitignored) so the next run can show you the trend (ALE movement, title last changed, views/CTR).
This is always on, zero setup. If you already have a **Supabase** MCP connected (for example from
the Revenue Manager), the same record also syncs to a `listing_optimizer_runs` table — run
`migrations/001_listing_optimizer_runs.sql` once in your own Supabase project (or let Claude apply
it for you the first time). No Supabase? It just keeps the local history. The memory **never stores
pricing** — it's scanned with the same zero-pricing guardrail as the reports.

---

## Updating

Improvements land in this repo. To pull the latest version into your folder:

```bash
git pull
```

(Or just ask Claude Code: *"update the listing optimizer."*) Your `.env`, `branding.json`, and
`config/properties.json` are gitignored, so updates never touch your keys or settings. If you
downloaded the zip instead of cloning, re-download and copy your `.env` across.

---

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt   # pytest, if you skipped it above
.venv/bin/python -m pytest -q          # or: .venv/bin/python tests/test_guardrail.py
```
The **zero-pricing guardrail** is the #1 invariant: the paste content is word-strict (no price/ADR/
min-stay terms at all) and the report blocks any price number. Both are covered by the test suite.

---

## License

MIT. See [LICENSE](LICENSE).
