# Listing Optimizer: agent instructions

This is a local Claude Code skill, not a hosted app. Read
`.claude/skills/listing-optimizer/SKILL.md` when running an optimization.
Python scripts collect and validate evidence; the agent writes ALE + StoryBrand copy.

## Boundaries

- PMS access is read-only during analysis. Never change pricing, calendars, availability,
  minimum stays, fees or policies. Strip monetary fields before storing calendar/comps.
- Deliver HTML, Markdown and paste-ready copy. Content write-back requires exact reviewed
  content, explicit approval, a before-snapshot, a content-only payload and a confirming
  read. Hospitable remains paste-only. Other PMS write paths are not bundled/tested.
- Credentials live in the gitignored `.env`. Never print keys, commit them, overwrite an
  existing `.env`, or send account config to the model just to inspect which keys exist.
- Run history is local (`state/history.jsonl`). Nothing writes to a database.

## Setup or update

Trigger: "Set up the Listing Optimizer", the README's start line, or the first optimization
in a session when `.venv` is missing or `scripts/check_keys.py` does not exit 0. The setup
script installs what is missing; never send the user off to install Python, Git or packages
by hand. You run every command yourself; the attendee never opens a terminal.

### The credential contract (the same one as the STR Secrets Connections kit)

- **NEVER ask for an API key in chat.** Not "paste it here", not "what's your key".
- Never put a value into `.env` with the Edit or Write tools. `scripts/kit_link.py` copies
  keys from the attendee's connections kit; anything still blank, the attendee pastes into
  `.env` themselves after setup opens that file. You read `check_keys.py` output afterward;
  you never type a secret.
- A key pasted into chat by accident: one line, no lecture. Rotate it in the vendor's
  dashboard and paste the new one into the file.

### Steps

1. Existing `.git`: inspect status, preserve local edits, then `git pull --ff-only`. Never
   clone a second copy. ZIP install: preserve local config/state; convert in place to git
   only if asked, never by deleting the folder.
2. Run the setup script without prompts. It finds or installs uv and Python 3.13 (uv's own
   installer, per user, no admin prompt; Python under `%USERPROFILE%\.uv\python`, where the
   connections kit keeps it, because the desktop app is a Store app that redirects AppData
   writes) and Git (winget, which may show one Windows approval prompt), builds `.venv`,
   installs the requirements, creates `.env` from `.env.example` only if absent, links the
   connections kit (step 3), runs the tests and checks each key with a free read-only
   request. It never overwrites keys or files.
   - Windows, from the Bash tool (Git Bash) or PowerShell:
     `powershell.exe -NoProfile -ExecutionPolicy Bypass -File setup.ps1 -NoPrompt -AutoInstall`
   - Mac/Linux: `bash setup.sh --no-prompt --auto-install`
   Exit 0: ready. Exit 2: read its `!!` lines and continue with step 4. Exit 1: show the
   user its last lines and stop. If the app's Auto mode blocks the command ("Blocked", "no
   verdict"), that is the app's classifier, not the command: switch the session to Manual
   and rerun. If Git was just installed, tell the user to restart Claude Code afterwards.
3. The kit bridge. Setup runs `scripts/kit_link.py`, which finds the attendee's
   `str-secrets-connections` folder (next to this one, Desktop, Documents, Downloads),
   writes `SKILL_PATH_LISTING_OPTIMIZER=<this folder>` into the kit's `.env` (a path, not a
   secret) so the kit's `fan-out-env.sh` keeps this folder in sync from now on, and merges
   the kit's keys into `./.env` with the kit's rules (declared names only, non-blank only,
   nothing removed, `HOSPITABLE_TOKEN` from `HOSPITABLE_API_KEY`). It never prints a value.
   No kit found: ask where the connections folder is (a path) and run
   `scripts/kit_link.py --kit <folder>` with the venv Python. No kit at all: the attendee
   pastes keys straight into `.env` (step 4).
4. A key still blank or rejected after the link is wrong in the kit too, and the kit's
   `.env` is the one place keys live: setup has opened it (this folder's `.env` only when
   no kit is linked). Tell the attendee which lines to fill and where each key comes from
   (AirROI https://www.airroi.com/api/developer/activate, Gemini
   https://aistudio.google.com/apikey, Hospitable my.hospitable.com → Apps → API access →
   Platform token, named `HOSPITABLE_API_KEY` in the kit; other PMSs leave it blank). Wait
   for "saved", then rerun the setup command from step 2: it copies the new value over and
   rechecks. Never paste a corrected value into this folder's `.env` while a kit is linked;
   the next link would overwrite it with the kit's value.
5. Other PMSs stage the documented JSON contract using their supported read tools. The
   bundled `hospitable_api.py` implements read-only Hospitable collection.
6. Tests do not verify live credentials; `check_keys.py` and the first pipeline run do.
7. Optional branding: `branding.example.json` to `branding.json` only if absent.
   Per-listing configuration: `config/properties.example.json` to `config/properties.json`.
   Preserve `.env`, `config/`, `branding.json`, `state/`, and `output/` through upgrades.

## Default workflow

```bash
.venv/bin/python scripts/run_pipeline.py --slug <SLUG> --date <DATE> --property-id <UUID>
```

For another PMS, stage `subject.json`, `images.json` and optional `reviews.json`,
`calendar.json`, `channels.json`, then omit `--property-id`. The contract is in the skill.
Never pass a non-Hospitable property ID to this flag.

Read only `digest.md`. Author `result.json`, then render with `--workdir` so photos,
comps, occupancy and cadence come from the files. Never transcribe those blocks manually.
`pipeline_status.json` identifies incomplete/failed stages and excludes stale output.
A failed subject stops the run before paid work; missing optional sections produce a
clearly labelled degraded report. Exit 0 can mean degraded: inspect the status summary.

## Cost controls and correctness

- Copy is written by `.claude/agents/listing-writer.md` (Read and Bash, plus Write on Windows; rules in its
  system prompt, three tool calls). It is generated: after editing SKILL.md or the writing
  references, run `scripts/build_writer_agent.py` (a test fails while it is stale).
- Reviews: 20 newest in one request. `--all-reviews` is opt-in. Report the sample scope.
- AirROI: one call for a fresh coordinate pool, a second address call only if it is empty;
  cache 14 days. Exclude the subject's Airbnb ID from its own competitor pool.
- Photos: five distinct images per Gemini request, two concurrent batches, bounded
  transient retries. Cache scores 120 days by full image URL, model and rubric version.
  `--batch-size 1` on `analyze_photos.py` enables individual scoring for comparison.
- `--no-cache` or `LO_NO_CACHE=1` bypasses paid caches. `--refresh` re-pulls Hospitable
  source files; paid steps still honor their own TTL caches. Derived outputs are rebuilt
  on every run, so changed inputs and limits cannot be hidden by an old output file.
- Report actual `comps.fetch.calls` and `photo_scores.usage`, including failed requests.
  A zero-cost warm run means cached data, not freshly verified provider data.
- `--skip photos,comps` avoids paid work. Ctrl-C stops the foreground pipeline; rerun the
  same command to resume. No scheduler is installed by this project.
- Never load raw response JSON into conversation. Keep prose bounded while preserving
  verified amenities, capacity and house rules in the digest.
- Photo scores are subjective. Banding reduces sensitivity but does not eliminate noise.
  Cover slots must have distinct beats. A short cover set exposes missing shots rather
  than filling it with duplicate scenes. Never use a map as a cover.
- Bump `RUBRIC_VERSION` whenever the model prompt/schema/scoring configuration changes.
- Calendar dates are unique. Missing/conflicting availability is unknown, never a booking.
- The renderer rejects missing/overlong copy and scans all outputs for pricing. It also
  displays incomplete photo coverage. Do not bypass those checks.
- History upserts on `(listing_slug, run_date)`, under a file lock. Cache updates are also
  locked for parallel portfolio runs. Malformed history lines are preserved.
- Lint with `.venv/bin/ruff check scripts tests` (config in `pyproject.toml`).
- Mark cadence only when the live listing was actually changed, never for a draft report.
