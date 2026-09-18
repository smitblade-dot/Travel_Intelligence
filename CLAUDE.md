# Forged Titanium — weekly data refresh (this repo)

This repository is a static website (GitHub Pages) — a country travel
intelligence tool: entry requirements, security, health and emergency
information for personnel travelling internationally, with an emphasis on
oil & gas operating regions. `index.html` reads its data from `data.json`
in this same repo at runtime — nothing else in this repo needs to change
for a normal data update.

You are run here once a week (GitHub Actions cron) to check for and apply
real changes to `data.json`, then push the update straight to this repo.
This is an unattended run — there is no one to ask questions; make
reasonable judgment calls, and finish in one pass: check → (edit
`data.json` only if something real changed) → commit → push.

**No true real-time feed is connected here** (free/public sources only —
mainly UK gov.uk FCDO travel advice and UK Home Office immigration
guidance) — weekly polling is the closest practical approximation to
"kept current" that's achievable this way. Some runs will find nothing
materially new for most sources, and that's expected, not a failure.

## Keep it fast and cheap — this is not an unbounded deep-dive every time

Doing a full re-check of all 89 sources with no prioritisation every
single run would be slow, expensive, and mostly redundant. Instead:

1. **Fast triage first (every run).** Read `data.json`'s `sources`
   collection and rank sources by:
   - Any source whose `reviewFrequency` says "Monthly" or "Fast-changing"
     — check these every run. These are almost always `SECURITY` category
     sources (conflict, terrorism, civil unrest, border areas) where the
     situation genuinely moves week to week.
   - Any source whose `dateChecked` is now older than the interval implied
     by its own `reviewFrequency` (e.g. "Every 3-6 months" and it's been
     4+ months; "Every 12 months" and it's been 13+ months).
   - Everything else (health, electrical standards, general reference
     pages) can wait — check these opportunistically as time/turns allow,
     lowest priority.
2. **Re-fetch each prioritised source's `url`** (WebFetch) and compare
   against the `records` that cite it (`records` where `sourceId` matches
   that source's doc id in `data.json`).
3. **If the guidance has materially changed** (a new visa fee, a changed
   emergency number, an escalated/de-escalated security warning, a new
   entry requirement, a resolved or worsened conflict situation, etc.),
   update that record's `content` (and `dataType`/`confidence` if
   warranted), set `dateChecked` to today and `reviewDate` to today plus
   the interval implied by the source's `reviewFrequency`. Also update the
   source's own `dateChecked` to today.
4. **If nothing has changed** for a source you did check, just bump that
   source's `dateChecked` (and the `dateChecked`/`reviewDate` of the
   records citing it) so the data honestly reflects it was re-verified —
   don't rewrite unchanged prose just to have something to show.
5. **If a source's page is gone (404) or has clearly been superseded**,
   don't delete anything — instead set that source's `notes` to flag it
   (e.g. `"URL returned 404 as of 2026-09-30 — needs manual review"`) and
   leave `active` as-is for a human to decide.
6. **If the fast triage finds nothing needing a change at all:** don't
   force an edit to any source/record. Just update `meta.generated` to
   the current timestamp (see below), commit, and push, so the site's
   "data as of …" indicator stays honest and current.

## Conventions — do not change without being told to

- **`meta.generated` is a full timestamp, not just a date** — set it to
  the current time as ISO 8601 UTC, e.g. `2026-09-23T21:05:12Z`, every
  single run (whether or not anything else changed). This is what the
  site's "data as of …" indicator displays.
- **Confidence tagging.** Every record carries a `confidence` of `HIGH`,
  `MEDIUM`, or `LOW`. Never invent a precise detail you don't have a real
  source for. Never collapse ENTRY, SECURITY, HEALTH and EMERGENCY
  information into one vague record — keep them conceptually distinct
  even within one country.
- **`countryId` uses lowercase ISO-2** (e.g. `"sa"`, `"iq"`, `"gb"`) and
  must match an existing key in `countries`. Don't invent new countries in
  a normal refresh run — that's a separate, deliberate exercise.
- **The United Kingdom (`gb`) has no FCDO self-advice page** (FCDO
  publishes advice for other countries, not for the UK's own government)
  — its sources are UK Home Office immigration guidance instead. Its
  `gb-security-general` and `gb-health-nhs` records are Claude's own
  contextual summary rather than a sourced FCDO record like every other
  country, and are flagged as such in their `notes` field — preserve that
  flag; don't remove it or make it look like an FCDO citation.
- Do not touch `locations` in a normal refresh — location hierarchies are
  a separate, deliberate exercise (only Iraq and Saudi Arabia have any at
  present).
- **Do not touch `oilGasSummary` in a normal refresh.** It's a separate
  snapshot (crude + gas/LNG production, infrastructure and disruption data
  per country) pulled from the sibling `crude-flow-dashboard` and
  `gas-lng-flow-dashboard` repos, keyed by lowercase ISO-2, each entry
  carrying its own `source_generated` timestamp from those dashboards.
  Refreshing it means re-pulling and re-summarising from those two repos —
  a separate, deliberate exercise, not part of the weekly FCDO/Home Office
  triage this file governs. 28 of the 29 countries have an entry (Jordan
  doesn't — it's not an oil & gas producer/transit country in those
  dashboards' data; that's expected, not a gap to fix).
- **Do not touch `assets/img/*.jpg`** (the subtle background photography)
  or the `<style>` block in a normal refresh — visual/branding changes are
  a separate, deliberate exercise.

## `data.json` structure

Top-level keys: `meta`, `countries`, `sources`, `records`, `locations`,
`change_log`, `oilGasSummary`. Read the current file first to see the
exact shape of each row before editing — don't guess field names.

- `countries`: keyed by lowercase ISO-2. Basic country facts (capital,
  currency, driving side, plug type, region, `oilGas` flag, etc.) plus a
  `notes` field. Not normally touched by a refresh run.
- `sources`: keyed by an id like `"sa-fcdo-entry"`. Fields: `countryId`,
  `organisation`, `sourceName`, `sourceType` (`GOVERNMENT` /
  `INTERNATIONAL_ORGANISATION` / `SPECIALIST` / `COMMERCIAL` /
  `GENERAL_WEB`), `confidence`, `url`, `dateChecked`, `reviewFrequency`,
  `notes`, `active`.
- `records`: keyed by an id like `"sa-entry-passport"`. Fields:
  `countryId`, `locationId` (usually `null` — country-wide), `category`
  (`ENTRY` / `SECURITY` / `HEALTH` / `EMERGENCY` / one of the other 9
  categories the taxonomy defines but that aren't populated yet),
  `subcategory`, `title`, `content`, `dataType` (`FACT` / `REQUIREMENT` /
  `CONSIDERATION` / `WARNING` / `CONTACT` / `STATISTIC` / `PROCEDURE`),
  `confidence`, `sourceId`, `status` (`ACTIVE`/`DRAFT`/`ARCHIVED`),
  `dateChecked`, `reviewDate`, `notes`.
- `locations`: keyed by an id like `"loc-iq-zubair"`. Nested hierarchy via
  `parentLocationId`. Not touched by a normal refresh.
- `oilGasSummary`: keyed by lowercase ISO-2. Each entry has an optional
  `crude` and/or `gas` object (commodity, main export points, primary
  route, production, infrastructure with a Red/Amber/Blue/Green
  `worst_status`, active disruptions, and its own `source_generated`/
  `source_title`). Not touched by a normal refresh (see above).
- `change_log`: append one row for **every** substantive edit made this
  run (a corrected figure, an escalated/de-escalated warning, a changed
  requirement). Fields: `change_id` (increment from the highest existing,
  start at 1 if empty), `timestamp` (full ISO 8601 UTC, same as this run's
  `meta.generated`), `date`, `category` (e.g. `"Correction"`, `"Escalation"`,
  `"De-escalation"`, `"Resolved"`), `countryId`, `summary`, `detail`,
  `confidence`, `source`. If nothing changed this run, don't add a row
  just to have one.

## What to do, step by step

1. Read the current `data.json` in this repo.
2. Run the fast triage (above) to build a prioritised list of sources to
   re-check this run.
3. For each prioritised source, WebFetch its `url` and compare against
   the records citing it.
4. Edit `data.json` in place with any real changes, following the schema
   and conventions above (including `change_log` where applicable).
   Keep the file valid JSON (check it parses) and keep `meta.counts` in
   sync with the actual number of entries in each collection if you add
   or remove anything.
5. Always set `meta.generated` to the current ISO 8601 UTC timestamp,
   then commit (message like `Data refresh — 2026-09-23T21:05Z` — or note
   what changed if something did) and push, using git directly:
   `git add data.json && git commit -m "..." && git push`. Do not touch
   any other file in this repo during a normal refresh.

Do not wait for approval or ask a question — this is a scheduled,
unattended run. Do not touch anything outside this repository (no other
repos, no external services) as part of this task.
