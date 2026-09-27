# The lower description sections: Guest access, Other things to note, Neighborhood, Getting around

> Researched 2026-09-26: Airbnb Help Center and Resource Center, the page data of 12 live
> listings, 70 competitor listings (RankBreeze content joined to AirROI bookings), practitioner
> guides, Reddit host and guest threads, and GitHub listing tools. Re-verify yearly.

## Where each section lives (2026)

| result.json key | Airbnb editor field | Guests see | Where on the listing page |
|---|---|---|---|
| `the_space` | Description > Your property | The space | "About this space", behind Show more |
| `guest_access` | Description > Guest access | Guest access | About this space, after The space |
| `other_notes` | Description > Other details to note | Other things to note | About this space, last |
| `neighborhood` | Location > Neighborhood description | Neighborhood highlights | Under the "Where you'll be" map, no click needed |
| `getting_around` | Location > Getting around | Getting around | Only inside the map section's Show more |

- Interaction with guests is hidden from guests (gone from the listing page since late 2024;
  the editor now treats it as a preference). Do not write it.
- Plain text only. Bold and markdown are not rendered; asterisks show literally.
- Airbnb publishes no length cap for these fields. Longest live text measured: The space
  2,498, Other things to note 1,852, Neighborhood 954, Getting around 656. The widely
  repeated "1,000 / 500" limits trace back to an AI chatbot answer.
- Airbnb's AI writes "listing highlights" from reviews and, by observation, the host's own
  description (Airbnb has not said which fields it reads). Plain, specific facts are what it
  can reuse.

## What these sections do

No study, and none of our data (26 competitors with booking data), shows these sections move
bookings or ranking. Their job is to stop surprises. The recurring guest complaints are
undisclosed noise, shared space or people on site, parking, A/C, stairs and exaggerated
distances. Disclosure wins refund disputes but does not protect star ratings: guests rate the
experience, so the copy must prevent the surprise. Neighborhood is the most visible of the
four: every guest who scrolls to the map reads its first lines.

## Rules for all four

1. Facts come only from the digest: subject copy, live guest_access, house rules, amenities,
   OWNER NOTES, public reviews, photo captions. Never invent a place, distance, time, travel
   mode, entry method or parking detail. Keep the source's mode ("2-min walk", "5-min
   drive"); if the source gives none, do not add one.
2. Names and numbers beat adjectives: "2-min walk to the hospital", "parking for 2 on the
   driveway", "5 steps down". Cut "close to everything", "vibrant", "conveniently located".
3. Honest negatives: name it, put a number on it, give the upside or fix, say who it will not
   suit. No euphemisms ("lively" means state the hours). Never apologize twice.
4. A deal-breaker (stairs, shared space, people on site, noise, no A/C, limited parking)
   appears twice: in Guest access or The space, and again in Other things to note.
5. Host voice: "you" for what the guest gets, "we" for promises and personal picks. Warm and
   short. No hotel-speak, no ALL CAPS rule lists.
6. Voice of the customer: reuse how guests describe the place and the area in public reviews
   (paraphrase, or a short quote with "guests say"). Never private feedback.
7. One job per section. Do not repeat The space.
8. Never write door or lockbox codes, Wi-Fi names or passwords, the street address, phone,
   email or URLs, off-platform steering, prices, fees, deposits, minimum stays, or safety
   guarantees ("safe area", "guaranteed quiet"). The renderer rejects codes, addresses and
   contact details.
9. Gear places and moments to the chosen season and the hero guest named in the summary.
10. When a section needs a fact the digest lacks, write around the gap and add a plain
    question to `optimized.host_to_confirm` ("Who lives upstairs?", "Minutes to the
    airport?"). The report shows these to the host. Never fill a gap with a guess.

## Guest access (`guest_access`, 150 to 450 chars, 1 to 4 sentences)

The guest is deciding: what is mine, what is not, how do I get in, where do I park.
Cover what is known: whole place or private suite, anything shared or off-limits, the entry
method (self check-in, keypad, lockbox, only when the digest says so, never the code), the
route to the door, the parking count and spot, stairs. Top performers keep it literal and
short. A live guest_access that holds only a registration number is empty.

## Other things to note (`other_notes`, 150 to 600 chars, a short list)

The guest is deciding: is there a deal-breaker before I pay.
Lead with the most likely surprise, one fact per line starting with "• ". Use what the
digest supports: stairs, noise sources and hours, shared walls or people living on site,
pets on the property, cooling and heating specifics, bed types (sofa bed, cot), exterior
cameras or noise monitors (only when stated), check-in and check-out times, house rules with
a short reason when known, useful extras (crib, high chair). Top performers average about 160
characters of plain facts; long area guides here belong in Neighborhood. Never write fees:
if the listing charges add-ons, tell the host in the report that Airbnb requires them to be
described in the listing.

## Neighborhood (`neighborhood`, 300 to 700 chars)

The guest is deciding: will I like what is outside the door, and how far is the thing I came
for. Its first sentence shows under the map without a click, so it carries the section.
Order: (1) the main draw for the hero guest, with minutes and mode; (2) one honest line on
how the street feels, in guests' words; (3) three to five named places for this season and
this guest, with minutes when the digest has them; (4) one real caveat, if any. Personal
picks ("our go-to") beat lists, and a local-guide tone beats hotel language. At most five
places, no filler (ATMs, laundromats) unless the hero guest needs it.

## Getting around (`getting_around`, 150 to 450 chars)

The guest is deciding: do I need a car, where do I park, how far is the airport. It sits
behind Show more, so keep it practical: car needed or not, parking specifics (count,
driveway or street, EV or winter plug-in only when stated), verified walking distances,
transit and airport minutes only when the digest has them (otherwise ask in
`host_to_confirm`), seasonal road notes when stated.

## Flag what is wrong on the live listing (`listing_gaps`)

Every problem you spot on the live listing goes in the top-level `listing_gaps` list as
`{"issue": "...", "fix": "..."}`, one sentence each, so the host sees it in the report:
a deal-breaker guests mention in reviews that the listing never discloses (never quote
private feedback), an amenity box that contradicts OWNER NOTES or the description (a
"wood-burning" fireplace that is electric), live copy that contradicts the live guest_access
or the digest, a claim the evidence disproves. The renderer already flags a missing or vague
live Guest access, cameras or noise monitors the copy never discloses, and amenities the copy
names that are not ticked on Airbnb; do not repeat those. A DISCLOSURE REQUIRED line in the
digest means other_notes must disclose that item.

## Check before you finish

- Every place name, number and travel mode appears in the digest.
- Nothing contradicts OWNER NOTES or the live guest_access.
- Each deal-breaker appears twice.
- No codes, street address, contact details, prices or fees.
