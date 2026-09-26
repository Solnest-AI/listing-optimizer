# Listing Optimizer

A local Claude Code skill for short-term-rental listing audits and draft improvements.
It combines the ALE framework (Amenities, Location, Experiences), StoryBrand copy,
competitor evidence and photo scoring. Outputs are an HTML report, Markdown report
and paste-ready title, summary, description and captions.

Hospitable collection is built in. Other PMSs can supply the same staged JSON contract
through their read tools. This MVP does not ship a hosted UI or automatic PMS publishing.
It never writes pricing, calendars, availability, fees or stay restrictions.

## Start here

Open Claude Code in your existing Listing Optimizer folder and say:

> Review the setup instructions in CLAUDE.md, configure any missing dependencies,
> and run the tests. Preserve my existing keys, settings, history and reports.

Then ask **"Optimize my [listing] for [season]."** The agent discovers your properties,
collects evidence, writes the copy and renders the reports. It asks only for missing
information. The agent's reasoning runs in your Claude Code session; there is no separate
text-generation API key required by these scripts.

New install:

```bash
git clone https://github.com/Solnest-AI/listing-optimizer.git
cd listing-optimizer
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

Use Python 3.10+. On Windows, create the environment with `py -m venv .venv` and use
`.venv\Scripts\python` / `.venv\Scripts\pip` in the commands.

Create `.env` from `.env.example` **only if it does not already exist**, then configure:

| Configuration | Purpose |
|---|---|
| `HOSPITABLE_TOKEN` | Read your property content, photos, reviews and availability. `HOSPITABLE_API_KEY` is also accepted. |
| `AIRROI_API_KEY` | Competitor data. Obtain through [AirROI developer access](https://www.airroi.com/api/developer/activate). |
| `GEMINI_API_KEY` | Photo scoring. Obtain through [Google AI Studio](https://aistudio.google.com/apikey). Access and quotas depend on your account. |

API keys and personal files are gitignored. Do not paste them into reports or commit them.
Optional: copy `branding.example.json` to `branding.json` and
`config/properties.example.json` to `config/properties.json`, preserving existing files.
RankBreeze is optional and requires its separately connected MCP tools.

## One-command gathering

```bash
.venv/bin/python scripts/run_pipeline.py --slug my-cabin --date 2026-09-20 --property-id HOSPITABLE_UUID
```

Use the actual run date. The command creates `output/<date>/<slug>/digest.md` and
`pipeline_status.json`. The agent reads that compact digest, writes `result.json`, and runs:

```bash
.venv/bin/python scripts/render_report.py --data output/<date>/<slug>/result.json --workdir output/<date>/<slug> --listing-slug <slug> --date <date>
```

Reports land in `~/Desktop/Listing Optimizer/<slug>/<date>/`. The renderer validates copy
lengths, blocks pricing content and shows missing data and incomplete photo coverage.
It merges measured facts directly from files so the agent does not copy them by hand.

For another PMS, stage `subject.json`, `images.json` and optional reviews/calendar files
in the working directory, then omit `--property-id`. The exact contract is in
[the skill](.claude/skills/listing-optimizer/SKILL.md). A non-Hospitable ID must never be
passed to the Hospitable flag. An Airbnb URL alone has no bundled importer in this MVP.

## Requests and token usage

- Reviews default to the newest 20 in one request. Aggregates are labelled with their
  actual window. `--all-reviews` is available when a lifetime calculation is needed.
- AirROI makes one request for a fresh coordinate pool. Only an empty pool can trigger
  a second address lookup. Pools cache for 14 days; subject listings are excluded.
- Gemini scores five distinct images per request, with two batches at a time. Scores
  cache for 120 days by URL, model and rubric version. Changed query parameters are part
  of image identity. Thirty small uncached photos normally need six scoring requests;
  large-image splits and transient retries can add requests, which are counted.
- Gemini outputs record actual requests, downloads and returned token usage. An expired
  key or missing model cancels remaining queued batches. Each transient failure has at
  most three attempts; permanent failures are not repeatedly billed.
- The agent reads one digest and writes only copy and reasoning. Shared instructions
  avoid repeating historical research and debugging stories on every run.

A live five-photo comparison used **1 request / 2,561 tokens** in a batch versus
**5 requests / 4,731 tokens** individually. A cached repeat made **0 requests and 0 image
downloads**. This is one measured sample, not a guarantee for every gallery. Scene labels
matched, but scores and the selected cover differed. Treat vision scores as recommendations,
not objective measurements. `analyze_photos.py --batch-size 1 --no-cache` enables a fresh individual comparison.

## Refresh, recovery and stopping

| Option | Behavior |
|---|---|
| `--refresh` | Re-fetch Hospitable source data. Paid caches still apply. |
| `--no-cache` or `LO_NO_CACHE=1` | Bypass paid caches even if old output files exist. |
| `--photo-limit N` | Score 1..100 gallery photos; default 60. |
| `--review-limit N` | Pull 1..50 recent reviews; default 20. |
| `--skip photos,comps` | Avoid paid scoring and comp lookup. |

A missing/invalid subject stops the run before paid work. Optional failures are labelled
as degraded; exit 0 can still have missing sections. The status file excludes old failed
artifacts so they cannot silently reenter the report. A failed refresh stays invalid until
the source is fetched successfully or restaged. Rerun the same command to recover.

Ctrl-C stops a foreground run. There is no background scheduler. Cached successful results
are reusable. Generated reports and draft copy do not change the live listing.

## History and application

`memory.py record` saves a compact, price-free local history in `state/history.jsonl`.
Same-listing/same-date runs replace that record. File locks protect history and cache
updates when portfolio runs finish together. Draft reports are not marked as applied;
cadence changes only after the live listing actually changes. History is per machine
and never leaves the folder.

Hospitable uses the paste block. Other PMS content updates require a verified supported
API, approval of the exact copy, a before-snapshot, a content-only request and a confirming
read. Those write integrations are not bundled or end-to-end tested here.

## Updating

Reuse the existing installation. Inspect local changes before updating:

```bash
git status --short
git pull --ff-only
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Preserve any local code changes if git reports a conflict. ZIP users should ask the agent
to convert the existing folder to git in place after backing it up. Do not clone a second
copy or delete the original. Preserve `.env`, all personal `config/` files, `branding.json`,
`state/` and `output/`. Gitignore is not a substitute for a backup.

## Verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check scripts tests
```

Tests cover pricing guards, copy validation, retries, real request construction against
mock HTTP transports, cache reuse, concurrency, pipeline recovery, occupancy and report
assembly. Unit tests alone do not establish live provider access. See
[the MVP review](docs/MVP-REVIEW.md) for the measured validation and remaining limits.

MIT. See [LICENSE](LICENSE).
