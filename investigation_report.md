# 03AA-04_Investigation_Verification_TI

Run at 2026-10-22 09:00 EEST (Cyprus time); stored timestamps are UTC. Mode: **FIXTURE REHEARSAL (recorded provider data, isolated target)**. Scope: TROPICAL_CYCLONE in jp, cn, GDACS alert Orange or higher.

## Upstream discovery

- Discovery signals (GDACS, `data/phase1_signals.json` generated 2026-10-22 07:35 EEST): ok; 1 items, 0 other hazard types, 0 below alert threshold, 0 outside the scope countries.
- Haiku-triaged leads: 0 stored. no triaged candidates are stored; triage has not completed (NOT_RUN_DRY_RUN) Paid triage is a separate agent's switch; this agent never changes it.
- **Intake path this run:** direct GDACS discovery signals (1 items) only; monitor/triage leads: 0. The monitor -> Haiku triage -> candidates chain is **not operating** (triage has not completed), so the full discovery chain is not connected end to end.

## This run

| Step | Count |
|---|---|
| New candidates taken in | 1 (deferred 0, reopened 0) |
| Tasks started | 1 (recovered from an interrupted run: 0) |
| Assessed | 1 |
| Passed the rules | 1 |
| Held (exceptions) | 0 |
| Not eligible (at intake 0, after evidence 0) | 0 |
| Unchanged on reassessment | 0 |
| Provider requests | 1 of 12 |
| Model calls / spend | 0 / none (ordinary code) |

## Publication

- Publication `pub-20261022T060000Z-172b64` to `ti-investigation-rehearsal-target`: added [], updated ['iv-tc-gdacs-1001338'], skipped [].
- Live verification: OK
- Recheck `pub-20261024T164847Z-16bf75`: superseded

## Assessed this run

- `cand-gdacs-TC-1001338` (CHAMPI-26): **PUBLISH** -> READY REPORTED/ALERT; evidence RETRIEVED (fixture, retrieved 2026-10-22 09:00 EEST Cyprus; provider last modified 2026-10-22T04:00:32 UTC; provider alert Red); claims: occurrence SUPPORTED, period SUPPORTED, location SUPPORTED, name SUPPORTED, intensity SUPPORTED, alert_level SUPPORTED, status SUPPORTED, impact MISSING

## Published events under reassessment

- `iv-tc-gdacs-1001338` REPORTED / ALERT: next check 2026-10-22 12:00 EEST

## Exceptions

- none open

## Candidate ledger

PUBLISHED: 1

