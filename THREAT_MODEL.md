# Threat model: bogus, mistaken and manipulated reports

Floodline ranks where responders are needed. Anything that can move that ranking is an attack surface. The
design rule that limits all of them:

> **One report can send someone to look. It cannot send a boat.** Neither can several that agree, where the
> satellite has looked and seen no water.

A field report's depth or people claim raises *rescue* priority only when something independent backs it: a
second device agreeing within one depth class, satellite water in the hexagon or a neighbour, or the model's
own assessment of the photo. Otherwise it lands on the coordinator's verification list (`needs_verification`,
`verify_score` in the fused hexes) and the ranking stays where the independent layers put it. Satellite extent,
road access and building exposure carry the ranking. Reports refine it.

Each row names the code or test that holds the line.

| Threat | Mitigation | Evidence | Residual risk |
|---|---|---|---|
| A stranger with the public anon key floods the database | The anon key alone can write nothing. Only a phone that redeemed a coordinator's invite can insert a report or upload a photo, for its own team; invites, devices and team codes are unreadable to every phone | `*_device_enrolment.sql`; `app/tests/sql.test.ts` ("the anon key alone can do nothing", "a signed-in phone that has not redeemed an invite cannot report"); live check, 16/16 | A stolen phone reports as its owner until the coordinator withdraws it |
| One person poses as many devices (Sybil) to fake corroboration or dodge per-device limits | One single-use invite per phone, each with a label saying whom it was given to; the device id is the phone's server-signed Supabase identity, stamped by the database, never the value the phone sends | `*_device_enrolment.sql`; `sql.test.ts` ("an invite works once", "stamps the server's device id", "inventing device ids does not get round it"); `floodline invites` | N identities need N invites, all issued and labelled by a coordinator; a dishonest insider is bounded by the invites they were given |
| A device turns bad after it was enrolled | `floodline devices --revoke` stops it at once (row-level security) and `floodline ingest` rejects all its reports, past ones included (`device_withdrawn`) | `sql.test.ts` ("a revoked phone can no longer report"); `tests/test_ingest.py` ("withdrawing a device rejects its reports including past ones") | Reports already used before the withdrawal were used; the next fuse run drops them |
| A device or team spams reports | Database trigger: 30 reports per device per 10 min, 300 per team per minute, answered as HTTP 429 | `*_report_limits.sql`; `sql.test.ts` ("rate-limits one device to 30 reports in 10 minutes") | Per device now means per invite: inventing device ids no longer opens new budgets |
| Photos uploaded without a report fill storage | Private bucket, JPEG only, 5 MB cap, `<uuid>.jpg` names only; orphans pruned after 24 h | `sql.test.ts` (bucket tests); `ingest --prune-orphans`; `tests/test_ingest.py` | Storage can fill for up to a day; Supabase quotas apply |
| A web photo or an old published flood photo is passed off as a report | Perceptual hash against 11,563 images from CVFD and European Flood 2013: within 2 bits rejected, 3-6 bits sent to review | `stages/trust.py`; `results/trust_eval.json`: 99.8 % of forwarded copies caught; 0.7 % of unrelated photos fall in the review band | A heavily cropped copy (10 % crop: 22 % caught) passes this check, but still cannot move priority on its own |
| One photo sent twice, or forwarded between team members | Near-duplicate of an earlier report: same device rejected (`duplicate_resend`), another device reviewed (`forwarded_photo`) | `tests/test_trust.py` | A forwarded photo with a new crop counts as a new photo; the second device still has to agree on depth to corroborate |
| One phone reports the same place over and over to inflate it | Each device counts once per hexagon (its deepest claim); people are the maximum, not the sum; more than 10 reports in 10 min from one device in one hexagon go to review | `fuse.aggregate`; `tests/test_location_and_fuse.py` ("one device cannot corroborate itself"); `test_trust.py` (bursts) | None for ranking: one device never corroborates itself |
| Old photos from a past flood | Server refuses capture times more than 30 days old or in the future (HTTP 422); trust sends anything over 72 h old to review | `*_report_limits.sql`; `sql.test.ts`; `test_trust.py` ("timestamps") | A camera with a wrong clock produces honest reports that need review |
| A gallery photo given the phone's current position | EXIF GPS first; the phone's fix is used only for a photo taken within 10 min of it; otherwise no location | `app/src/location.ts`; `app/tests/location.test.ts` | EXIF can be edited before upload; a forged location in a dry area still needs corroboration |
| Spoofed GPS (mock-location apps) or colluding devices | Location confidence from the reported accuracy; device agreement never overrules a satellite map that says dry | `location.accuracy_confidence`; `fuse.score`; `results/attack_prague.json`: 10 colluding pairs in mapped-dry hexagons left the top ten unchanged | Where the satellite has not mapped at all, two colluding enrolled devices still corroborate each other; both are traceable to their invites and revocable |
| A depth claim the satellite contradicts | Not discarded (radar misses water between buildings), but neither one report nor several agreeing ones can raise priority; only the model's reading of the photo can. Flagged for verification | `fuse.score`; `attack_prague.json` (50 lone fakes: top ten unchanged, all 50 flagged) | A true report in a mapped-dry street waits for the model assessment or a responder's check |
| A fabricated depth inside water the satellite did map | The depth counts (water is confirmed there); people claims in it still need a second source | `attack_prague.json`: 50 such reports displaced on average 1 of the top 10 | Depth exaggeration inside confirmed water moves the ranking a little |
| Exaggerated people counts | People count only when the model counted them in the photo or two devices report people; satellite water never confirms people; once per hexagon (maximum) | `fuse.score`, `fuse.aggregate`; `test_location_and_fuse.py` ("satellite water confirms depth but never people") | Under-counting when separate groups are photographed in one hexagon |
| Photos of identifiable people leak | Photos never leave the private bucket and `data/field/` (git-ignored); the public map shows hexagon aggregates only; the committed run manifests hold counts | `.gitignore`; `tests/test_ingest.py` (manifest holds no coordinates or notes) | Coordinators see raw photos; face blurring before any wider sharing is not built yet |
| The service-role key leaks | It exists only in the analysis machine's environment, never in the app or the repo | `ingest.SupabaseStore.from_env` | A compromised analysis machine exposes all reports; rotate the key |

## What the model-assessment step adds later

Once SAM 3 and the vision-language model have assessed a photo, `depth_assessed` corroborates it, which is
the strongest single check, because it reads the photo itself rather than trusting the tag. Until then, a
lone report's tag is a request for verification, not evidence.

## Known gaps, stated rather than hidden

- No coordinator interface yet for working through the verification list; it is a CSV column today.
- Background upload from a closed app is not implemented (iOS Safari does not support Background Sync).
- The CVFD release itself contains European Flood 2013 photos (2,371 of 3,710 within 2 bits); see
  `data/DATASET_CARD.md`. That does not affect the zero-shot pipeline, which trains on nothing.
