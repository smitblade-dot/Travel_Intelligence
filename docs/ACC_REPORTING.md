# Optional reporting to the FORGEDTITANIUM Agent Command Centre (ACC)

FORGEDTITANIUM LTD, Cyprus. Stream 03 Agents & Automation, substream 03AA-04 Intelligence Automation Agents.
No agent number, organisational code, cost code or document number has been invented. Acceptance standard:
`docs/AGENT_BUILD_STANDARD.md`.

## What it is

An optional step that tells the ACC what each 03AA-04 agent did in its run, under its permanent code:

| Agent | Code | Workflow | What is reported |
|---|---|---|---|
| Master Agent (its gated Haiku monitor) | `03AA-04_Master_Agent_TI_Ops_Director` | continuous-intelligence-monitor.yml | the monitor's own outcome: triage result, dry run or live, model calls, spend |
| Country Intelligence Builder | `03AA-04_Country_Intelligence_Builder_TI` | country-intelligence-builder.yml | tasks, records published, exceptions held and needing a decision, model calls |
| Production Controller | `03AA-04_Production_Controller_TI` | production-controller.yml | verdict, mode, open exceptions, those needing a decision |

Each run sends two events: `working` when the run starts, then `idle`, `failed`, `unknown`, `offline` or
`awaiting_approval` from the agent's own outcome record. It reports agent activity only. It does not report
other workflows, jobs or repository events.

## Off by default; cannot change a run

- Nothing happens unless the repository variable `ACC_REPORTING_ENABLED` is exactly `true`. Until then the
  report steps are skipped and `TI_AGENT_OUTCOME_FILE` is empty, so agents run exactly as before (tested:
  identical exit codes, output, `GITHUB_OUTPUT`, job summary and state).
- When enabled, each agent writes one small JSON outcome record (`scripts/ti_ops_director/agent_outcome.py`).
  Writing never raises and never changes the agent's output, state or exit code.
- The report steps use `continue-on-error: true` and a 1-minute limit; `scripts/acc_report.py` always exits 0.
  A failed or unreachable ACC never turns a run red and never delays the agent.
- No report on pull requests (trials are not production activity, and secrets are unavailable there).
- Plain `http://` is refused except to the local machine; the secret is never printed.

## Outage buffer

If the ACC cannot be reached, reports are kept and offered again on the agent's next run. They are never
retried in a loop.
- **Order of work in a run.**
  1. The start step makes one attempt with a 3-second timeout. If it fails, the start event is written to a
     per-run pending file.
  2. After the agent has finished, the workflow restores the agent's buffer from the Actions cache
     (`acc-outbox-<agent>-*`).
  3. The end step adds the pending start, the end event and the buffered events, and makes **one** signed
     delivery attempt (8-second timeout).
  4. Whatever is still undelivered is saved back to the cache under this run's key.
- Every delivery attempt has one hard deadline (the same 3 or 8 seconds) covering DNS, connect, headers and body, so a
  service that hangs or drips bytes cannot hold the step open. Redirects are refused (the signed request goes only to the
  configured address) and replies over 256 KB are refused.
- **Bounded.**
  - At most 200 events, 72 hours and 24 attempts per event. Start events are dropped first.
  - The number dropped travels with the next delivered event (`delivery.dropped_before`) so the ACC can show
    it.
  - The Actions cache evicts unused entries on its own schedule; the 72-hour limit is enforced by the
    reporter.
- **No duplicates.** Events keep their run-based `event_id`. The ACC ignores repeats. Events the ACC accepted,
  already holds or permanently rejected are removed and never resent. Authentication or server errors keep
  events until the limits above.
- **Times.**
  - `timestamp` stays the original event time.
  - Buffered events add `delivery.queued_at_utc` and `delivery.attempts`.
  - The ACC records its own receipt time, so the dashboard can show both.
- **Integrity.** The buffer carries an HMAC tag made with the agent's ACC secret. A buffer that is altered,
  belongs to another agent or predates a key rotation is discarded, not sent. Requests are signed afresh at
  every delivery.
- **Content.** The buffer holds only the mapped ACC events (status, counts and enumerated reasons). It never
  holds the agent's outcome record, error text, output or secrets. Note: in a public repository, workflows from
  pull requests can read caches created on `main`, so the buffer is treated as public. It contains nothing
  that is not already in the run's public job summary.
- **Off means off.** With reporting off, or not configured, no buffer or pending file is created.

## Event mapping

| Agent | Outcome | ACC status |
|---|---|---|
| any | no outcome record, run succeeded | `unknown` |
| any | no outcome record, run failed or cancelled | `failed` |
| Master Agent | `NOT_RUN_DRY_RUN`, `COMPLETED`, `NO_NEW_HEADLINES`, budget pause | `idle` |
| Master Agent | `FAILED`, `NOT_RUN_NOT_CONFIGURED` (exit 3), state not persisted (exit 4) | `failed` |
| Builder | exit 0, no exception needs a decision | `idle` |
| Builder | exit 0, held exceptions need Marc's decision | `awaiting_approval` |
| Builder | exit 3, 4 or 5 | `failed` |
| Builder | mode off | `offline` |
| Controller | verdict in observe mode | `idle` |
| Controller | active mode with exceptions needing a decision | `awaiting_approval` |
| Controller | exit 3 or 4 | `failed` |

The following fields are set on every event:
- `dry_run`: monitor spend mode; builder `publish` is live, other modes dry; controller `active` is live,
  `observe` dry.
- `model_tier`: `code` when no model call was made.
- `cost_eur`: 0 when no model call was made. Real model spend is reported as `cost_usd_estimated`. It is
  converted to `cost_eur` only when `ACC_EUR_PER_USD` (and `ACC_EUR_RATE_DATE`) are set. A dry-run "would cost"
  estimate is never reported as spend.

## Configuration (Marc; not applied)

These settings live under Settings > Secrets and variables > Actions.

| Setting | Kind | Value |
|---|---|---|
| `ACC_REPORTING_ENABLED` | variable | `true` to enable; unset = off |
| `ACC_REPORT_URL` | variable | the ACC server's HTTPS address (needs hosting approval) |
| `ACC_KEY_ID_MASTER`, `ACC_KEY_ID_COUNTRY_BUILDER`, `ACC_KEY_ID_PRODUCTION_CONTROLLER` | variables | key ids issued by the ACC (`acc_admin.js issue-agent-key <code>`) |
| `ACC_AGENT_SECRET_MASTER`, `ACC_AGENT_SECRET_COUNTRY_BUILDER`, `ACC_AGENT_SECRET_PRODUCTION_CONTROLLER` | secrets | the matching key secrets; never commit |
| `ACC_EUR_PER_USD`, `ACC_EUR_RATE_DATE` | variables, optional | conversion rate for reported USD spend |

## Stages

| Stage | Status |
|---|---|
| Built | Yes: outcome records, reporter, gated workflow steps |
| Tested | 33 offline tests (`tests/test_ti_ops_director_acc_reporting.py`); full suite otherwise unchanged; end-to-end against a local ACC server in the ACC repository (synthetic events) |
| Merged | No: draft PR only |
| Configured | No: needs the ACC hosted at an HTTPS address and the settings above |
| Operational | No |

Cost: runtime AI none. Infrastructure: about two extra short steps per run once enabled (none while off).
