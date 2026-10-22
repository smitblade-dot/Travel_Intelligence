# 03AA-04_Country_Intelligence_Builder_TI

FORGEDTITANIUM LTD, Cyprus. Stream: 03 Agents & Automation. Substream: 03AA-04 Intelligence Automation
Agents. Business application: Travel Intelligence. Development, associated IP and agent costs belong under
03AA-04. No agent number, organisational code, cost code or document number has been invented.
Acceptance standard: `docs/AGENT_BUILD_STANDARD.md`.

## What it is

A worker that finds a missing or stale country/category in `data.json`, collects permitted evidence for it, and
produces a **draft** that is checked claim by claim against what was retrieved. The draft is saved on its own
branch (`ti-country-drafts`). It never writes `data.json`, never publishes, and a completed draft is not an
independently verified or published country profile.

It is the `country_content_builder` worker of the Master Agent (`03AA-04_Master_Agent_TI_Ops_Director`) and
also runs on GitHub Actions. It reuses the Director's queue and dedupe-key format (`store.task_dedupe_key`), its
reserve-before-call spending gateway (`ModelGateway`, through the monitor's `MonitorSpend` wrapper so limits are
shared across runs), the compare-and-swap state branch backend, text sanitising, and the operational report. It
reuses the baseline requirements (`config/baseline_requirements.json`), the 14 category keys, the existing
`data.json` record, source and observation shapes, and the baseline-batch shape that
`scripts/merge_baseline_batches.py` reads. It does not duplicate the Sonnet bulk generator
(`baseline-population.yml`).

## What it does, step by step

1. **Detect a gap** (ordinary code). A country/category is MISSING when it has no active record (same definition as
   `scripts/audit_baseline_coverage.py`), or STALE when every active record is past its `reviewDate` or last checked
   more than 365 days ago. Only the configured countries and categories are considered.
2. **Queue a deduplicated task.** One task per country, category, gap type and recheck window (7 days), using the
   Director's dedupe-key format. A repeat plan creates nothing. In the Master Agent queue the same key stops a
   second task; on Actions the ledger on the drafts branch does.
3. **Collect permitted evidence** (ordinary code). HTTPS GET from hosts named in `config/country_builder.json` only,
   `robots.txt` checked first (an unreadable `robots.txt` counts as not permitted), size, time and retries bounded.
   Pages become numbered, verbatim passages with their heading path. Publication dates are recorded only if the source
   states them; otherwise `publicationDateStatus` is `NOT_STATED`. Each source gets a status: `RETRIEVED`, `STALE`,
   `UNAVAILABLE`, `BLOCKED_BY_ROBOTS` or `PARSE_FAILED`. A source that is not `RETRIEVED` contributes nothing.
4. **Compute what code can compute.** Climate and seasons come from the NASA POWER monthly climatology for the
   capital's grid point (hottest/coolest month, wettest/driest months), with no model. Plugs come from TI's own
   country reference fields and are flagged as needing an external source. The next 4-6 weeks is always reported as
   missing evidence until an approved current outlook source is configured.
5. **Draft the rest with Haiku** (only if there is changed evidence, the mode is `draft`, and paid execution is
   configured). The model receives only the selected passages and proposes claims with verbatim quotes.
6. **Check every claim** (ordinary code). A claim is kept only if its quote is verbatim in a cited passage, every
   number in it appears in its quotes, it makes no assurance (safe, normal, no restrictions, operating normally) that the
   quotes do not make, and it does not use present-tense language unless declared CURRENT and supported by a source that
   is allowed to support current claims. Valid CURRENT claims go to `currentContext`, beside the baseline, never into
   candidate records. A CONFLICT claim needs two independent sources, otherwise it is downgraded to UNCERTAIN.
   Rejected claims are listed in the draft with the reason; a topic with nothing left is reported as missing.
7. **Build the candidate batch.** Records (status `DRAFT`), any new sources, and one SourceObservation per
   record/source with `recordId` only, `sourceUrl`, `extractedClaim`, `independenceGroupId`, retrieval time, rights and
   language. It is merged into a **copy** of `data.json` and run through `scripts/validate_ti_data.py`; it is never
   applied.
8. **Save, report and cost.** Draft, ledger and `country_builder_report.md` go to `ti-country-drafts` by compare-and-swap
   commit. The report shows country and category, the gap, the draft content, sources, uncertainty, review status, and
   the actual processing cost (measured from API usage fields x list prices; not invoice-confirmed).

## Draft content rules (from the brief and CLAUDE.md)

- Every claim carries source URL, publisher, publication date where stated, retrieval timestamp and the verbatim quote.
- VERIFIED, UNCERTAIN, CONFLICT and missing evidence are kept distinct. Absence of evidence is never written as safe,
  normal or operating.
- Original language is recorded per source; no translation is performed, so `translationMethod` is `NONE`.
- Model memory is never evidence. Stable reference information is separated from information needing current retrieval.
- Content rights and automation permission are recorded per source as configured (TravelHealthPro is UNKNOWN, so only short
  quotes are kept).

## Where it runs and what triggers it

- **GitHub Actions** (`.github/workflows/country-intelligence-builder.yml`): daily cron `41 5 * * *` UTC (GitHub may delay
  scheduled runs), manual dispatch, and a same-repository pull-request trial. Permissions: `contents: write` for the
  drafts branch only. On pull requests the model key is not passed and dry-run is forced.
- **Master Agent queue** (one host; the NUC is out of scope and not deployed): the `country_content_draft` task type is
  registered; `country_builder_interval_minutes` in the Director config enqueues gaps. Off unless set.
- **Evaluation** (`.github/workflows/country-builder-eval.yml`): runs only when `config/country_builder_eval_approval.json`
  exists; one capped live Haiku run over the manually assessed cases in
  `tests/fixtures/country_builder/labelled_cases.json`.

## Controls

| Control | How | Effect |
|---|---|---|
| Stop everything | repository variable `TI_CB_MODE=off`, or disable the workflow | job skipped; immediate |
| Pause / stop | `python3 scripts/country_builder.py pause`, `stop` (resume with `resume`) | stored in the ledger; no task starts |
| Observe | `operation.mode: observe` (committed default) | plans and checks evidence, writes nothing, no model call |
| Draft | `operation.mode: draft` | saves drafts; the model is called only if paid execution is also configured |
| Paid execution | `TI_CB_DRY_RUN=false` **and** `TI_CB_PER_TASK_LIMIT_USD`, `TI_CB_DAILY_LIMIT_USD`, `TI_CB_MONTHLY_LIMIT_USD` **and** `ANTHROPIC_API_KEY` | otherwise `awaiting_paid_authorisation`, $0 |
| Review | `python3 scripts/country_builder.py review <draftId> --decision accepted\|rejected\|needs_changes --by NAME` | records a decision; publication stays `DRAFT` |

The Ops Director's `TI_OPS_*` limits and the earlier evaluation allowance are not read: this agent has its own limits.

## Cost

Ordinary code costs nothing. A scripted Ghana call is about 2,200 input and 600 output tokens: about $0.0004 at Haiku 5.5
list prices. A conservative per-call reservation is about $0.0013. No eligible work means no model call; unchanged
evidence means no model call (the evidence fingerprint covers the exact passage text the model would see, not page
chrome such as live weather widgets). Infrastructure is GitHub Actions minutes (about one minute per run).

## Tests

`tests/test_ti_ops_director_country_builder.py` and `tests/test_ti_ops_director_country_eval.py` (offline, no key):
configuration; gaps; evidence parsing, robots, unavailable, parse failure, stale, hostile text; grounding (paraphrase,
invented number, assurance language, undeclared and unsupported current claims, conflict, unknown passage); end-to-end draft;
dry-run; duplicate and unchanged evidence; supersession; malformed output; Sonnet only after failed validation and only
when enabled; Opus never; budget exhaustion; shared daily limit; unavailable and stale evidence; interrupted execution and
lease recovery; bounded errors; provider failure; off/observe/pause/stop/resume; review; the real-git branch backend
against a bare remote (main untouched); Master Agent worker, dedupe, capability limits and report; evaluation scorer,
refusals, cap, and idempotence. Model behaviour in all of them is **scripted (mock)** and says so.
