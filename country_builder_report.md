# 03AA-04_Country_Intelligence_Builder_TI

Run: 22 Oct 2026 23:41 Cyprus time | mode: publish | automatic publication: ON | paid model use: OFF (awaiting_paid_authorisation)

Coverage (whole product): 187/728 combinations; gaps in scope: 0. Tasks this run: 0; new 0; reopened 0; duplicates suppressed 0; interrupted tasks recovered 0.

Spend recorded (all time, USD): 0 (estimated from API usage fields x configured list prices; not invoice-confirmed).

## Publication

- Nothing passed the rules in this run (see the exceptions below).

## Exceptions held (routed to the Master Agent)

- [automatic retry] gh/EQUIPMENT/MOSQUITO: no_dated_source_within_freshness_limit. Held until a registry-backed source states a publication date inside its freshness limit. No action unless it persists. (seen 2x since 22 Oct 2026 22:48 Cyprus time)
- [NEEDS DECISION] gh/EQUIPMENT: source_blocked_by_robots. Source cannot be used as configured: robots.txt could not be read (HTTP 403); treated as not permitted. Needs Marc's decision (robots exception or another source). (seen 1x since 22 Oct 2026 23:20 Cyprus time)
- [automatic retry] gh/EQUIPMENT/PLUGS: evidence_missing. Held: plug_type: source not retrieved (not published: no source of the required standing states it). Retried automatically each run; source problems are listed separately. (seen 1x since 22 Oct 2026 23:20 Cyprus time)
- [NEEDS DECISION] gh/EQUIPMENT: source_blocked_by_robots. Source cannot be used as configured: robots.txt could not be read (HTTP 403); treated as not permitted. Needs Marc's decision (robots exception or another source). (seen 1x since 22 Oct 2026 23:20 Cyprus time)
- [automatic retry] gh/EQUIPMENT/PLUGS: evidence_missing. Held: voltage: source not retrieved (not published: no source of the required standing states it). Retried automatically each run; source problems are listed separately. (seen 1x since 22 Oct 2026 23:20 Cyprus time)

## Awaiting budget authorisation (model-written topics; not exceptions)

- none

## Scope

- Validation scope: gh / ENVIRONMENT, TRANSPORT, EQUIPMENT. Expansion: OFF (needs confirmation of coverage and budget); proof gate: MET (13 record(s) verified live (need 3), 0 rollback(s) (max 0), 3 clean publication run(s) (need 2))

## Records published by this agent

- pub-20261022T142741Z-1dfa3c: gh-environment-flooding-natural-hazards, gh-transport-road-travel, gh-transport-taxis-and-public-transport, gh-transport-air-travel-and-gateways, gh-equipment-clothing, gh-equipment-mosquito | commit 6465d45e349cedc2a37c929153d24470b0a5a0a7 | verified live 22 Oct 2026 17:36 Cyprus time
- pub-20261022T181139Z-21fa3f: gh-environment-climate-seasonal, gh-environment-flooding-natural-hazards, gh-equipment-clothing, gh-equipment-plugs | commit None | verified live 22 Oct 2026 22:22 Cyprus time
- pub-20261022T202028Z-a48d6e: gh-equipment-clothing, gh-equipment-mosquito, gh-equipment-plugs | commit 136492e432f18448c753cbb6012e8e1761597d66 | verified live 22 Oct 2026 23:21 Cyprus time

## Drafts and assessments held on this branch

- gh/EQUIPMENT: cib-gh-equipment-a48d6e4474 | AUTO_PUBLISHED_AND_VERIFIED | model: NOT_NEEDED | claims 27 | https://github.com/smitblade-dot/Travel_Intelligence/blob/ti-country-drafts/drafts/gh/EQUIPMENT/cib-gh-equipment-a48d6e4474.json
- gh/EQUIPMENT: cib-gh-equipment-3a0cda8cc9 | ELIGIBLE_AWAITING_PUBLICATION_RUN | model: NOT_NEEDED | claims 29 | https://github.com/smitblade-dot/Travel_Intelligence/blob/ti-country-drafts/drafts/gh/EQUIPMENT/cib-gh-equipment-3a0cda8cc9.json | superseded by cib-gh-equipment-a48d6e4474
- gh/ENVIRONMENT: cib-gh-environment-21fa3f4dcf | AUTO_PUBLISHED_VERIFICATION_PENDING | model: NOT_NEEDED | claims 9 | https://github.com/smitblade-dot/Travel_Intelligence/blob/ti-country-drafts/drafts/gh/ENVIRONMENT/cib-gh-environment-21fa3f4dcf.json
- gh/EQUIPMENT: cib-gh-equipment-e087b56a8f | AUTO_PUBLISHED_VERIFICATION_PENDING | model: NOT_NEEDED | claims 29 | https://github.com/smitblade-dot/Travel_Intelligence/blob/ti-country-drafts/drafts/gh/EQUIPMENT/cib-gh-equipment-e087b56a8f.json | superseded by cib-gh-equipment-3a0cda8cc9
- gh/EQUIPMENT: cib-gh-equipment-3de3313bcd | HELD_EXCEPTIONS_ROUTED | model: NOT_NEEDED | claims 28 | https://github.com/smitblade-dot/Travel_Intelligence/blob/ti-country-drafts/drafts/gh/EQUIPMENT/cib-gh-equipment-3de3313bcd.json | superseded by cib-gh-equipment-e087b56a8f
- gh/TRANSPORT: cib-gh-transport-21272f0ba2 | AUTO_PUBLISHED_AND_VERIFIED | model: NOT_NEEDED | claims 31 | https://github.com/smitblade-dot/Travel_Intelligence/blob/ti-country-drafts/drafts/gh/TRANSPORT/cib-gh-transport-21272f0ba2.json
