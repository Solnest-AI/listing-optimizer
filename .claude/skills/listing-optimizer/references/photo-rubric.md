# Photo Rubric — scoring + hero/top-5 ordering

> Photos are **ALE channel #1** and the single biggest lever on click + book rate.
> This rubric is what `analyze_photos.py` (Gemini vision) scores against, and what the optimizer uses to recommend a **top-5 order** and **reshoot/restage flags**.
>
> ⚠️ **Honest scope limit:** the model scores **quality + ALE-fit**, it does NOT predict literal conversion lift (Airbnb exposes no per-photo performance data). This is a quality/ALE ranking, not a conversion oracle.
> ⚠️ **No pricing.** Never reference price/value-for-money in photo analysis.

---

## Per-photo scores (0–5 each)

| Criterion | 0–1 | 3 | 5 |
|---|---|---|---|
| **Technical quality** | blurry, dark, crooked, phone-snap | decent exposure | pro: sharp, level, well-lit, wide |
| **Lighting** | flat/harsh/yellow | even | warm golden-hour / bright airy |
| **Staging** | cluttered, empty, black TVs, cords | tidy | styled: textures, drinks, throws, fire lit |
| **Composition** | awkward crop, dead space | centered | leading lines, depth, rule-of-thirds |
| **Emotional pull (E)** | empty room | pleasant | sells a *moment* you want to be in |
| **ALE fit** | generic | shows one ALE element | clearly sells Amenity / Location / Experience |

Also tag each photo:
- **room/subject** (e.g., hot tub, arcade, primary bedroom, exterior, map)
- **season** (winter / summer / shoulder / interior-neutral)
- **has_people?** (true/false)
- **is_map?** (true/false)
- **flags:** `reshoot` (technically bad), `restage` (good room, bad styling — black TV, clutter, cords), `dehero` (currently high but weak).

---

## Hero + top-5 selection rules (ALE-driven)

1. **Hero (cover):** strongest single shot that instantly says what's special — usually the signature amenity or the view (hot tub w/ mountains, ski-in/out, the arcade). Must be sharp, bright, emotionally pulling.
2. **People in ≥1 of the top 5** (Experiences). If no good people shot exists → flag to stage one.
3. **Top 5 should cover distinct ALE beats**, not 5 of the same room: e.g., hero amenity → experience-with-people → key living space → bedroom → view/location.
4. **Map photo with pins + drive-times belongs in the top 10** (Location). If missing → flag to create one.
5. **All-seasons represented** for seasonal properties: don't show only snow in summer.
6. **De-prioritize:** duplicates, weak/dark shots, anything with black TVs or visible cords until restaged.

---

## Output shape (per run)

```json
{
  "photos": [
    {"order_current": 0, "url": "...", "subject": "hot tub", "season": "winter",
     "has_people": false, "is_map": false,
     "scores": {"technical":5,"lighting":5,"staging":4,"composition":5,"emotion":5,"ale_fit":5},
     "avg": 4.8, "flags": [], "caption_note": "lead with the soak-with-a-view moment"}
  ],
  "recommended_top5_order": [0, 6, 12, 23, 14],
  "hero": 0,
  "gaps": ["no map photo with drive-times", "no person in top 5 — stage an après/hot-tub moment"],
  "reshoot": [11, 20],
  "restage": [9]
}
```

The optimizer then writes captions for the recommended order and feeds the gaps into the ALE scorecard.
