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

1. Check the current folder and known install before cloning. Existing `.git`: inspect
   status, preserve local edits, then `git pull --ff-only`. Never clone a second copy.
   ZIP install: preserve local config/state and back up shipped files before converting
   in place to git. Never remove the old folder as an update strategy.
2. Use Python 3.10+. Create `.venv` if absent and install
   `requirements.txt` plus `requirements-dev.txt`. Reinstall dependencies after updates.
   Mac/Linux: `.venv/bin/python`; Windows: `.venv\Scripts\python`.
3. Create `.env` from `.env.example` only if absent. Configure AirROI and Gemini using
   the vendor account pages: https://www.airroi.com/api/developer/activate and
   https://aistudio.google.com/apikey. Verify the current account UI before giving click paths.
4. For Hospitable, use the existing platform token as `HOSPITABLE_TOKEN` (alias:
   `HOSPITABLE_API_KEY`). The bundled `hospitable_api.py` implements read-only collection.
   Other PMSs stage the documented JSON contract using their supported read tools.
5. Run `.venv/bin/python -m pytest -q`. Tests do not verify live credentials. Verify
   configured providers with a small read-only request, saving payloads to files.
6. Optional branding: `branding.example.json` to `branding.json` only if absent.
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
