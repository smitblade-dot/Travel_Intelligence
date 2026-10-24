# 03AA-04_Investigation_Verification_TI

FORGEDTITANIUM LTD, Cyprus. Stream: 03 Agents & Automation. Substream: 03AA-04 Intelligence Automation Agents. Business
application: Travel Intelligence. Development, associated IP and agent costs belong under 03AA-04. No organisational code, cost
code or document number has been invented. The agent name follows the existing `03AA-04_<Name>_TI` convention; the Agent
Command Centre register lists this role as "Intelligence Analyst & Publisher" with **no code assigned** (see "Register and
reporting"). Acceptance standard: `docs/AGENT_BUILD_STANDARD.md`.

## What it is

TI is an automated service governed by evidence rules and spending controls. This worker takes discovery candidates, retrieves
their evidence from permitted sources, assesses **each claim on its own**, and publishes what qualifies through TI's established
automatic publication path. Routine qualifying content never waits for a person. Anything the rules cannot resolve is held with
its reasons, and published events keep being reassessed.

It is the `investigation_verification` worker of the Master Agent (`03AA-04_Master_Agent_TI_Ops_Director`) and also runs on
GitHub Actions. It fills the *investigation* half of the Master Agent's planned `investigation_content` role (only
`content_building` stays planned) and does its own reassessment, so the generic planned `verification` role is left untouched.
It reuses, and does not duplicate: the Director's task schema and dedupe-key format, the spending gateway
(`MonitorSpend` -> `ModelGateway`), the compare-and-swap state backends, the Country Builder's publication primitives (commit
backend, canonical validation, live-site poll, "release is as new as ours" test, change_log and meta upkeep), the Production
Controller's site address, the source register `config/source_registry.json`, the Country Builder's robots.txt reader, and the
optional ACC outcome record.

## Inspection findings (24 October 2026, read from `main`, the workflows and the code, not from handovers)

| Area | Finding |
|---|---|
| Discovery and Haiku triage | The monitor runs every 15 minutes but triage is **disabled**: committed state shows `NOT_RUN_DRY_RUN`, limits `null`, 20 consecutive untriaged runs. **Before this change it kept no candidate content at all**: decisions were only printed to the log, existing-event leads were printed and dropped, and the 12 pending headlines were not stored. There was nothing for a worker to read. |
| Master Agent queue | One SQLite file on one host. **Nothing services it**: no workflow runs it, the NUC daemon is not deployed, and Actions runner disks are ephemeral. The CLI existing does not mean the queue is serviced. |
| Spending gateway | Works and is shared (`MonitorSpend`); every paid path needs its own limits. The monitor's are unset. This agent's are unset. |
| Country Builder | Record-oriented, TIER_1 sources only; it holds the established publication path but has no event publication. |
| Publication and release | Branch-based GitHub Pages from `main`; the Production Controller verifies it in `observe` mode. The Pages queue can lag (observed 35 minutes on 22 Oct), so slow is not wrong. |
| Agent register / ACC | The ACC register has no coded entry for this role; ACC reporting is merged but off (`ACC_REPORTING_ENABLED` unset, no hosted ACC). |
| Pending candidates that actually exist | (1) GDACS alert items in the discovery snapshot (`data/phase1_signals.json`, 86 items). (2) 21 DRAFT events in `data.json`, which were deliberately held by earlier QA (sensitive allegations, legal review) and are **not** this agent's to publish. (3) No triaged headline leads. |
| Access from the build session | `git push` to the feature branch works; the sandbox cannot reach GDACS, news, gov.uk or the public site (proxy 403), only GitHub. Repository variables cannot be read or set from it. Live retrieval and live-site verification therefore run on GitHub Actions. |

**Dependency on paid triage, stated plainly.** The Haiku-triaged lead producer is wired and tested but produces nothing while
triage is off. This agent does not enable it. The first live proof uses the deterministic GDACS discovery signals instead and is
labelled as such everywhere. A demonstration fed by recorded data (the rehearsal job) is labelled **FIXTURE** in the report and in
the stored observations and is never described as live discovery.

## First scope (chosen from the actual pending candidates and permitted sources)

- **Event type:** tropical cyclone, GDACS alert Orange or higher. **Region:** Japan and China (tracked countries).
- **Why:** the only current, relevant candidate in the discovery snapshot is a tropical cyclone (CHAMPI-26, Japan, GDACS Red), and
  GDACS is the one registered source whose terms and rights are already reviewed (`gdacs-ecjrc`, register `gdacs-alerts`,
  automation YES). Earthquakes are implemented and tested but disabled (`scope.event_types`). USGS and EMSC would corroborate
  earthquakes independently, but neither is in the source register and robots/terms could not be verified from the build
  session, so neither is used or registered (see "Decisions needed").
- **Honest limits of this scope:** every event rests on **one reporting chain** (GDACS), so it is `REPORTED` with the single-chain
  caveat, never `CONFIRMED` (GDACS' own terms say its cyclone and earthquake notices are automatic and not human-reviewed).
  Impact on travellers is **not** established by any source here and is published as "no verified information", never as "no impact".

## Configuration (established in this session)

Repository variables cannot be read from the build session, so any `TI_IV_*` variable is **UNVERIFIED (inaccessible)** and is
treated as unset. Every operating value is committed in `config/investigation_verification.json`; variables are overrides only.

| SETTING | EXACT CONFIG NAME | CURRENT VALUE | RECOMMENDED VALUE | EFFECT | OWNER |
|---|---|---|---|---|---|
| Mode | `operation.mode` (override var `TI_IV_MODE`) | `publish` (committed; var UNVERIFIED) | `publish` | `off` stops; `observe` plans only; `draft` assesses and keeps the ledger; `publish` also publishes | Marc authorised automatic publication; build applied |
| Publication switch | var `TI_IV_PUBLISH` | UNSET (var UNVERIFIED) | leave unset | exactly `false` stops publication only; anything else changes nothing | Marc |
| Event types | `scope.event_types` (var `TI_IV_EVENT_TYPES`) | `TROPICAL_CYCLONE` | same until proved, then add `EARTHQUAKE` | which hazards are taken in | build, expansion by Marc |
| Countries | `scope.countries` (var `TI_IV_COUNTRIES`) | `jp`, `cn` | same until proved | tracked countries in scope | build |
| Alert threshold | `scope.min_alert_level` | `Orange` | `Orange` | GDACS alert level needed to take a candidate in | build |
| Sources | `sources.gdacs` | GDACS (register `gdacs-alerts`, TIER_2, ACTIVE, automation YES), host `www.gdacs.org` only | same | only registered, allow-listed hosts are contacted | build |
| Freshness | `freshness.*` | event activity within 72 h; end date at most 7 days ahead; republish gap 7 days; reassess ALERT 3 h, CURRENT 24 h; archive after 336 h | same | an old event republished as new, or a stale one, is ineligible | build |
| Trigger | workflow `schedule` + `workflow_dispatch` | on `main` only | same | PRs and other refs are trials | build |
| Frequency | `schedule.cron_utc` | `17 */2 * * *` (every two hours, UTC) | same | GitHub may delay runs; Cyprus clock times move by one hour at the winter change (25 Oct 2026) without any change here | build |
| Batch size | `scope.batch_size_tasks_per_run` (var `TI_IV_BATCH_SIZE`) | 3 (max 10) | 3 | tasks per run | build |
| New candidates per run | `scope.max_new_candidates_per_run` | 25 | 25 | intake bound | build |
| Concurrency | workflow concurrency group + ledger lease `retries.lease_minutes` | 1 run at a time; 20 min lease; sequential retrieval | same | overlapping runs do nothing | build |
| Evidence limits | `evidence_limits.*` | 12 requests/run, 3/task, 2 MB/response, 20 s timeout, 2 attempts, 2 redirects (allow-list only), 600 s run bound | same | per-task worst case is 3 x 20 s x 2 = 120 s | build |
| Retry limits | `retries.*` | 3 task attempts; conflict holds retry 4 times, collection failures 6 times, then become a decision | same | no unbounded loop; the event ages out first | build |
| Model routing | `spending.*` (gateway) | code only; Haiku default; Sonnet needs a logged reason and `sonnet_escalation_enabled` (false); Opus refused | same | no model call happens at all in this scope | build |
| Spending allowances | `spending.per_task/daily/monthly/total_limit_usd`; vars `TI_IV_*_LIMIT_USD`; `TI_IV_DRY_RUN` | all UNSET (`null`); dry-run true | unchanged: none needed | paid execution is blocked; the earlier evaluation allowance does not apply | Marc (only if a model path is ever wanted) |
| Persistence | `operation.state_branch` | `ti-investigation-state` (orphan branch, never main) | same | ledger, report and leases by compare-and-swap | build |
| Publication eligibility | `publication.*`, `claim_rules.*` | see "Rules" | same | rule-passing events publish automatically | Marc authorised |
| Publication verification | `publication.verification.*` | 900 s first poll, 6 rechecks of 120 s, rollback only on demonstrated wrong content | same | slow deployment is pending, never deletion | build |
| Pause / stop | CLI `pause`, `stop`, `resume`; `TI_IV_MODE=off`; disable the workflow; Master Agent `stop` | all inactive | n/a | each stops processing; `off`/disable is immediate | Marc |
| ACC reporting | `ACC_REPORTING_ENABLED`, `ACC_KEY_ID_INVESTIGATION_VERIFICATION`, secret `ACC_AGENT_SECRET_INVESTIGATION_VERIFICATION` | UNSET / not issued (disabled) | leave off until the ACC is hosted and a code is assigned | agent activity reporting only | Marc |
| Upstream paid triage (not this agent's) | `TI_OPS_DRY_RUN`, `TI_OPS_*_LIMIT_USD` | dry-run, limits `null` (from the monitor's committed state) | Marc's decision | while off, no headline leads exist | Marc |

## The process (and where each step lives)

1. **Read an eligible candidate** (`investigation_candidates.py`): GDACS signal items and, when triage runs, stored leads. Cheap
   scope checks (type, alert, tracked country, stated dates) run first; an out-of-scope or stale lead costs no request.
2. **One deduplicated task** per candidate and window, in the Director's task schema and `task_dedupe_key` format, in the ledger.
   A task of an earlier window that is still open blocks a twin. Same candidate + same inputs = same task.
3. **Bounded evidence** (`investigation_sources.py`): URLs are built from configured templates and validated numeric ids, never
   from a candidate's URL; https and allow-listed hosts only; redirects only inside the allow-list; robots.txt honoured (unreadable
   = not permitted); source must be in the register with an allowed tier, status and automation; request, size and time bounds.
4. **Grouping:** a headline lead is attached to the hazard it is about (same type and tracked country, report inside the event's
   activity window). Attached leads are recorded as `ATTRIBUTION_UNVERIFIED`, never count as an independent chain and never reach
   public text. Unmatched leads wait, then expire without an exception.
5. **Claims separated** (`investigation_assess.py`): occurrence, period, location (core), intensity, alert level, name, status,
   impact. The provider's alert level is published only as the provider's own classification, never as a TI finding.
6. **Assessment:** support, dates, relevance, independence and conflicts, per claim (statuses below).
7. **Schema-valid event with claim-level provenance** (`investigation_build.py`): every published claim maps to its observation
   (`claimIds`, `claimAssessments`); exactly one primary target per observation; original-language and translation fields set.
8. **Existing publication rules** (`investigation_publish.py`): compare-and-swap commit to `main`, canonical validation, change_log and
   meta upkeep, only events this agent published are ever replaced (previous version kept in the change_log entry; earlier
   observations stay as provenance).
9. **Automatic publication** from a scheduled or manual run on `refs/heads/main` only.
10. **Release verification:** the public `data.json` is fetched with a cache-busting query until it serves the event, its sources and
    observations. Slow = `VERIFICATION_PENDING`, rechecked on later runs (bounded). Rollback only if the site is serving a release
    at least as new as ours yet not our content. After a rollback the candidate is held until its signal changes: no recovery loop.
11. **Exceptions** are held with reasons. `retry` class clears itself; `decision` class (robots refusal, source not permitted,
    unresolved verification, rollback trouble, retries exhausted) is the only thing a person is asked about. Each is mirrored into
    the Master Agent queue as one deduplicated blocked task (`investigation_exception`) when a host services the queue.
12. **Report:** job summary and `investigation_report.md` on the state branch (Cyprus time, counts, publication, exceptions, ledger).

### Claim statuses

SUPPORTED, CONFLICTING, STALE, MISSING, COLLECTION_FAILURE, ATTRIBUTION_UNVERIFIED, EXCLUDED_UNTRUSTED. A quotation or value that
matches the source does not by itself make a claim SUPPORTED: dates, scope, internal consistency (value against the provider's own text
and class label), plausibility range and relevance are checked. Nothing is inferred from silence.

### Rules (`config/investigation_verification.json`, `claim_rules`)

There is deliberately no universal source-count rule. Per claim type: **occurrence** needs one retrieved, dated, register-backed
in-remit source whose event id and type match what was asked for; **period** needs a stated start not in the future and ordered
before the end; **location** needs a tracked country the source names, with a name that passes the text allowlist; **intensity** needs a
numeric value in range that agrees with the source's own text and class label (otherwise only that claim is excluded and the event
still publishes on its core claims); **alert level** is attributed; **impact** needs an in-remit statement, none exists, so it is recorded as
missing. Status: one independent chain `REPORTED`; two or more `CORROBORATED`; ended events `RESOLVED`; GDACS is never the basis of `CONFIRMED`.
Freshness is judged from the event's own activity dates, never from fetch time or the provider's re-save date; an old event re-saved
recently is `old_event_republished_as_new` and ineligible.

## Persistence, restart and duplicates

The ledger is on its own branch. A run takes a lease (20 minutes); a second run during a live lease does nothing. Tasks left
`running` by a killed run are recovered once the lease expires. An event that reached `main` but not the ledger (killed between push and
ledger write) is **adopted** by the next run and verified, never published twice. A publication intent is recorded before the push.
Identical content (hash) is never rewritten; an event the daily refresh already represents (same GDACS id) is never duplicated; an event
this agent does not own is never overwritten.

## Reassessment

Published events carry a next-check time (ALERT 3 h, CURRENT 24 h). Unchanged evidence costs one request and no write to `main`. Changed
evidence updates the event (history kept). A source outage, malformed answer or conflicting new evidence **keeps the last valid content**
and raises a retry exception. Ageing moves the lifecycle (ALERT, CURRENT, ARCHIVED after 336 h) and never unpublishes. Archived events
are no longer checked.

**Externally edited events (`STOOD_DOWN`).** Every published event carries a hash of the content the agent wrote (`provenance.contentHash`).
Before reassessing, the agent recomputes that hash from the live event in `data.json`. If it no longer matches, someone or something else
(a person, the nightly reviewer) edited the event: the agent keeps the edit, stops reassessing that event (ledger state `STOOD_DOWN`, no
next check), raises one `decision` exception and never overwrites it. The decision for the owner is either to leave the edited event as it is,
which needs no action and leaves the exception open until acknowledged in the ledger, or to have the edit reverted. The stand-down is permanent by design: the open `decision` exception, shown in every report until it is resolved, is the
only prompt, so an owner who leaves it unanswered also leaves that event un-reassessed. An event that disappears from
`data.json` is treated the same way (`published_event_missing_from_data`, decision, throttled, never silently re-published).

## Security

Provider text is untrusted data: it passes an allowlist and an instruction-pattern screen, never becomes TI prose (public text is a
fixed template over validated fields), and cannot change routing, status, permissions or actions. Candidate URLs are never fetched.
Permissions: `contents: write` only in the jobs that need it; production runs from `main` only; trials write only their own branches;
the workflow contains no model key. No secret is committed.

## Models and cost

Ordinary code does everything in this scope: **0 model calls, EUR 0**. A gated model step for narrative evidence exists
(`investigation_model.py`) behind the spending gateway (reservation before the call against shared per-task, daily, monthly and total
limits; cached results; Haiku default; Sonnet only with a logged reason; Opus refused; dry-run by default) and code accepts a proposed
claim only if its quotation is verbatim in the retrieved passage. **No engine path reaches it** and its accuracy is **not measured**; tests use
scripted replies, which establish integration behaviour only. Using it would need a defined, capped allowance from Marc. Infrastructure:
a short GitHub Actions job every two hours (free in a public repository at this size).

## Register and reporting

The ACC register entry for this role ("Intelligence Analyst & Publisher", `ti-analyst-publisher`) has `code: null`. The agent name here follows
the `03AA-04_<Name>_TI` convention, but the **ACC must assign or confirm the code** and issue a key before reporting can be enabled. The reporter
(`scripts/acc_report.py`) and the workflow steps are in place, off by default. The Master Agent's report shows the worker as implemented.

## Controls and exit codes

`python3 scripts/investigation_worker.py run | plan | status | pause | resume | stop | route-exceptions | rollback <publicationId> --by NAME`.
Exit 3 configuration error; 4 ledger could not be read or persisted; 5 a publication needs attention (refused, push failed, content
demonstrably incorrect, rollback unresolved, verification unresolved after bounded rechecks). A pending deployment and held exceptions exit 0.

## Known limits

- One reporting chain per event in this scope; no impact claims.
- Earthquake corroboration (USGS/EMSC) needs register entries and a robots/terms review that could not be done from the build session.
- The multi-country event has one primary country; a second tracked country does not get its own event.
- The Master Agent queue is still not serviced anywhere; exceptions live in the ledger and are mirrored only when a host runs the queue.
- The discovery snapshot is refreshed by the daily refresh; a stale snapshot delays discovery, not evidence freshness.
- `reviewDate` of an unchanged event is not extended; it moves when the event is updated.
