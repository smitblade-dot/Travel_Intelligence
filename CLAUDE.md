# Travel Intelligence — repository operating rules

This repository is the standalone Travel Intelligence (TI) product. It is a
static GitHub Pages application backed by `data.json`.

**Repository scope:** only `smitblade-dot/Travel_Intelligence` may be
modified for TI work. The sibling Crude Flow Intelligence and Gas & LNG
Intelligence repositories are read-only upstream signal sources.

## Canonical data model

`data.json` uses schema version 2.0 with these top-level collections:

`meta`, `countries`, `records`, `sources`, `locations`,
`events`, `sourceObservations`, `change_log`, `oilGasSummary`.

### Baseline intelligence

`records` contains relatively stable travel intelligence. Existing records
are **grandfathered** and retain their `sourceId`; do not mass-create
historical observations merely to make the counts match another system.

The underlying 14 category keys remain distinct:

SECURITY, ENTRY, HEALTH, EMERGENCY, TRANSPORT, ENVIRONMENT, INSURANCE,
LAWS_CULTURE, COMMUNICATIONS, FINANCE, LANGUAGE, ACCOMMODATION, EQUIPMENT,
TRAINING.

The UI may group these into fewer display groups, but the stored category
keys must not be deleted or merged. Empty categories remain visible with a
zero count.

### Current country intelligence must also refresh

The country pages are a live operational layer, not a static archive. The
daily refresh must inspect the existing country records for all tracked
countries and update them when a current, verified source shows a material
change to traveller-relevant entry, security, health, emergency, transport,
environment, insurance, laws/culture, communications, finance, language,
accommodation, equipment or training information.

For country records:

- update an existing record when the underlying rule, warning, requirement,
  procedure, contact or other traveller-relevant fact has materially changed;
- create a new record when a verified current source establishes a material
  traveller-relevant item that is not already represented;
- use the correct existing 14-category key and appropriate dataType;
- attach the real source and create the required SourceObservation for every
  new or updated intelligence item;
- preserve uncertainty and source provenance;
- do not create records merely because a signal exists or because a country
  has no records;
- do not perform a historical backfill simply to increase country coverage;
- do not manufacture content to fill empty categories;
- prioritise official government, border, CAA/AIS/NOTAM, airport, transport,
  health and other authoritative sources relevant to the country's current
  travel conditions.

An empty category is not a defect if no verified traveller-relevant
information exists. The objective is for verified current changes to reach
the country page automatically on subsequent refreshes, without inventing
coverage.

### Current Intelligence

Current Intelligence is event-based, not a collection of rewritten country
records.

`events` represents a discrete current event and may contain:

- `id`, title/summary and event type
- country/location/geographic scope
- event date/time and update timestamps
- `eventStatus`
- `publicationStatus`
- operational impact
- uncertainty and relationships
- legal/compliance review fields where required

Allowed event statuses:

`EARLY_REPORT`, `REPORTED`, `LOCAL_REPORT`, `UNVERIFIED`,
`CORROBORATED`, `CONFIRMED`, `DISPUTED`, `FALSE`, `CORRECTED`,
`SUPERSEDED`, `RESOLVED`.

Allowed publication statuses:

`DRAFT`, `INTERNAL_REVIEW`, `PUBLISHED`, `WITHDRAWN`.

Only `PUBLISHED` events are shown publicly.

Do not invent an event because another dashboard or social source mentions it.
A specialist oil/gas dashboard is a signal for investigation, not an
authoritative TI event source.

## Source observations and provenance

`sourceObservations` records what a particular source actually reported.

Every new observation MUST:

- reference exactly one primary target: `recordId` OR `eventId`
- contain `sourceId`, `sourceUrl` and `extractedClaim`
- have an `independenceGroupId`
- preserve observation status and source provenance
- preserve original language and translation method when translation is used

Never set both `recordId` and `eventId`, and never leave both empty.

Independence groups represent the underlying reporting chain. Syndicated or
copied reporting should normally share a group; genuinely independent
organisations may use separate groups. When uncertain, be conservative.

Source quality is separate from event status:

A = authoritative official/international/directly affected party within remit
B = established professional media
C = recognised specialist/trade/professional body
D = identifiable local/professional OSINT with demonstrable local presence
E = social/unverified
F = unknown/anonymous

Do not treat a high-quality source as proof that an event is confirmed.
Likewise, an event can be confirmed while a secondary source remains lower
quality.

### Rights and access

Where known, source/observation provenance may include:

- `contentRights`
- `accessMethod`
- `automationPermission`
- `originalLanguage`
- `geographicScope`
- `jurisdiction`

Use conservative values when rights or automation permission are unknown.
Do not assume that public web access grants a right to reproduce source text.

AI translation must retain the original language/source and identify the
translation method. Do not replace the original source with translated prose.

## Evidence rules

- Discovery and verification are separate steps.
- Wikipedia is supplementary only.
- Social/local OSINT can provide early warning but does not automatically
  become authoritative fact.
- Preserve uncertainty explicitly.
- Do not silently turn an early report into a confirmed event.
- If evidence is insufficient, keep the event in review/draft rather than
  filling the gap with inference.
- Corrections, withdrawals and supersession should preserve provenance rather
  than silently overwriting history.

## Public UI rules

The public application is read-only. It consumes static `data.json`; it
must not depend on `window.claude.use('db')` or an artifact-only runtime.

Current Intelligence appears before Hotspots on the home view.

Visible baseline groups:

1. TRAVEL ESSENTIALS
2. TRAVEL PREPARATION
3. OPERATIONAL PREPARATION
4. CURRENT INTELLIGENCE

The underlying 14 category keys remain unchanged.

Current Intelligence cards/details must provide source attribution. When an
event has only one independent source, display a clear text caveat that TI
has not independently confirmed the event.

Do not expose internal independence groups, legal-review notes, internal
uncertainty notes, internal feed IDs or other operational metadata on the
public UI unless deliberately promoted to a public field.

The public visual system is dark and belongs to TI itself. Do not add
FORGEDTITANIUM LTD branding to headers, navigation, titles or descriptions.

The exact legal footer text must remain:

FORGEDTITANIUM LTD a Company Registered in the Republic of Cyprus,

Reg No: HE 490451

## Oil & gas integration

`oilGasSummary` is an upstream specialist signal layer. It remains
independent from TI's event/source-observation model.

`scripts/refresh_oilgas.py` may read the sibling crude-flow-dashboard and
gas-lng-flow-dashboard repositories. It must never modify those repositories.

Do not manually rewrite `oilGasSummary` during normal TI intelligence
triage. If the deterministic refresh fails or produces an implausibly small
dataset, preserve the previous summary rather than partially overwriting it.

## Automation

Normal refreshes must:

1. read the current `data.json`;
2. check specialist oil/gas dashboards for signals;
3. prioritise current/security sources for verification;
4. verify claims against the actual source;
5. update only substantiated changes;
6. create a SourceObservation for every newly created/updated intelligence
   item where the architecture requires one;
7. preserve provenance and uncertainty;
8. keep `meta.counts` accurate;
9. update `meta.generated`;
10. validate the JSON before commit.

Run `python3 scripts/validate_ti_data.py` before committing data-model
changes.

Never fabricate source URLs, event IDs, publication dates, observations,
corroboration or confidence.

## Migration policy

The TI architecture migration is deliberately additive:

- existing countries, baseline records, sources, locations, change_log and
  oilGasSummary are preserved;
- `events` and `sourceObservations` are added without rewriting the
  grandfathered baseline;
- no historical observation backfill is required for all existing records;
- future new records/events require real source observations;
- event content from the Claude artifact must not be reconstructed from
  memory or approximate values — use an exact export/snapshot when migrating
  those objects.

Do not replace `data.json` wholesale with an artifact export. Merge
architectural improvements into the existing GitHub dataset.

## Normal refresh discipline

Do not make unrelated UI/branding changes during a data refresh.
Do not modify sibling repositories.
Do not delete historical data simply because a source is temporarily
unavailable.
Do not force changes when verification finds no material update.
