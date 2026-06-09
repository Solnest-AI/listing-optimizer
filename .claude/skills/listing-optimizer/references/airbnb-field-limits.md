# Airbnb Field Limits & Photo Specs — what the optimizer must respect

> Verified 2026-06 across Airbnb's official Resource Center/Help Center and four PMS channel
> managers that enforce these at sync time (Guesty, OwnerRez, SuperControl, RentalWise — a
> too-long title/summary makes their Airbnb sync FAIL). Re-verify yearly; Airbnb changes things.

## Text fields — HARD caps (sync fails beyond these)

| Field | Hard cap | Write to this budget | Why |
|---|---|---|---|
| **Title** | **50 chars** (incl. spaces) | **aim 32–45** | Mobile search truncates around ~32 chars; put the strongest hook first. (Note: Guesty counts title+nickname together against 50.) |
| **Summary / "Listing description"** | **500 chars** | ≤500, **front-load the first ~295** | The app shows only ~295 chars before "Show more" — the 4 ALE jobs must land above that fold. |

### Title style rules (Airbnb's official guidelines — violations can hurt ranking)
- No ALL CAPS (abbreviations like "YKA" are fine). Airbnb prefers sentence case.
- No emojis. Single special characters (·, /, comma) are fine; **repeated** ones (!!!, ***) are not.
- Don't waste characters on what search results already show (city, bed count, "New").

## Description sections (The space · Guest access · Interaction with guests · Neighborhood · Getting around · Other things to note)

- These are the API fields PMSs sync (Hostaway maps them as Summary/Space/Access/Interaction/
  Neighborhood_overview/Transit/Notes).
- **No hard API cap observed** — live listings sync "The Space" at 2,500–3,000 chars without
  error. However, hosts in Airbnb's NEW listing editor report a ~1,000-char box on "The Space"
  and ~500 on "Guest access" (conflicting reports; Airbnb publishes no number).
- **Practice:** put the most persuasive content (key features, seasonal draws, location
  drive-times) in the FIRST ~1,000 chars of "The Space" so nothing load-bearing is lost if a
  cap applies; total length 1,500–2,500 chars is proven safe via PMS sync.
- No phone numbers, emails, or URLs in any description field — breaks Airbnb sync/policy.

## Photos

| Spec | Value | Status |
|---|---|---|
| Max photos per listing | **100** | widely reported (Hospitable, iGMS, 10XBNB); no official article |
| Recommended count | 20–35 (Airbnb suggests ≥20; <10 hurts conversion) | guidance |
| Min resolution | **1024×683** | official ("Refresh your listing") |
| Recommended resolution | **≥1200×800**, larger is better | official ("How to take great photos") |
| Max file size | ~10 MB guidance (PMS channels can be stricter — Guesty caps 4 MB/photo) | official + PMS |
| Aspect ratio | **3:2 landscape** preferred (Airbnb crops to fit) | official + community |
| Format | JPEG (PNG accepted) | community/PMS |
| **Photo captions** | no official cap published — **write ≤250 chars** (unverified community norm; our ~2-sentence captions fit) | conservative budget |
| **Visual descriptions (alt text)** | **<250 chars**, objective contents-only (different from captions, which carry mood) | official (Help 3452) |

## Write-back gotcha — Airbnb "locked fields" (attribute locking)
If a host has edited title/summary/sections/amenities **directly on Airbnb**, Airbnb locks
those fields against PMS/API updates — a write-back will be silently ignored until the host
unlocks the field in their Airbnb PMS settings (per Hospitable's locked-fields doc). If a
Step-8 write-back "succeeds" but the re-read shows the old content, this is the likely cause —
tell the user to unlock the field on Airbnb or paste manually.

## Enforcement in this system
- The optimizer MUST count characters on the title (≤50) and summary (≤500) before rendering —
  state the counts in result.json (`summary_char_count`).
- Keep generated captions ≤250 chars each; lead "The Space" with its strongest 1,000 chars.
- Photo plan: respect that only ~the first 5 photos drive the click decision; the map photo
  belongs in the top 10; aim the final gallery at 20–35 shots.
