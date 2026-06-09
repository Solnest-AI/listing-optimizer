# Listing Optimizer — setup & usage guide for Claude

This folder is the **Listing Optimizer**: a Claude Code skill that optimizes short-term-rental
listings against the ALE framework (see `README.md` for the human overview, and
`.claude/skills/listing-optimizer/SKILL.md` for the full pipeline).

**Hard rules: it NEVER touches pricing and NEVER writes to the user's PMS.** All Hospitable
access is read-only; output is paste-ready content the user enters manually.

---

## When the user says "set this up" (or similar), do this:

1. **Make sure you're working IN this folder.** If the Claude Code session was started
   elsewhere and the user dragged this folder in, work at this folder's path — and tell the
   user: *"For the skill to auto-load in future sessions, open Claude Code in this folder."*

2. **Create the venv + install dependencies** (skip anything that already exists):
   - macOS/Linux: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
   - Windows: `py -m venv .venv` then `.venv\Scripts\pip install -r requirements.txt`
   - If Python ≥3.10 isn't installed, point them to python.org/downloads first.

3. **Create `.env` from the template** (`cp .env.example .env`), then ask the user for their
   keys — one at a time, telling them exactly where to get each:
   - `AIRROI_API_KEY` — competitor comps. Get it: https://www.airroi.com/api/developer/activate
   - `GEMINI_API_KEY` — photo scoring. Get it: https://aistudio.google.com/apikey
   - `HOSPITABLE_TOKEN` — only needed if they do NOT have a Hospitable MCP server connected
     (see step 4). Get it: my.hospitable.com → Apps → API access → create a Platform token.
   Write the values into `.env` for them. Never echo a key back into the chat or commit it.

4. **Connect their PMS (Hospitable) — two options, either works:**
   - **MCP server** (if they have one): tools named `hospitable_*` appear in the session.
   - **No MCP needed:** the bundled fallback `scripts/hospitable_api.py` calls the Hospitable
     Public API v2 directly using `HOSPITABLE_TOKEN`. Verify it with:
     `.venv/bin/python scripts/hospitable_api.py properties` → should list their properties.

5. **Verify the install:** run `.venv/bin/python -m pytest -q` → expect all tests passing.

6. **Optional extras** (mention briefly, don't push):
   - `cp branding.example.json branding.json` and edit → their brand on the reports.
   - `config/properties.example.json` → `config/properties.json` for per-listing extras
     (e.g. a RankBreeze id, `seasonal: true`).

7. **Confirm + teach usage:** tell them setup is done and they can now say
   **"optimize my [listing name]"** or **"run the listing optimizer"**. Reports land in
   `~/Desktop/Listing Optimizer/<listing>/<date>/` as HTML + Markdown + a paste-ready block.

## Running the optimizer

Follow `.claude/skills/listing-optimizer/SKILL.md` exactly — it is the pipeline of record.
Where SKILL.md calls `hospitable_*` MCP tools, and no MCP is connected, substitute the
equivalent `scripts/hospitable_api.py` subcommand (`properties` / `property` / `images` /
`reviews` / `calendar` / `reservations`) — same data, same read-only guarantee, and the
`calendar` subcommand already strips price/min-stay at the source.

## Invariants (do not bend these)

- **Zero pricing** in any output — `render_report.py` enforces it and has no bypass.
- **Read-only PMS** — never call any create/update/delete endpoint or tool.
- Keys live only in `.env` (gitignored). Never print or commit them.
