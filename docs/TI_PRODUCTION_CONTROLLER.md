# 03AA-04_Production_Controller_TI

FORGEDTITANIUM LTD, Cyprus. Stream: 03 Agents & Automation. Substream: 03AA-04 Intelligence Automation
Agents. Business application: Travel Intelligence. Development, associated IP and agent costs belong under
03AA-04. No agent number, organisational code, cost code or document number has been invented.

## What it is

The production controller verifies Travel Intelligence production and reports exceptions. It:

- detects delayed, missed, stalled and failed production runs;
- checks that the published `data.json` is valid, fresh and complete;
- verifies that GitHub Pages built the head of `main` and that the public site serves exactly those files;
- keeps one deduplicated diagnostic or recovery task per open exception, and resolves it when the condition clears;
- reports each run as a verdict, a check table, open exceptions and the actions to take.

It is ordinary code. It makes **no model call and no spend**. It never writes `main` or `data.json`, never
publishes, and never edits another workflow. Its only possible action is a bounded `workflow_dispatch` of a
workflow Marc has allow-listed; that is off by default.

**It is not a second controller.** It is the `production_verification` worker of the Master Agent
(`03AA-04_Master_Agent_TI_Ops_Director`), which was registered but unimplemented until now. It reuses the
Director's CLI (`scripts/ops_director.py production-check`), task schema and dedupe-key format
(`store.task_dedupe_key`), compare-and-swap state backend (`monitor_state.py`), scheduling helpers, text
sanitising, and the Haiku monitor's committed state for spending-control outcomes.

## Inspection findings (21 October 2026, read from GitHub and the repository, not from handovers)

| Item | Finding | Evidence |
|---|---|---|
| PR #27 (Master Agent) | Merged 21 Oct 12:17 UTC as squash `26e8321` | `merged_at` on the PR; commit on `main` |
| PR #28 (monitor early reports) | Merged 21 Oct 14:02 UTC as squash `60fb910` | `merged_at` on the PR; commit on `main` |
| PR #26 | Still open, draft | PR list |
| Runs automatically | Daily refresh (cron `0 21 * * *`, `timezone: Europe/Nicosia`); Haiku monitor (`*/15`); baseline build (`30 20 * * *` UTC); `pages-build-deployment` on every push to `main` | workflow files; Actions run history |
| CLI or test only | Ops Director queue, daemon, feed-health worker (no workflow runs the queue; NUC not deployed); `eval-monitor` | workflow files |
| Publication pipeline | Branch-based GitHub Pages from `main` (`pages-build-deployment`), custom domain `travelintelligence22.com` (`CNAME`). No Pages workflow in the repository | workflow list; `CNAME` |
| Monitor gateway at last run (17:24 UTC) | Dry-run; per-task, daily and monthly limits **UNSET** (null); `TI_MONITOR_PUBLISH` **UNSET** | `data/continuous_monitor_state.json` → `lastOutcome` |
| Monitor schedule delivery | 5 runs since 06:37 UTC, 2 of them scheduled (12:21, 17:24); about 45 were due | Actions run list |
| Daily refresh | 20 Oct scheduled run started 21:21 UTC for an 18:00 UTC (21:00 Nicosia) slot: 201.6 minutes late. Whether GitHub honoured `timezone:` or delayed the run is **UNVERIFIED** until the controller observes more slots | run 39499702808 |
| Daily refresh conclusion | Last two runs red, but **after** the safety-net commit: only "Audit discovery feed health" failed (GDELT unavailable, ADSB.lol degraded). The data was published | jobs of runs 39499702808, 39600080368 |
| Baseline build | Failed on each of the last 6 nights; every generate job fails at `upload-artifact` (no batch file written). `scripts/audit_baseline_coverage.py` has a syntax error, so the coverage gate always reports "needed" | run 39513877171; `python3 scripts/audit_baseline_coverage.py` |
| Baseline coverage | 183 of 728 country/category combinations | computed from `data.json` |
| Public site from the build sandbox | Unreachable (proxy refuses the tunnel). The release check must run on Actions | `curl` |
| Repository access | Public repository; this session has push and admin | GitHub API |

## Checks

All thresholds are in `config/production_controller.json` (reviewed by pull request). Times are UTC
internally; Nicosia time is shown alongside.

**Workflows** (runs on `main`, events `schedule` and `workflow_dispatch` only):

| Workflow | Expected | Delayed (warning) | Missed (critical) | Stalled (critical) | Other |
|---|---|---|---|---|---|
| Daily refresh | 21:00 Europe/Nicosia (18:00 UTC summer, 19:00 UTC winter) | not started 60 min after the slot, or started more than 60 min late | no run 180 min after the slot | running more than 75 min | gate skipped the `refresh` job (critical); failed before `Safety-net commit and push` (critical, re-runnable); failed after it (warning: data published, a later step failed) |
| Haiku monitor | every 15 min | last success more than 60 min ago | last success more than 180 min ago | running more than 15 min | latest run failed (critical); fewer than 50 percent of scheduled runs in 24 h (warning) |
| Baseline build | 20:30 UTC | 240 min | 360 min | 120 min | failed (critical, never auto-dispatched: Sonnet cost) |

A slot still inside its grace period is not judged; the previous slot is judged instead, so a miss is not
forgotten at midnight. A later successful manual run for the same slot clears a failed scheduled run.

**Outputs** (the checkout of `main`): `validate_ti_data.py` passes (critical); `meta.generated` older
than 27 h (warning) or 51 h (critical); `meta.counts` and `meta.currentIntelligence` equal the data
(critical); no collection shrinks more than 10 percent against the last good run, and at least 52 countries
(critical); `oilGasSummary` not empty (critical); baseline country/category gaps (warning, CLAUDE.md calls
them a build defect); discovery evidence older than 30 h (warning).

**Spending-control outcomes** (the Haiku monitor's committed state; no paid call): configuration defect
(critical); 2 consecutive `FAILED` runs (critical); budget exhausted (warning); dry-run with untriaged
headlines (info: a configuration choice, not a fault); spend reservations unsettled for over 60 min (warning).

**Release**: the Pages build is not `errored` (critical); Pages built the head of `main` within 20 min of
the commit (critical); the public `data.json` and `index.html`, fetched with a cache-busting query and
`Cache-Control: no-cache`, are byte-identical (SHA-256) to those files at the built commit (critical after
the 15 min CDN window, info inside it); the exact legal footer is present (critical); the site answers
(critical).

**Controller**: if the controller itself did not run for 180 min, it says so (warning).

A check that cannot run is **UNVERIFIED**. It is never read as healthy, the verdict shows UNVERIFIED, and
open tasks under that check are kept, not resolved.

## Tasks, deduplication and recovery

One task per dedupe key (for example `workflow:daily_refresh:failed`, `release:stale_content`). A repeat
of the same exception updates that task (`occurrences`, `last_seen_utc`, latest evidence); a worse severity
escalates it; when the condition is no longer observed it is completed with `resolution`, and a later
recurrence opens a new task, so history is kept. Resolved tasks are kept 30 days; run history keeps 200 runs.

Tasks use the Director's fields (`id`, `dedupe_key`, `task_type` `production_diagnostic` or
`production_recovery`, `worker` `production_recovery`, business application, substream, `state`
`blocked`/`completed`, `requires_human`, `blocking_reason` = the action). The `production_recovery` role is
registered as a planned worker: its tasks are blocked for Marc, never faked.

**Recovery dispatch** is the only automatic action. Conditions: mode `active`; the workflow is listed in
`TI_PC_RECOVERY_WORKFLOWS` and marked `dispatchable` in the config (daily refresh, monitor; never the
baseline build); the exception is critical and of a re-runnable kind (missed, failed before output, gate
skipped); no run of that workflow is in progress; at most `TI_PC_MAX_DISPATCHES_PER_DAY` (default 1, hard
ceiling 3) per workflow per UTC day. The dispatch is recorded on the state branch **before** a separate job
with `actions: write` performs it, so a failed dispatch is over-counted, never repeated without limit. That job (`ops_director.py production-dispatch`) re-checks every planned dispatch against the
allow-list and the production branch before calling `gh workflow run`, and refuses anything else. A
post-output failure is never re-dispatched: the data is already published and a re-run would repeat the
Claude refresh for nothing.

## Where it runs and where state lives

**GitHub Actions** (`.github/workflows/production-controller.yml`): hourly at minute 7, after every
completed daily refresh or baseline build (`workflow_run`), and by manual dispatch. One run at a time
(`concurrency`). Python standard library only; no secrets other than the job's own `GITHUB_TOKEN`.

State is `production_controller_state.json` and the latest `production_controller_report.md` on the branch
named by `TI_PC_STATE_BRANCH`, written by compare-and-swap commits (the monitor's backend). The branch is
created as an orphan on first use and holds only these two files. Commits there never touch `main`, so they
never trigger a Pages build or the push-triggered regression workflow. `main` is refused as a state branch.

**Master Agent queue** (one host; the NUC is out of scope and not deployed): `production_verification`
tasks run the same checks, keep the ledger in `var/production_controller/` beside the queue database
(`TI_PC_STATE_DIR`), and mirror each open exception into the queue as one blocked task, cancelled when it
resolves. The queue worker is read-only (GET only, hosts `api.github.com` and the site) and never
dispatches. Queue scheduling is off unless `production_check_interval_minutes` is set in the Director config.
`python3 scripts/ops_director.py report` has a "Production controller" section.

## Controls and exit codes

| Mode (`TI_PC_MODE`) | Scheduled runs | Writes | Alerts | Dispatch |
|---|---|---|---|---|
| unset / `off` | job skipped | none | none | none |
| `observe` | checks and job summary | **none**: never creates or writes the state branch; reads it only if it already exists | annotations marked "detected, not recorded"; job stays green | none |
| `active` | checks, summary, state branch | state branch only | job red (exit 1) only when an exception is **newly opened or escalated to critical** | only if allow-listed |

**Observe-mode report semantics.** An observe run detects exceptions but records nothing. Its report is
headed "Exceptions detected by this run", states that no task, incident, state change or dispatch was
recorded, shows no task identifiers for new detections, and marks each one "not recorded (observe mode)".
If a persisted ledger already exists (from earlier active runs), each detection shows whether that ledger
holds it as an open task, and the report says what an active run would have opened or resolved, without
changing the ledger. Only active runs open, escalate or resolve tasks.

A manual run with the mode unset is one read-only observation. Pause = `observe`. Stop = `off`, or disable
the workflow in the Actions tab (immediate, overrides everything). Exit 3 = configuration error (invalid
mode, active without a state branch, bad numbers): nothing runs. Exit 4 = state could not be persisted.
A standing fault is reported every run but turns the job red once, so GitHub's failure e-mail is not
repeated hourly.

## Verification performed

- 60 offline tests (`tests/test_ti_ops_director_production.py`): every check and threshold, DST, the
  measured 21 October patterns, deduplication, escalation, resolution and reopening, UNVERIFIED handling,
  bounded dispatch, exit codes, the real-git state branch against a local bare remote (main untouched), the
  queue worker through the Director controller, capability refusal, and the workflow's permissions. The
  existing 179 tests still pass; the fixture demonstration is unchanged.
- Live read-only observation from the build sandbox against production (21 Oct, 17:54 and 17:58 UTC):
  workflow and output checks ran on real data; the Pages API and the public site were not reachable from
  the sandbox, so the release check reported UNVERIFIED as designed.
- The `production-observe` job in "TI Ops Director tests" runs the same observation on GitHub with a token
  on every relevant PR, including the release check against `travelintelligence22.com`.

## Not built / limitations

- No e-mail, Slack or issue notifications: alerts are job annotations, the job summary, GitHub's own failed-run
  e-mail (active mode, new criticals only) and the report on the state branch.
- The controller is itself scheduled by GitHub (best effort). `workflow_run` covers the refresh and
  baseline build promptly; its own gaps are reported as `controller:gap`.
- It does not fix the defects it finds (the baseline audit syntax error, discovery feed outages).
- The release check compares files, not rendered pages; it does not test the UI.
