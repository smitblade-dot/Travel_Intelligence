# 03AA-04_Master_Agent_TI_Ops_Director

FORGEDTITANIUM LTD, Cyprus. Stream: 03 Agents & Automation. Substream: 03AA-04 Intelligence
Automation Agents. Business application: Travel Intelligence. Agent development, IP and costs belong
under 03AA-04. No agent number, organisational code, cost code or document number has been invented:
the cost code field is intentionally absent until the FT register supplies it.

## What this is, and is not

This work package delivers the **controller foundation, one working diagnostic worker, and (second
package) routing of the existing Haiku monitor through the spending gateway**. It is not the agent team.

**Workers, stated once.** Eight workers are registered. **One is implemented:** `feed_health_diagnostic`
(a diagnostic, not one of the seven flow roles). **The seven functional-flow roles are all unimplemented
as queue workers:** discovery, triage, investigation_content, verification, publication,
production_verification, daily_consolidation. Any task for them is blocked with
`worker_not_implemented:<name>`, never faked. Earlier handover text that said "six" was wrong. Some roles
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
- The existing 21:00 Europe/Nicosia refresh uses GitHub's `timezone:` cron field and a schedule gate.
  The Director reuses the same wall-clock rule and does not run a second consolidator.

## Monitor integration (work package 2)

`scripts/continuous_intelligence_monitor.py` no longer contains a direct Anthropic call. Its only paid
path is `MonitorSpend.triage()` -> `ModelGateway.run()`: input-size cap, dry-run check, per-model price
lookup, reservation against per-task, daily and monthly limits, call, settlement, then output validation.
Kept as before: URL dedupe via the `seen` cursor, the 36-hour freshness window, 12-headline batches,
the daily token caps, the 2,200 output-token cap, the 90 s timeout, the publish guards and the
instruction text.

**Behaviour change on merge (read before merging).** The gateway defaults to dry-run and has no limits, so
after merging, Haiku triage will not run until these repository variables are set: `TI_OPS_DRY_RUN=false`,
`TI_OPS_PER_TASK_LIMIT_USD`, `TI_OPS_DAILY_LIMIT_USD`, `TI_OPS_MONTHLY_LIMIT_USD` (all USD, all three
required). Until then each run discovers headlines, makes no model call, leaves the cursor where it is so
no headline is lost, records `lastTriageBlocked` in the state file and prints a workflow warning. The job
stays green, so watch the warning. A missing `ANTHROPIC_API_KEY` still fails the job when an allowance is set.

**Authoritative spend record on Actions** is `data/continuous_monitor_state.json` (`spend`, committed
each run by the single-writer workflow). Each run hydrates a throwaway SQLite ledger from it, so
reservations are still transactional within a run, then writes the totals back. Spend made before this
change is not in the ledger. Amounts are estimates from API usage fields and list prices.

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

**Output quality on Haiku 5.5 is not measured.** No paid testing was authorised. `eval-monitor` plus
`tests/fixtures/ops_director/monitor_eval_cases.json` is a replay harness whose cases and "model
outputs" are synthetic: the tests prove the harness and the code guards, not the model. Run
`eval-monitor` once with a small allowance before turning `TI_MONITOR_PUBLISH` on. One known limit is
recorded in the fixture: if a model wrongly returns `publish: true` for an injected headline, only the
existing code guards stand between it and an attributed, LOW-confidence EARLY_REPORT.

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
(`TI_OPS_ENV_FILE`). **The image has not been built** (no Docker daemon in the build environment);
`docker compose config` validates the file. Ubuntu Server 24.04 on an ASUS NUC 15 Pro is untested.

## Security

External feed content is data. It is never copied into instructions, task routing, budgets, or
evidence text; workers only touch hosts listed for their source, over HTTPS, with a byte cap and
timeouts; capabilities are checked per worker. Credentials come from environment variables and are
redacted from stored URLs. Publication has an interface that refuses in every configuration.

## Not built / limitations

The seven functional-flow roles as queue workers; batch and caching paths; an invoice import for confirmed costs; ECB rate fetcher (use `fx-set`); notifications; multi-host queue;
deployment; production verification. Live checks from the build sandbox were blocked by its proxy
(HTTP 403 on tunnel) for GDELT and ADSB.lol, so they prove nothing about those providers.
