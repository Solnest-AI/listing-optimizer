# ALE Framework — The Optimization Engine

> Source: STR Secrets "How to Create a $$$ Listing — Airbnb Algorithm Hack" (Mike Reilly).
> This is **the** engine. Photo eval + comp eval are **inputs that feed ALE**, not separate reports.
> StoryBrand SB7 (see `storybrand-sb7-rubric.md`) shapes *how* the ALE copy is written.

ALE = **A**menities, **L**ocation, **E**xperiences — the 3 things to CAPTURE, then DEPLOY into 5 channels, then PROVE with funnel metrics.

---

## Layer 1 — ALE: the 3 things to CAPTURE

### A — Amenities
Not just hot tubs/saunas. The win is the **low-cost / high-value items most hosts hide**: free weights, yoga mat, beach/ski gear, stocked kitchen, dish pods, gifted local coffee. *Don't make the guest pack it.*
- **Seasons count as amenities** (winter ski / summer bike-golf-hike).
- **Views count as amenities.**
- Score: are ALL real amenities surfaced in copy + captions, or buried in the checkbox list only?

### L — Location
Two parts:
1. **The setting itself** (ski-in/out, lakefront, walkable village).
2. **What's nearby, with drive-times** (lake, hiking, restaurants, arena, hospital, airport).
- **Unlock = a map photo with pins + minutes-to-each.** If there's no map photo, that's a gap.
- Score: are drive-times concrete (minutes), and is there a map asset in the top 10 photos?

### E — Experiences
The emotional payload — firepit s'mores, hot-tub soak under string lights, kid's first fish, après-ski arcade night, date night.
- **Stage them.** Put **people in ≥1 of the top 5 photos.**
- Score: do photos + copy sell *moments*, or just rooms? Is there a human in the top 5?

---

## Layer 2 — the 5 channels you DEPLOY ALE into

1. **Photos** — pro, staged, all-seasons; photoshop black TVs; **map photo in top 10**; **person in 1 of top 5**; hero shot leads.
2. **Copy** — the **~500-char Summary does 4 jobs in order**: (1) who it's for → (2) why this place → (3) distance to attractions → (4) invite to book. **"The Space" = SEO keyword-load**, bullets first, scannable.
3. **Captions** — caption **every** photo (never one-word). ~2 sentences on the **top 25–30** for SEO surface area.
4. **Reviews** — surface the **best review quotes** inside photos + copy; **respond to every review** (Airbnb reads "active host" → ranking boost). Flag unanswered reviews.
5. **Expand reach** — master Airbnb first, then VRBO + direct; the same system lifts all channels.

---

## Layer 3 — the funnel metrics that PROVE it (READ-ONLY diagnostic)

| Metric | Healthy range | If leaking → pull this lever |
|---|---|---|
| 1st-page impression rate | 50–60% | cover/hero photo, top 5, review score, amenities, flexible check-in |
| Search → listing (click) | 12–18% | "above the fold": cover photo, reviews, title |
| Listing → booking | 1–2.5% | top 5 photos, summary + "The Space"; "below the fold": amenity checkboxes, reviews, policies |
| Overall conversion | 0.1–0.5% | composite |

- Clicks low = **above-the-fold** problem. Failed bookings after click = **below-the-fold** problem.
- **⚠️ ZERO PRICING.** The source names price as a lever; this tool does NOT. Never read, recommend, or surface price / ADR / min-stay. If occupancy looks unusual, note it as a *read-only* observation only — never tie it to a price action.

---

## Cadence (drives the 2–3 week refresh)

**Authoritative intervals live in ONE place: `scripts/cadence.py` (the `CADENCE` table).** Run `cadence.py --check` for what's due — don't restate the day counts here (they'd drift). Rough shape: title ~monthly · photo rotation ~biweekly · captions & "The Space" every 1–4 months · seasonal swap quarterly · full reshoot ~3 years · conversion audit monthly.

---

## How the optimizer GRADES a listing

For the subject, produce an **ALE scorecard** (0–5 each) with concrete gaps and fixes:

| Dimension | What to check | Gap → Fix |
|---|---|---|
| **A — Amenities surfaced** | every real amenity in copy/captions, not just checkboxes; seasons + views called out | list missing surfacings |
| **L — Location specifics** | concrete drive-times (minutes); map photo w/ pins in top 10 | name what's missing |
| **E — Experiences staged** | moments sold in photos+copy; person in top 5; hero leads | name flat/room-only shots |
| **Photos channel** | hero strength, top-5 order, map in top 10, captioned, all-seasons | from photo rubric |
| **Copy channel** | Summary does its 4 jobs in order; "The Space" keyword-loaded + bullets-first | rewrite |
| **Captions channel** | every photo captioned; top 25–30 ~2 sentences; SEO terms | flag blanks/one-word |
| **Reviews channel** | best quotes used in copy; all reviews responded | flag unanswered + unused quotes |

Then ground every recommendation in: (a) the **comp gaps** (what top performers do that we don't) and (b) the **photo findings**. Output paste-ready new **title**, **500-char summary**, **"The Space"**, and **per-photo captions**.
