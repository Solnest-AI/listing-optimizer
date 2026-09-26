# Photo Rubric . scoring + hero/top-5 ordering

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
- **subject_kind** . the DOMINANT beat, from a **closed list** (this is the dedupe key, so it must
  never be free text): `hot_tub, pool, sauna_cold_plunge, fire_pit, game_room_arcade, home_theater,
  living_room, kitchen_dining, primary_bedroom, bedroom, bathroom, workspace, exterior,
  deck_patio_yard, view_scenery, location_map, neighbourhood_area, amenity_detail, kids_pet_family,
  food_drink_staging, collage_multi, other`
- **subject** . free-text label, for the caption writer only (e.g. "hot tub at dusk with mountains")
- **season** (winter / summer / shoulder / interior)
- **has_people?** (true/false)
- **is_map?** (true/false)
- **flags:** `reshoot` (technically bad), `restage` (good room, bad styling . black TV, clutter, cords).

---

## Hero + top-5 selection rules (ALE-driven)

1. **Hero (cover):** strongest single shot that instantly says what's special . usually the signature amenity or the view (hot tub w/ mountains, ski-in/out, the arcade). Must be sharp, bright, emotionally pulling.
   Enforced in code (`NOT_COVER`): never a collage, map, neighbourhood/street scene, bathroom
   or close-up detail, and never a reshoot-flagged shot. Collages are also kept out of the top 5.
   When the current cover (lowest gallery order) breaks this, the gaps say so and name the swap.
   Ties inside a score band go to the higher `ale_fit + emotion`, then gallery order, so a tie
   does not just echo the host's current order back as a recommendation.
2. **People in ≥1 of the top 5** (Experiences). If no good people shot exists → flag to stage one.
3. **Top 5 MUST cover five distinct `subject_kind` beats**, not 5 of the same room: e.g. hero amenity
   → experience-with-people → key living space → bedroom → view/location. This is enforced in code
   against the closed enum, because free-text labels do not dedupe: a shipped 2026-06-08 run put
   "arcade" and "arcade room" plus "hot tub" and "hot tub with mountain view" in the same five-photo
   cover set. If the gallery has fewer than five distinct beats, that is a **content gap** . say so
   and name the beats to shoot. Return fewer than five slots instead of repeating scenes.
4. **Map photo with pins + drive-times belongs in the top 10** (Location). If missing → flag to create one.
5. **All-seasons represented** for seasonal properties: don't show only snow in summer.
6. **De-prioritize:** duplicates, weak/dark shots, anything with black TVs or visible cords until restaged.
7. **Cover-set composition is enforced in code:** at most one off-property or pure-scenery beat
   (`neighbourhood_area`, `view_scenery`), and a bedroom slot whenever the gallery has a usable
   bedroom shot (never evicting the hero or the only people shot). If the model filed a
   bedside detail as `bedroom`, override the slot with the real bed shot and say why.
8. **Duplicates are detected in code** (`photo_dupes.py`, whole gallery, thumbnails only) and
   listed in the gaps as "#B repeats #A". The same room re-shot with people in it is NOT a
   duplicate; it is the Experiences shot. Never tell a host to delete a people version.

---

## Score precision . do not over-read it

Measured on identical input, the model's per-photo average drifts by ~0.2 (max 0.66) on the 0–5
scale, and neither `temperature: 0` nor an explicit seed removes it. Ranking is therefore done on
the average **banded to 0.5**, with ties broken by gallery order so a given set of scores always
produces the same order. **Never tell the user photo A beats photo B on a sub-band gap** . 4.17 vs
4.0 is noise, not a finding. Run-to-run stability comes from the score cache, which pins a scored
photo for 120 days (a replaced photo gets a new URL, so it re-scores automatically).

A photo that fails scoring is **absent from the ranking** and cannot win the hero slot, so
`coverage_note` must be reported: "ranked N of M photos", never an implied full sweep.

---

## Output shape (per run)

```json
{
  "photos": [
    {"order": 0, "scored": true, "subject_kind": "hot_tub", "url": "...", "subject": "hot tub", "season": "winter",
     "has_people": false, "is_map": false,
     "technical":5,"lighting":5,"staging":4,"composition":5,"emotion":5,"ale_fit":5,
     "avg": 4.8, "flags": [], "caption_note": "lead with the soak-with-a-view moment"}
  ],
  "recommended_top5_order": [0, 6, 12, 23, 14],
  "hero": 0,
  "gaps": ["no map photo with drive-times", "no person in top 5 . stage an après/hot-tub moment"],
  "reshoot": [11, 20],
  "restage": [9]
}
```

The optimizer then writes captions for the recommended order and feeds the gaps into the ALE scorecard.
