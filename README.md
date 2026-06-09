# Listing Optimizer

A Claude Code skill that optimizes your short-term-rental listings against the **ALE framework**
(Amenities · Location · Experiences) + StoryBrand SB7. It discovers your listings from your
connected PMS, evaluates your photos, compares you to the top performers in your market, and
writes **paste-ready** copy — new title, 500-char summary, "The Space", and per-photo captions —
plus a photo plan and a competitor-gap report. A human pastes the result into the PMS.

**It never touches pricing, and never writes back to any channel.** Output is paste-ready only.

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

Unzip this folder, open **Claude Code**, drag the folder into the chat, and say **"Set this up."**
Claude will create the environment, ask for your API keys, connect your PMS, and verify the
install (the playbook it follows is `CLAUDE.md` in this folder). Then just say
*"optimize my [listing]."*

## Manual setup

1. **Python 3.10+** and a virtualenv:
   ```bash
   # macOS / Linux
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt

   # Windows (PowerShell)
   py -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
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
1. List your Hospitable properties and let you pick one (or "all").
2. Pull the subject + comps + score the photos.
3. Write the optimized copy + report to `~/Desktop/Listing Optimizer/<listing>/<date>/`
   (`report.html`, `report.md`, `paste-block.txt`).

Optional per-listing extras (e.g. a RankBreeze listing id) go in `config/properties.json`
(see `config/properties.example.json`).

---

## Tests

```bash
.venv/bin/python -m pytest -q          # or: .venv/bin/python tests/test_guardrail.py
```
The **zero-pricing guardrail** is the #1 invariant: the paste content is word-strict (no price/ADR/
min-stay terms at all) and the report blocks any price number. Both are covered by the test suite.
