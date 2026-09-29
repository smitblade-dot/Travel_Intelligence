# Independent EMERGENCY batch review

Verdict: PASS for the 15 staged source-backed additions, pending integration and PR/release QA. This is not acceptance of production deployment, the entire EMERGENCY category, or the full launch.

Reviewed output/recovery/baseline/EMERGENCY.json on 29 September 2026. Independently opened all 15 exact GOV.UK getting-help pages and checked every telephone number and material content claim. All match the retrieved source. Independently validated IDs, source references, one-target observations, independence group, matching extracted claims, country/category, active state and absence of collisions with origin/main 854392c. The additions fill 15 previously empty country/category combinations. Production remains 171/728 until merged and deployed; candidate would be 186/728 (25.5%).

Country/source paths checked under https://www.gov.uk/foreign-travel-advice/: bahrain, congo, cote-d-ivoire, ecuador, gabon, equatorial-guinea, guyana, israel, mauritania, sudan, suriname, south-sudan, syria, chad, yemen, each ending /getting-help.

Particular caveats verified:
- Syria: source states limited local services and unavailable in-country British consular support. Batch restricts London consular number to British nationals, expressly distinguishes it from emergency dispatch, and invents no local number.
- South Sudan: source says no central emergency numbers. Batch recommends contacting provider/insurer to establish possible assistance without guaranteeing coverage or response. Optional enrichment: source also warns that insurance may be invalidated by travel against FCDO advice; include in the future INSURANCE/SECURITY record and link to current advisory.
- Gabon, Equatorial Guinea, Congo, Mauritania, Sudan and Yemen: specific language, geographic access or response limitations retained.
- Israel source covers Israel and Palestine; current record is scoped to tracked Israel country ID and does not assert verified coverage across other jurisdictions.

These are concise useful baseline emergency contacts/access guidance consistent with minimumContent; no fabricated local number is needed where the authoritative source expressly provides none. All observations share uk-fcdo, correctly avoiding false independent corroboration. Claims are factual summaries with conservative automationPermission UNKNOWN. Existing 37 country EMERGENCY records were not reverified in this review.

Integration must preserve source/observation attribution and uncertainty, run validators against the final current-main candidate, and retain all strict release gates and independent QA. No phone connection/response-time field tests were performed or claimed.
