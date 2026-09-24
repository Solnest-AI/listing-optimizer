# MVP review and validation

Review date: 2026-09-20. Scope: the local Listing Optimizer skill, deterministic collection,
photo scoring, competitor logic, report rendering, caching and local history. Existing
uncommitted changes were retained and reviewed as the starting point.

## What changed

- Missing/invalid subjects stop before paid work. CLI arguments are validated early.
  A failed refresh invalidates its old artifacts across retries. Changed source data and
  scoring limits rebuild derived outputs instead of silently reusing old output files.
- Staged calendars do not invoke Hospitable. The reservation count is scoped to the
  calendar window, so it no longer undercounts upcoming stays.
- Photo scoring groups five distinct images into one request, with bounded payloads,
  bounded transient retries, permanent-error cancellation, validated scores and real usage
  counters. API keys use the request header. Duplicate URLs are scored once.
- Cached image identity retains query parameters. Changed rubric/model invalidates scores.
  Partial failures and photos excluded by a scoring limit are disclosed in reports.
- Cover recommendations preserve distinct scenes, including when adding a people shot.
  Thin galleries return fewer slots. A map-only gallery has no recommended cover order.
- Comp pools deduplicate numeric/string IDs, exclude the subject, strip monetary data
  before new cache writes, and reject malformed upstream response shapes. Address fallbacks
  have a separate cache identity so they do not contaminate another coordinate query.
- The digest bounds prose separately from property facts. Long descriptions no longer
  remove amenities, capacity, room details or house rules.
- Unknown/conflicting calendar days no longer become bookings. Duplicate dates count once,
  month keys retain their year, and unavailable occupancy displays as unknown.
- Reports reject blank and overlong copy, recompute summary length, disclose missing inputs
  and preserve the existing pricing guard. Human prose follows the no-em-dash rule.
- Report and history merges reject a work folder belonging to another listing or date.
  Failed/incomplete runs cannot enter history. Review-gap claims are limited to the
  account inventory and the retrieved review sample.
- Local history and cache updates are locked across concurrent processes. History upserts
  retain malformed lines and replace the same listing/date record safely.
- The skill distinguishes drafted copy from applied content. Cadence is marked only after
  the live listing changes.
- CLAUDE.md plus the skill shrank from 61,630 to 18,148 bytes, a 70.6% reduction in instruction
  text. This is a byte measurement, not a tokenizer measurement or bill estimate.

## Validation

The complete test suite passed: **154 tests** (149 after the 2026-09-21 MVP cut, which
removed the unused Supabase mirror and its tests, and added four regression tests). New regression tests were run against the
old behavior first and failed for the reproduced defects. Python compilation and
`git diff --check` also passed.

Live Gemini comparison using the same five existing listing photos:

| Measurement | Individual requests | One batch | Cached repeat |
|---|---:|---:|---:|
| Gemini requests | 5 | 1 | 0 |
| Image downloads | 5 | 5 | 0 |
| Reported input tokens | 4,000 | 1,852 | 0 requests |
| Reported output tokens | 731 | 709 | 0 requests |
| Reported total tokens | 4,731 | 2,561 | 0 requests |

The measured sample reduced requests 80% and total tokens 45.9%. Scene labels matched for
all five images. Average scores differed by up to 0.67 and the selected cover differed.
Batching is not proven quality-equivalent across all galleries. Photo recommendations
remain subjective, and cache reuse stabilizes repeat runs. For a fresh individual comparison,
use `analyze_photos.py --batch-size 1 --no-cache` with the same input.

A live read-only pipeline also succeeded against an existing Hospitable property:
property detail, 43-image gallery, 20 newest reviews, seven calendar days, one AirROI request,
24 comps after excluding the subject, five cached photo scores, cadence check and digest.
Channels and history retrieval were intentionally skipped in this smoke run. A repeat
made **zero AirROI requests, zero Gemini requests and zero image downloads**. Its digest
was 14,235 bytes versus 82,122 bytes across the four primary raw inputs (5.8 times smaller).

A clearly labelled validation fixture exercised the complete renderer with those machine
artifacts. All three deliverables were written, pricing checks passed, and both reports
explicitly disclosed that 38 gallery photos were not assessed. No PMS content was changed.
No live database write, history repair, publication or deployment was performed.

Implementation references: Google's [image input guidance](https://ai.google.dev/gemini-api/docs/image-understanding),
[structured output guidance](https://ai.google.dev/gemini-api/docs/structured-output) and
[thinking controls](https://ai.google.dev/gemini-api/docs/thinking). Actual acceptance of the
Gemini 2.5 Flash request shape was verified by the live calls above.

## Remaining limits

- Windows file locking is implemented but has not been exercised on a Windows machine.
- Non-Hospitable staged inputs are tested offline. Other PMS APIs and content write-back
  integrations have not been tested live. Hospitable remains paste-only.
- Live expired-key, quota exhaustion and provider outage conditions were simulated with
  HTTP transports. They were not deliberately triggered against production accounts.
- The photo comparison covers five images, not a broad quality benchmark. Large-image
  splitting, retries and missing data can change request counts.
- Cache reuse represents earlier observations. Use `--refresh` for PMS sources and
  `--no-cache` for paid data when fresh measurements are required.

## Stop and recovery

Ctrl-C stops a foreground run. No scheduler was installed. `--skip photos,comps` prevents
paid work. Rerun the same command after correcting a missing source or credential problem;
read `pipeline_status.json` before treating a degraded run as complete. Draft reports do
not change live listings.

Changes remain local for review. A rollback patch captures only this review's edits against
the source snapshot, preserving changes that already existed before the review. From the
repository root, this one command restores that snapshot:

```bash
git apply --reverse .git/listing-optimizer-mvp-review.patch
```

The reverse patch was checked without applying it. Later overlapping edits can make it
refuse to apply; inspect those conflicts instead of forcing it. Personal config, caches,
existing reports and history are outside the patch.
