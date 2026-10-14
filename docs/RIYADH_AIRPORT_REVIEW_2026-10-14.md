# Riyadh airport intelligence review — 14 October 2026

Record: `sa-riyadh-airport-attack-2026-10-10`

## Decision
Retain in INTERNAL_REVIEW. A qualified operational summary is evidentially supportable, but this change does not approve publication or claim legal clearance. CONFIRMED/HIGH is scoped to the incident and disruption, not every claim. Preserve conflictingInformation=true, sensitiveAllegation=true and REVIEW_RECOMMENDED.

## Evidence and limitations
- FCDO: current 14 October, updated 13 October; avoid RUH until secure, essential-travel restrictions for Riyadh province and Saudi airports except Medina. https://www.gov.uk/foreign-travel-advice/saudi-arabia
- US State Department: 12 October Level 3; national commercial-flight availability does not establish RUH reopening. https://travel.state.gov/en/international-travel/travel-advisories/saudi-arabia.html
- FAA A0056/26: direct PDF checked; OEJD caution for covered US operators, military activity/GNSS interference, effective 11 October 21:00 UTC to 10 December 05:00 UTC. This is not a Saudi airport closure NOTAM. https://www.faa.gov/air_traffic/publications/us_restrictions/kicz_advisory_notam_a0056_26_oejd.pdf
- Reuters operational reporting updated 13 October supports continued closure. Airline roundup reports a closure notice through 17 October, but TI did not retrieve that underlying Saudi NOTAM. Both Reuters articles belong to the same reporting chain as the original Reuters syndication, not two new independent sources.
- Official SPA release found through indexed text: https://www.spa.gov.sa/en/N2699133 reports GACA extension for safety/operational checks. Full release body unavailable; GACA direct retrieval timed out. No full-body verification claimed.
- KKIA homepage/flight-board page found, but no current flight-status dataset or dated reopening clearance retrieved. Generic page existence proves nothing about operation.
- The original AP, Reuters-syndicated and NBC observations remain unchanged. They were not all re-retrieved in this review and their casualty/follow-on claims are omitted from the focused candidate.

## Publication requirements
1. Record editorial/legal disposition of the focused public text. Repository rules expose only PUBLISHED events; the current frontend does not independently enforce REVIEW_RECOMMENDED. Do not flip publicationStatus to bypass unresolved review.
2. Reconcile DRONE_ATTACK_ON_INFRASTRUCTURE metadata with missile/projectile reporting using the actual source wording. Avoid declaring an identified perpetrator or weapon type without verified evidence.
3. If publishing an exact closure period, obtain the current Saudi AIS/NOTAM identifier, scope, effective/expiry times and replacements/cancellations. Otherwise keep 17 October explicitly Reuters-reported and never a guaranteed reopening.
4. Independently verify any casualty, nationality, 11 October follow-on or Dammam details before restoring those claims to public copy.
5. Recheck official aviation and advisories immediately before publication. Do not interpret a scheduled expiry or airline booking availability as safe reopening.

## Lifecycle
HOT_TOPIC: 10 October is beyond the 1–3 day Current window and disruption continues. Review on 17 October, earlier if official conditions change. This date is a review checkpoint, not a reopening promise. Publication remains separately gated. After continuing relevance ends, retain the country/category archive for 6–12 months and database archive for seven years; no record deletion is introduced. Production main currently has no Hot Topic lifecycle renderer; PR #18 supplies that separate implementation. This change stores the lifecycle classification and does not duplicate or weaken that work.

## Validation
Existing schema validator passes: 52 countries, 380 records, 164 sources, 14 locations, 23 events, 105 observations.
Preservation checks: all unrelated events, all original observations, countries, records, locations, change_log and oilGasSummary unchanged. Five observations added, four source entries added; two new Reuters observations share reuters-wire. IDs unique; INTERNAL_REVIEW remains excluded by the existing public predicate. No UI, workflows, publication controls or sibling repositories changed.

Reconcile the focused record changes against any intervening refresh and PR #17/#18 before merge. No deployment or publication performed.
