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



## Source tiers and acquisition register

config/source_registry.json is the canonical TI source due-diligence register. It records candidate sources, source tier, access method, legal/access category, automation permission and implementation status.

Use the four source tiers as a development constraint:

- TIER_1 — free/open government and international machine-readable sources; build first.
- TIER_2 — operational sources such as airports, airspace, borders, roads, ports, rail and maritime; integrate selectively when access, reliability and reuse rights are clear.
- TIER_3 — discovery / OSINT sources such as local media, specialist reporting and social channels; use for early warning and investigation, not as automatic authoritative fact.
- TIER_4 — paid/commercial data; deferred until TI generates income, and then only where free/public sources cannot provide sufficient quality or coverage and the commercial data creates a material product advantage.

Legal/access categories are explicit in the registry: OPEN, GOVERNMENT_OPEN, FREE_WITH_CONDITIONS, PUBLIC_WEB, HUMAN_REVIEW_REQUIRED, PAID_COMMERCIAL, DISCOVERY_ONLY, UNSUITABLE.

Do not infer reuse rights from public accessibility. Source terms, API terms, attribution and commercial-use conditions must be checked before production use.

## Government advisory change detection

scripts/collect_government_advisories.py is a deterministic discovery/change-detection layer for the U.S., Canada and Australia government advisory sources. It writes:

- data/source_snapshots.json — normalized metadata/hash snapshots only;
- data/government_change_queue.json — new/changed advisory candidates for review.

The collector MUST NOT create or publish TI events or rewrite baseline records. The Claude refresh step must verify the original government source, compare the substantiated change with existing TI data, and then create/update the appropriate record/event and SourceObservation where warranted.

Keep U.S., Canadian and Australian national perspectives separately attributed. Never combine their advisory levels into a synthetic TI risk score. New Zealand SafeTravel remains human-review until a suitable structured production feed is verified.

## Independent government source strategy

TI should not rely on FCDO alone. The source registry includes independent official travel-advisory perspectives from:

- U.S. Department of State — Travel Advisories
- Global Affairs Canada — Travel Advice and Advisories
- Australian DFAT — Smartraveller
- New Zealand MFAT — SafeTravel

Use these sources primarily for current/security intelligence and for corroboration of material changes to baseline travel guidance. Their national perspectives are not interchangeable and must remain separately attributed. Do not combine their advisory levels into a synthetic TI risk score.

Verified machine-readable/public access currently includes the U.S. Department of State RSS feed, the Australian Smartraveller public destinations-export API/RSS feeds, and Travel.gc.ca RSS/update feeds. SafeTravel is registered as an official source but its structured automation path remains HUMAN_REVIEW_REQUIRED until a public API/feed suitable for production use is independently verified.

For any material change found in an independent government source:
1. identify the exact destination/page and publication/update date;
2. compare it with the existing TI record/event;
3. create or update only the substantiated claim;
4. preserve the government source as its own SourceObservation and independence group;
5. if the source corroborates an existing event, record the corroboration rather than creating a duplicate event;
6. retain the original national perspective in the extracted claim.

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


## Paid/commercial source rule

Tier 4 is explicitly deferred until Travel Intelligence is generating income. Do not purchase subscriptions, paid APIs or commercial datasets during the current build phase. Build the core intelligence engine from Tier 1 and selectively integrated Tier 2 sources, with Tier 3 discovery/OSINT used only as an early-warning layer requiring verification/corroboration.

When TI is generating income, Tier 4 may be assessed selectively where free/public sources cannot provide sufficient quality or coverage and the commercial source creates a material product advantage.
