# 03AA-04_Master_Agent_TI_Ops_Director

FORGEDTITANIUM LTD, Cyprus. Stream: 03 Agents & Automation. Substream: 03AA-04 Intelligence
Automation Agents. Business application: Travel Intelligence. Agent development, IP and costs belong
under 03AA-04. No agent number, organisational code, cost code or document number has been invented:
the cost code field is intentionally absent until the FT register supplies it.
Acceptance standard for this and every 03AA-04 agent: `docs/AGENT_BUILD_STANDARD.md`.

## Completion record (21 October 2026)

**Status: complete for the agreed scope; merged as PR #27 (`26e8321`).** Ongoing paid triage and
publication are disabled (no `TI_OPS_*` limits, `TI_OPS_DRY_RUN` and `TI_MONITOR_PUBLISH` unset).

- **Implemented:** the controller (one SQLite queue, leases, pause/stop, scheduling, report); the
  `feed_health_diagnostic` worker; the spending gateway (reservation before every paid call, per-task,
  daily, monthly and evaluation total limits, shared ledger on Actions); the Haiku monitor routed through
  it, with fail-closed publication and run outcome reported separately from triage outcome.
- **Measured validation:** 160 offline tests and all PR checks green; Docker image built and
  smoke-tested; live read-only feed diagnostic on Actions; approved live Haiku 5.5 evaluation (run
  39595259197): relevance 11/11 firm cases, injection ignored, 0 malformed replies, conflicting reports
  never doubled. One defect found (incident-key format) and fixed.
- **Actual test cost:** 16 calls, $0.002742 (about EUR 0.0024 at the ECB rate of 20 Oct 2026) of the
  EUR 1 evaluation cap; estimated from API usage fields, not an invoice. No other paid calls.
- **Operational report:** each monitor run's job summary (Actions tab > "Continuous intelligence
  monitoring (Haiku)"), and `python3 scripts/ops_director.py report`; the live feed diagnostic appears in
  the summary of "TI Ops Director tests". Evaluation evidence: `docs/evidence/ops_director_haiku_eval.json`.

## What this is, and is not

This work package delivers the **controller foundation, one working diagnostic worker, and (second
package) routing of the existing Haiku monitor through the spending gateway**. It is not the agent team.

**Workers, stated once.** Nine workers are registered. **Two are implemented:** `feed_health_diagnostic`
(a diagnostic, not one of the seven flow roles) and, since the production controller work package,
`production_verification` (`docs/TI_PRODUCTION_CONTROLLER.md`). **Six functional-flow roles remain
unimplemented as queue workers:** discovery, triage, investigation_content, verification, publication,
daily_consolidation, plus the planned `production_recovery` role whose tasks wait for Marc. Any task for
them is blocked, never faked. Some roles
have partial work outside the queue: the Python collectors (discovery), the Haiku monitor (triage, now
spend-gated but still not a queue worker), and the Actions refresh (daily consolidation). The existing Python monitor and the GitHub
Actions refresh remain the working pipeline and are not replaced or duplicated.

## Repository findings (inspected 21 October 2026)

- `CLAUDE.md` is the only instruction file (no `AGENTS.md`). Its rules were followed: this build is
  additive, does not touch `data.json`, never creates events, and keeps publication off.
- **PR #23 contents (continuous monitoring, Haiku triage):** commit `1d76439` "Introduce 15-minute Haiku
  intelligence monitoring (publication disabled)" is in `main` history, between PR #21 and PR #24, and
  adds `scripts/continuous_intelligence_monitor.py`, the 15-minute workflow, its tests and
  `docs/CONTINUOUS_INTELLIGENCE_AGENTS.md`. Its branch still exists on the remote. The PR number and
  merge status is **unconfirmed**: there is no GitHub API access in this session, and the repository
  evidence is ambiguous. `1d76439` has no PR number in its subject (unlike #24 and #25), there is no merge
  commit for #23, and the six commits on `feature/continuous-haiku-intelligence-monitoring` are not
  ancestors of `main` (consistent with a squash, but not proof). What can be said: the monitor's
  code is on `main`. Whether PR #23 itself is merged, closed, or superseded must be read from GitHub.
  PRs #24 and #25 (freshness, duplicate and budget guards) are on `main`.
- Two bot commits ("Continuous intelligence monitor", 06:38 and 06:58 UTC on 21 October) show the
  Actions job has run, and the committed state records 5,936 input and 950 output tokens used on
  21 October, so Haiku calls were made with a working key. At Haiku 4.5 list prices that is about
  $0.011 (estimate, not an invoice). That spend predates the gateway ledger and is not in it.
- **Publication in each path.** New controller: disabled in every configuration (`PublicationGateway`
  refuses; tested). Existing monitor: the code default is `TI_MONITOR_PUBLISH=false`, the workflow
  passes the repository variable of that name, and the variable's value cannot be read from here, so
  its actual setting is **unconfirmed**. The evidence is consistent with publication being off:
  `data.json` contains zero monitor-created events (`monitorIncidentKey` / `early-` IDs).
- **ReliefWeb.** No record of a submitted or approved application exists in the repository. The docs say
  the earlier `travel-intelligence` appname was rejected (HTTP 403) and the snapshot says none is
  configured. Whether an application is pending cannot be seen from here. Nothing was submitted or
  requested. Check the provider account and e-mail first; apply only if none exists.
- **Discovery feed recovery** (`scripts/collect_phase1_signals.py`, `feed_support.py`,
  `audit_feed_health.py`, `docs/DISCOVERY_FEED_OPERATIONS.md`) is reused as the evidence source. The
  stored snapshot (`data/phase1_signals.json`, generated 2026-10-20T21:21Z) reports GDELT
  unavailable (HTTP 429), ReliefWeb blocked (no approved appname), ADSB.lol degraded; OurAirports,
  GDACS and NASA FIRMS healthy per snapshot (not independently confirmed here).
- Existing monitor controls: daily token caps (`TI_MAX_DAILY_*`) only. **Production today has no
  monthly, per-task or currency limit.** The spending gateway built in package 1 protects nothing in
  production until the monitor uses it. In this branch the monitor does (next section), but that is
  **unmerged and undeployed**, so production is unprotected until this branch is merged and the
  repository variables are set.
- The existing 21:00 Europe/Nicosia refresh runs on two UTC crons (`0 18 * * *`, `0 19 * * *`) and a
  slot gate (`scripts/ti_ops_director/refresh_slot.py`) that keeps the cron that is 21:00 in Cyprus on that
  date. GitHub's `timezone:` key was dropped after it fired at 21:00 UTC on 20 and 21 October 2026.
  The Director reuses the same wall-clock rule and does not run a second consolidator.

## Monitor integration (work package 2)

`scripts/continuous_intelligence_monitor.py` no longer contains a direct Anthropic call. Its only paid
path is `MonitorSpend.triage()` -> `ModelGateway.run()`: input-size cap, dry-run check, per-model price
lookup, reservation against per-task, daily and monthly limits, call, settlement, then output validation.
Kept as before: URL dedupe via the `seen` cursor, the 36-hour freshness window, 12-headline batches,
the daily token caps, the 2,200 output-token cap, the 90 s timeout, the publish guards and the
instruction text.

**Authoritative spend record on Actions** is `data/continuous_monitor_state.json` on `main` (`spend`). See
"Persistent state on GitHub Actions" below for how it stays correct across runs. Spend made before this
change is not in the ledger. Amounts are estimates from API usage fields and list prices.

### Production transition (do this before merging)

After merge the gateway refuses every paid call until it is explicitly configured, so the existing Haiku
triage **stops** unless these repository variables (Settings > Secrets and variables > Actions > Variables)
are set first. Nothing below is set by this PR; the values are recommendations for Marc to decide.

| Variable | Unit / format | Default when unset | Effect | Recommended start |
|---|---|---|---|---|
| `TI_OPS_DRY_RUN` | `false` / `0` / `no` turns dry-run off; `true` / `1` / `yes` keeps it on | dry-run **on** | Off is the only way a model is called. Any other value keeps dry-run on and logs a warning. | `false` |
| `TI_OPS_PER_TASK_LIMIT_USD` | decimal USD > 0, finite | not configured: **paid call blocked** | Cap on reservations for one triage task (first call + corrective retry). | `0.01` |
| `TI_OPS_DAILY_LIMIT_USD` | decimal USD > 0, per UTC day | not configured: **paid call blocked** | Cap on committed spend (settled + open reservations) per UTC day, across all runs. | `0.50` |
| `TI_OPS_MONTHLY_LIMIT_USD` | decimal USD > 0, per UTC calendar month | not configured: **paid call blocked** | Same, per UTC month. | `12.00` |
| `TI_OPS_MAX_MODEL_CALLS_PER_TASK` | whole number | `2` | 2 = Haiku + one corrective retry. 3 allows one logged Sonnet escalation. | leave unset |
| `TI_HAIKU_MODEL` | model ID | `claude-haiku-5-5` | Rollback: `claude-haiku-4-5-20251001` (repriced automatically). | leave unset |
| `TI_MONITOR_PUBLISH` | exactly `true` (any case, no spaces) | publication **off** | See "Publication control". | leave unset |
| `ANTHROPIC_API_KEY` (secret) | Console API key | job fails | Already set (production runs used it). | unchanged |

All three limits must be positive numbers or nothing is paid for. A non-numeric, infinite or NaN value is a
configuration error: the run makes no model call, records `NOT_RUN_NOT_CONFIGURED` and the job fails
(exit 3) so it cannot pass silently. Zero or a negative value is "not configured", with the same result.

Basis for the recommendations (estimates, not invoices). The current `data.json` (52 countries, last 150
event titles) plus 12 maximum-length headlines is about 19,000 characters, which the gateway prices
pessimistically at 8,414 input tokens. A reservation is therefore about $0.0019 at Haiku 5.5 rates, and a
task with its corrective retry about $0.0039, so $0.01 per task leaves room for prompt growth. The worst
case (all 96 runs a day with new headlines, each needing a retry) is about $0.37 a day and $11.56 a month.
The one measured Haiku 4.5 run used 5,936 input and 950 output tokens. With about 30 percent more tokens on
Haiku 5.5 that is about $0.0014 a call, or about $4.13 a month if every run triaged.

The existing daily token caps still apply and are not repository variables: `TI_MAX_DAILY_INPUT_TOKENS`
(250,000) and `TI_MAX_DAILY_OUTPUT_TOKENS` (50,000) are code defaults because the workflow does not pass
them. At roughly 7,700 Haiku 5.5 input tokens a call, that is about 32 triage calls a day. After that, runs
record `NOT_RUN_BUDGET_EXHAUSTED` (`daily_token_cap`) until the UTC day changes. Raise these only by
changing the workflow, deliberately.

Sequence:
1. Set the three limits and `TI_OPS_DRY_RUN=false` (leave `TI_MONITOR_PUBLISH` unset).
2. Merge. The first run's job summary must say `Triage: COMPLETED`. If it says `NOT_RUN_NOT_CONFIGURED`
   the job is red. Fix the named variable.
3. If you want an observation period first, leave `TI_OPS_DRY_RUN` unset. Every run then reports
   `NOT_RUN_DRY_RUN` and keeps the headlines. That is **not** intelligence processing. The summary,
   the warning annotation and `consecutiveUntriaged` in the report say so on every run. Dry-run never
   turns the job red on its own.
4. Rollback: set `TI_OPS_DRY_RUN` back to `true` (no paid calls) or revert the PR.

### Operational visibility

A green job means **the workflow executed**. It does not mean that triage happened. Every run writes
`lastOutcome` to the state file and a job summary table with separate rows for "Workflow run" and
"Triage", and emits one annotation. `consecutiveUntriaged` counts runs in a row that had new headlines but
did not triage them, whatever the reason. `consecutiveFailed` counts only `FAILED` runs.

| Triage outcome | Meaning | Annotation | Job |
|---|---|---|---|
| `COMPLETED` | New headlines triaged (or served from the unchanged-input cache) | notice | green |
| `NO_NEW_HEADLINES` | Nothing new to triage | none | green |
| `NOT_RUN_DRY_RUN` | Gateway in dry-run; headlines kept | warning | green |
| `NOT_RUN_BUDGET_EXHAUSTED` | Daily/monthly/per-task allowance or daily token cap reached; headlines kept | warning | green |
| `NOT_RUN_CONCURRENT_RUN` | Another run holds a reservation for the same headlines | warning | green |
| `NOT_RUN_NOT_CONFIGURED` | Limits missing/invalid, pricing unverified, API key missing | error | **red (exit 3)** |
| `FAILED` | Provider error, invalid output after the retry, call cap, oversized input, state sync failure | error | green, then **red (exit 4)** from the 4th consecutive `FAILED` run |

The job is also red (exit 4) whenever the state could not be persisted. `python3 scripts/ops_director.py report`
includes a "Haiku monitor on GitHub Actions" section built from the same state file, as fresh as the
checkout it reads.

### Persistent state on GitHub Actions

The state file holds the discovery cursor (`seen`), the daily token counter, `spend` (`daily`, `monthly`,
`open` reservations, `expired`), the unchanged-input cache (`triageCache`) and the outcome fields.
`TI_MONITOR_STATE_BACKEND=git` (set in the workflow) makes every change a compare-and-swap commit to
`main` (`scripts/ti_ops_director/monitor_state.py`). The monitor reads the file at the current tip and
pushes a commit whose parent is that tip. If another run pushed in between, GitHub rejects the push as
non-fast-forward and the monitor re-reads and re-applies (up to 5 times, then it fails closed).

- **Before every paid call** a reservation (pessimistic input estimate plus the full output cap) is
  checked against the daily and monthly limits **in the shared state**, including other runs' open
  reservations, and pushed. If the push fails, no call is made (`state_sync_failed`).
- **Straight after the call** the settlement (actual usage at list price) and the result-cache entry are
  pushed. The final commit then adds the cursor, outcome and, when publication is on, `data.json`
  (re-applied to the latest `data.json` and validated before the push).
- **Killed run, cancelled job or "Re-run failed jobs"**: an unsettled reservation stays on `main`. It
  counts against the limits, and after 60 minutes it is charged **in full** (`expired`). Spend can be
  over-counted this way, never under-counted.
- **Concurrent runs**: the workflow concurrency group already serialises scheduled runs. The
  compare-and-swap also covers a second writer that is outside the group, and two runs that pick up the
  same headlines cannot both pay for them (`NOT_RUN_CONCURRENT_RUN`).
- **Unchanged-input detection**: headline URLs already triaged are skipped via `seen` (merged as a union,
  so a concurrent run's cursor is never lost). An identical prompt seen within 48 hours (up to 20 entries)
  is answered from `triageCache` with no model call, including after a run whose final write failed.
- A paid run makes 3 commits to `main` (reservation, settlement, final state). An unpaid run makes 1. The
  previous workflow made 1 commit per run.

Locally (and in tests) `TI_MONITOR_STATE_BACKEND` is unset and the same contract runs on the local files
under an exclusive lock. That is correct for several processes on **one host** only.

### Diagnostic task queue: local only, not supported on Actions

The Director's task queue (`store.py`, SQLite, WAL) is **local-only**. It is authoritative only on one
host's persistent disk (the NUC `/data` volume). It is **not** supported on GitHub Actions: runner disks
are ephemeral, so tasks, leases, deduplication windows and the queue's own spend ledger would vanish
after each job. Actions runs only the offline tests, the fixture demonstration and the Docker image build.
The monitor's per-run SQLite ledger is throwaway by design and authoritative nowhere; its totals come
from and go back to the shared state file.

### Publication control (traced)

`vars.TI_MONITOR_PUBLISH` (repository variable) → workflow step env `TI_MONITOR_PUBLISH` (the only
place it is set; an unset variable renders as an empty string) → `publication_setting()` in
`scripts/continuous_intelligence_monitor.py` (the only reader). Only `true` (any case, no padding)
enables publication. Unset, empty, `false` and any other value (`yes`, `1`, `" true"`, `on`) disable it,
and an unrecognised value is flagged with a warning. Publication also needs a `COMPLETED` triage and a
`data.json` that passes `validate_ti_data.py`. Tests: `tests/test_ti_ops_director_monitor_visibility.py`.
The Director's own `PublicationGateway` refuses in every configuration.

**Actual production setting:** the variable's value cannot be read with the access available in this
session. Indirect evidence comes from the public Actions logs of the production runs on 21 October 2026
(39558803018 at 06:58 UTC and 39562356978 at 07:29 UTC): the step environment showed
`TI_MONITOR_PUBLISH:` empty, and run 39558803018 printed `DRY_RUN_DECISIONS`. Publication was therefore
off **at those times**. The current value is **unverified**.

**Escalation.** Default `TI_OPS_MAX_MODEL_CALLS_PER_TASK` is 2 (Haiku plus one corrective retry), so
Sonnet is never reached by default; a failed batch is retried on the next run. Setting it to 3 allows one
Sonnet call with the logged reason `failed_corrective_haiku`. Opus has no environment switch at all.

**Model move to Haiku 5.5 (`claude-haiku-5-5`, $0.10 / $0.50 per million tokens up to 100k prompt
tokens, $0.50 / $2.50 above) from `claude-haiku-4-5-20251001` ($1 / $5)**, checked against Anthropic's
pricing, models and Haiku 5.5 migration pages on 21 October 2026. Compatibility handled in
configuration (`model_options`): `temperature: 0` is rejected with HTTP 400 so it is omitted; adaptive
thinking is on by default and counts inside `max_tokens`, so requests send `thinking: disabled` with
`effort: low` (documented as permitted at that level); no assistant prefill is sent; the tokenizer
produces about 30 percent more tokens, so the pessimistic estimate is deliberately generous; prompts are
refused above 60,000 estimated tokens so the 100k tier is never entered, and a reservation prices from
80 percent of the threshold at the long rates. Prices are keyed by model ID, so setting `TI_HAIKU_MODEL`
back to the old ID reprices correctly and restores `temperature: 0`. Rollback is that one variable.

### Model compatibility evaluation (measured 21 October 2026)

**Approved live run.** Marc approved up to EUR 1 for one bounded Haiku quality evaluation only
(`config/ops_director_eval_approval.json`). This approval is not ongoing paid operation. Workflow
`ops-director-eval.yml`, run 39595259197, used the repository API key with publication off.

- **Rate:** ECB reference rate dated 2026-10-20: 1 EUR = 1.1215 USD, fetched by the run itself; the run
  refuses if the rate is unavailable or more than 5 days old.
- **Cap:** EUR 1 × 1.1215 × 0.90, rounded down to the cent and limited to 1.00 USD, gives a 1.00 USD cap.
- **Enforcement:** every reservation, corrective retries included, was checked against the all-time total
  in `data/ops_director_eval_ledger.json` before the call.
- **Usage:** 16 calls, 11,347 input and 3,214 output tokens.
- **Cost:** **0.002742 USD (about EUR 0.0024)**, estimated from API usage fields at list price, not from an
  invoice. 0.997257 USD of the cap remains unused.
- **Results:** `docs/evidence/ops_director_haiku_eval.json`. The verbatim model decisions are in
  `tests/fixtures/ops_director/monitor_recorded_haiku55.json`.

| Measure (`claude-haiku-5-5`, production prompt) | Result |
|---|---|
| Relevance, firm cases (publish flag matches the manual label) | **11 / 11** |
| Relevance, borderline cases | 2 / 3 (published the local Ohio chemical-fire shelter order) |
| Instruction-injection headline | not followed, not published |
| Extraction fields (country, category, date, ongoing, valid key) | 24 / 30 as measured; the 3 invalid keys are fixed in code (below) |
| Conflicting evidence: never more than one event per incident | 2 / 2 (both disputed incidents produced 0 events) |
| Malformed or refused responses | 0 of 16 calls; no corrective retry was needed |

**Defect found and fixed.** The model wrote incident keys with spaces (for example
`"lagos-ibadan expressway tanker explosion closure"`). The guard accepted only hyphenated keys, so 3 of the
6 clearly relevant reports would have been dropped. `normalise_incident_key()` now lowercases the key and
joins its words with hyphens before validation and duplicate matching. The prompt is unchanged, so the
measurement above still describes the production prompt. Replaying the recorded live decisions through
the fixed guards (no cost; `RecordedHaiku55` tests) gives:
- 5 of 6 relevant reports published as attributed, LOW-confidence EARLY_REPORTs;
- 0 of the 8 firm irrelevant ones;
- 1 borderline item (Ohio);
- at most one event per disputed incident.

Remaining model behaviour, which the guards treat conservatively:
- The Riyadh sandstorm headline was marked "not ongoing" and undated, so it goes to review instead of
  being published.
- The Gatwick headline's "since 19 October" was not extracted as a date. It is still published, because
  the model marked it ongoing.

A re-run of the same prompt, model and cases is recognised from the ledger and makes no call; this was
confirmed on the next push (run 39595790904: "already evaluated", no reservation). One approval covers one
completed evaluation: a changed prompt or case set is refused until Marc gives a new approval.

Known limits, recorded rather than hidden:
- The triage schema cannot express "conflicting reports". If the model gives contradictory headlines
  about one incident *different* incident keys, the code guard creates one event per key. The test
  `test_known_limit_conflicting_reports_with_different_keys_create_several_events` demonstrates this.
  Monitor-created events always carry `conflictingInformation: false`.
- If a model wrongly returns `publish: true` for an injected headline, only the code guards stand between
  it and an attributed, LOW-confidence EARLY_REPORT.
- Parser overlap with draft PR #26: this branch applies the same strict rule (exactly one JSON object,
  optional fence, nothing before or after it) inside the gateway. PR #26 edits the pre-gateway
  `ask_haiku`, which this branch replaces, so the two will conflict in that function. If #26 merges first,
  resolve the conflict by keeping this branch's version.

## Architecture

```
scripts/ops_director.py            CLI
scripts/ti_ops_director/
  store.py        ONE authoritative queue: SQLite (WAL), BEGIN IMMEDIATE transactions
  controller.py   selection, leases, bounded concurrency, pause/stop, scheduled responsibilities
  workers.py      worker interface, capability checks, registry, planned (unimplemented) workers
  feed_health.py  the implemented diagnostic worker (Python only, no model)
  feed_scan.py    reads existing feed-health evidence, creates deduplicated diagnostic tasks
  models.py       model routing + the single gateway for any paid call
  budget.py       reservations, settlement, limits, EUR view
  scheduling.py   Europe/Nicosia slots, DST, missed-run coalescing
  report.py       operational report
  publication.py  disabled interface for future controlled publication
  sanitise.py     untrusted-text cleaning and secret redaction
```

**Authoritative queue.** One SQLite file on one host's persistent volume (`/data` in Docker). It is the
only queue. Copying the file to another machine, or running two hosts against separate copies, is not
a shared store and must not be done. Multi-host operation would need a server database; that is
deliberately out of scope. The GitHub Actions runner has an ephemeral disk, so the database does not
persist there: Actions can run the offline tests and one-shot diagnostics, not the queue.

**Task states:** pending, running, blocked, failed, completed, cancelled. Each task records identity,
business application, substream, worker, inputs, outputs, evidence, attempts, UTC timestamps, model
usage, estimated and confirmed cost, blocking reason, checkpoint and a `requires_human` flag.

**Duplicates and restarts.** A unique index on `dedupe_key` (application, type, subject, input
fingerprint, recheck window) covers pending, running, blocked and completed tasks. The same failing
feed therefore creates one task per recheck window (default 6 hours), not one per scan. Claims are
atomic with a lease; an expired lease returns the task to pending (or fails it at the attempt
limit). Workers here are read-only and write their result in one transaction, so a re-run after a
crash has no duplicate side effects. One scheduler lease protects each responsibility.

**Controls.** `pause` (no new tasks), `stop` (no new tasks, workers asked to stop), `resume`, `cancel <id>`.

## The implemented worker: `feed_health_diagnostic`

Reads `data/phase1_signals.json` and `config/phase1_sources.json` (read-only). For each source that is
not healthy, stale or unknown it creates one task, which performs bounded checks: configuration,
HTTP status, expected content shape, freshness, and the stored validation result.

HTTP 200 alone is never health. Outcomes: `healthy`, `provider_outage` (429/5xx), `stale_evidence`,
`configuration_issue`, `approval_required`, `recovered_unconfirmed` (probe passed, snapshot still
failing), `unknown_cause` (including no HTTP response: an outage cannot be told from a local
egress restriction). Approval and configuration findings **block** the task and require Marc;
unknown cause blocks it pending an investigation worker; an outage or stale finding completes the task
with the diagnosis recorded (the source stays unhealthy in the report). ReliefWeb without a
provider-approved `RELIEFWEB_APPNAME` makes **no request**, and nothing is ever submitted to the
provider. Fixture runs are labelled FIXTURE throughout and counted separately from live checks.

## Model routing and cost controls (enforced in code)

1. Ordinary code. 2. Haiku (default). 3. Sonnet only with a logged reason from a fixed list
(`conflicting_evidence`, `complex_investigation`, `failed_corrective_haiku`,
`representative_testing_exceeds_haiku`); the escalation, selected model and estimated incremental cost
are logged **before** the call. 4. Opus is refused unless `opus_approval` names Marc, the task type
and an allowance. A more expensive model never counts as verification.

Every paid call passes through `ModelGateway`: cached result reuse for identical content (no call),
dry-run skip, per-task call cap (default 2), at most one corrective retry on the same tier and then
escalation or block, output-token cap, 30 s timeout, JSON validation. Before the call a conservative
reservation (pessimistic input estimate plus the full output cap) is written inside a
`BEGIN IMMEDIATE` transaction against per-task, daily and monthly limits, so concurrent reservations
cannot overspend. After the call the reservation is settled with actual usage; a failed call with
unknown outcome keeps the full reservation. Missing or unverified pricing, or unset limits, block
paid work; deterministic tasks continue. Default state: dry-run, all limits unset.

Amounts are USD (provider currency), labelled **estimated** (API usage fields times configured price).
Invoice figures go in separately via confirmations; credits and payable cost show "not verified" until
recorded. EUR is shown only from a dated stored rate (`fx-set`); otherwise the report flags the missing
conversion. Claude Code subscription usage is excluded and is not assumed to cover API usage.

**Pricing is keyed by model ID in `config/ops_director_pricing.json`** (package 2; package 1 keyed by tier).
**Pricing and models checked 21 October 2026** against Anthropic's pricing and models pages
(platform.claude.com/docs/en/about-claude/pricing and .../models/overview): Haiku 5.5
(`claude-haiku-5-5`) $0.10 input / $0.50 output per million tokens (prompts up to 100k); Sonnet 5.5
(`claude-sonnet-5-5`) $2 / $10; Opus 5.5 (`claude-opus-5-5`) $4 / $20; batch is 50% off. These are
configuration values, not hard-coded. The monitor now defaults to Haiku 5.5 (see Monitor integration). Batch processing and prompt caching are
supported by the pricing model but not yet used: no implemented task needs a model.

## Scheduling

Monitoring interval is configurable (`monitor_interval_minutes`, default 15), aligned to UTC slots.
The daily consolidation slot is 21:00 Europe/Nicosia, resolved with `zoneinfo`, so it is 18:00 UTC in
summer and 19:00 UTC in winter automatically (tested across both 2026 changes). Missed slots coalesce
into one run; an overlapping run is skipped. The Director's daily-consolidation task is blocked as an
unimplemented worker: the existing Actions refresh remains the only consolidator.

**If GitHub Actions stays the scheduler:** `schedule` events are best effort. Runs can start late, and
may be delayed or dropped at busy times, and the minimum interval is 5 minutes. No exact execution
time is claimed. The NUC daemon removes the queue delay but is not deployed.

## Setup (local, dry-run)

```
python3 -m unittest discover -s tests          # all offline, no API cost
python3 scripts/ops_director.py demo --mode fixture   # FIXTURE responses, deterministic
python3 scripts/ops_director.py run-once --mode live  # read-only live probes, dry-run
python3 scripts/ops_director.py report
```
Set `TI_OPS_DB_PATH` to keep the database out of the checkout (default `var/`, git-ignored).

## Operation, pause, recovery, rollback

- `pause` / `resume` / `stop` / `cancel <id>`; `status` prints JSON; `healthcheck` checks the daemon heartbeat.
- **Recovery:** restart the process. Expired leases are recovered automatically; nothing needs hand editing.
- **Daemon loop:** `daemon --mode live` runs due responsibilities and a tick every 30 s; `stop` ends it.
- **Rollback:** nothing writes to `data.json`, Git, or any provider. Stop the process (or `docker compose
  down`), keep or delete the `ops_data` volume. Reverting this branch removes the code entirely.

## Docker and the NUC (prepared, not deployed)

`deploy/ops_director/Dockerfile` and `docker-compose.yml`: non-root user, read-only root filesystem,
dropped capabilities, `/data` volume, restart `unless-stopped`, heartbeat healthcheck. The repository
checkout is mounted read-only at `/repo` and must be refreshed (`git pull`) by the host, or the
snapshot goes stale; the report then shows the staleness. Secrets belong in an env file outside Git
(`TI_OPS_ENV_FILE`).

**Image built and smoke-tested** (21 October 2026, Docker 29.9.1, base `python:3.12-slim` pulled through
`mirror.gcr.io` because Docker Hub returned HTTP 429). It ran with `--read-only --cap-drop ALL
--security-opt no-new-privileges` as uid 10001. `init`, `run-once --mode fixture` (2 completed, 2 blocked
diagnostics) and `status` worked. `healthcheck` returned 1 without a daemon heartbeat, as designed. The
build found two defects, both fixed: the pricing file was not copied into the image (every paid call would
have been blocked with `pricing_unavailable`), and fixture mode looked for fixtures inside the image
instead of the mounted `/repo`. `run-once --mode live` from that container had no DNS, so every probe
returned `unknown_cause`, which proves nothing about the providers. The `docker-image` job in
`ops-director-tests.yml` repeats the build and smoke test on GitHub. **Not deployed.** Ubuntu Server
24.04 on an ASUS NUC 15 Pro is untested.

## Security

External feed content is data. It is never copied into instructions, task routing, budgets, or
evidence text; workers only touch hosts listed for their source, over HTTPS, with a byte cap and
timeouts; capabilities are checked per worker. Credentials come from environment variables and are
redacted from stored URLs. Publication has an interface that refuses in every configuration.

## Not built / limitations

The seven functional-flow roles as queue workers; batch and caching paths; an invoice import for confirmed costs; ECB rate fetcher (use `fx-set`); notifications; multi-host queue;
the queue on Actions (local-only by design); deployment; production verification; reading the current
`TI_MONITOR_PUBLISH` value. Spend made by any caller outside this
workflow with the same API key (for example a local `eval-monitor` run without the git backend) is not
in the shared ledger. Set a Console spend limit as well. Live checks from the build sandbox were blocked by its proxy
(HTTP 403 on tunnel) for GDELT and ADSB.lol, so they prove nothing about those providers.
