# 03AA-04_Country_Intelligence_Builder_TI

Run: 23 Oct 2026 13:28 Cyprus time | mode: publish | automatic publication: ON | paid model use: OFF (awaiting_paid_authorisation)

Coverage (whole product): 187/728 combinations; gaps in scope: 1. Tasks this run: 1; new 0; reopened 1; duplicates suppressed 0; interrupted tasks recovered 0.

Spend recorded (all time, USD): 0 (estimated from API usage fields x configured list prices; not invoice-confirmed).

## Publication

- Already published and unchanged (duplicate prevented): gh-equipment-clothing, gh-equipment-mosquito

## Ghana / EQUIPMENT: COMPLETED (assessed)

- Gap addressed: RECHECK_HELD: Held EQUIPMENT items for Ghana were assessed under earlier rules; assessed again.
- Evidence: cdc_travel=RETRIEVED, travelhealthpro=RETRIEVED, fcdo_health=RETRIEVED, fcdo_safety=RETRIEVED, gsa_plugs=UNAVAILABLE (http_error:404), li_1935=RETRIEVED, grid_code=RETRIEVED, ecg_technical=RETRIEVED
- Cost this task: $0.00 (no model call)
- CLOTHING: **PUBLISH**, confidence MEDIUM, 7 claim(s)
- MOSQUITO: **PUBLISH**, confidence MEDIUM, 8 claim(s)
- PLUGS: **HOLD** (source_unavailable_preserving_last_known_good)
- Draft: cib-gh-equipment-00acaa0f49 | status **AWAITING_PUBLICATION_DECISION** | publication NOT_PUBLISHED | model: NOT_NEEDED
- Link: https://github.com/smitblade-dot/Travel_Intelligence/blob/ti-country-drafts/drafts/gh/EQUIPMENT/cib-gh-equipment-00acaa0f49.json

### Clothing  [CLOTHING] PUBLISH
- PUBLISHABLE: Prepare for activities by wearing the right clothes and packing protective items, such as bug spray, sunscreen, and a basic first aid kit.  _[US Centers for Disease Control and Prevention (CD.45)]_
- PUBLISHABLE: Heat-related illness, such as heat stroke, can be deadly. Eat and drink regularly, wear loose and lightweight clothing, and limit physical activity during high temperatures.  _[US Centers for Disease Control and Prevention (CD.46)]_
- PUBLISHABLE: Protect yourself from UV radiation: use sunscreen with an SPF of at least 15, wear protective clothing, and seek shade during the hottest time of day (10 a.m.–4 p.m.).  _[US Centers for Disease Control and Prevention (CD.46)]_
- PUBLISHABLE: Very cold temperatures can be dangerous. Dress in layers and cover heads, hands, and feet properly if you are visiting a cold location.  _[US Centers for Disease Control and Prevention (CD.47)]_
- PUBLISHABLE: To prevent infections, wear shoes on beaches where there may be animal waste.  _[US Centers for Disease Control and Prevention (CD.48)]_
- PUBLISHABLE: Dress modestly in public. Shorts and t-shirts are acceptable for both males and females, but it is best not to wear anything too revealing.  _[UK Foreign, Commonwealth & Development Office (FS.21)]_
- PUBLISHABLE: Wearing military clothing including camouflage is illegal.  _[UK Foreign, Commonwealth & Development Office (FS.21)]_
- Uncertainty: Wording is the source's own. Other nationalities' governments may advise differently.

### Mosquito protection  [MOSQUITO] PUBLISH
- PUBLISHABLE: Bugs (like mosquitoes, ticks, and fleas) can spread a number of diseases in Ghana. Many of these diseases cannot be prevented with a vaccine or medicine. You can reduce your risk by taking steps to prevent bug bites.  _[US Centers for Disease Control and Prevention (CD.40)]_
- PUBLISHABLE: Use permethrin-treated clothing and gear (such as boots, pants, socks, and tents). Do not use permethrin directly on skin.  _[US Centers for Disease Control and Prevention (CD.41)]_
- PUBLISHABLE: Stay and sleep in air-conditioned or screened rooms.  _[US Centers for Disease Control and Prevention (CD.41)]_
- PUBLISHABLE: Use a bed net if the area where you are sleeping is exposed to the outdoors.  _[US Centers for Disease Control and Prevention (CD.41)]_
- PUBLISHABLE: FOR PROTECTION AGAINST TICKS AND MOSQUITOES: Use a repellent that contains 20% or more DEET for protection that lasts up to several hours.  _[US Centers for Disease Control and Prevention (CD.42)]_
- PUBLISHABLE: Always use insect repellent as directed.  _[US Centers for Disease Control and Prevention (CD.42)]_
- PUBLISHABLE: Avoid scratching bug bites, and apply hydrocortisone cream or calamine lotion to reduce the itching.  _[US Centers for Disease Control and Prevention (CD.43)]_
- PUBLISHABLE: Although bed bugs do not carry disease, they are an annoyance. See our information page about avoiding bug bites for some easy tips to avoid them.  _[US Centers for Disease Control and Prevention (CD.44)]_
- Uncertainty: Wording is the source's own. Other nationalities' governments may advise differently.

### Electrical plugs and supply  [PLUGS] HOLD
- HELD (item_held): Ghana's supply regulations (L.I. 1935) require the voltage at a customer's premises to stay within plus or minus ten percent of 230 volts or 400 volts (or of the higher network voltages they list).  _[Electricity Company of Ghana (EC.02); Ghana Public Utilities Regulatory Commission (LI.01)]_
- HELD (item_held): Ghana's National Electricity Grid Code treats 50 Hz as the nominal system frequency.  _[Electricity Company of Ghana (EC.02); Energy Commission of Ghana (GC.06)]_
- Uncertainty: Official statements as of their stated dates; not re-confirmed against any later amendment. Held: plug_type: source not retrieved.

Missing evidence:
- PLUGS: plug_type: source not retrieved (not published: no source of the required standing states it)

## Exceptions held (routed to the Master Agent)

- [automatic retry] gh/EQUIPMENT/MOSQUITO: no_dated_source_within_freshness_limit. Held until a registry-backed source states a publication date inside its freshness limit. No action unless it persists. (seen 4x since 22 Oct 2026 22:48 Cyprus time)
- [automatic retry] gh/EQUIPMENT/PLUGS: evidence_missing. Held: plug_type: source not retrieved (not published: no source of the required standing states it). Retried automatically each run; source problems are listed separately. (seen 1x since 23 Oct 2026 13:28 Cyprus time)
- [automatic retry] gh/EQUIPMENT: source_unavailable. Source cannot be used as configured: http_error:404. Retried automatically each run. (seen 1x since 23 Oct 2026 13:28 Cyprus time)
- [automatic retry] gh/EQUIPMENT/PLUGS: source_unavailable_preserving_last_known_good. A source behind the published record could not be retrieved this run, so the record was left exactly as it was (a source failure never shrinks or replaces published content). Retried automatically. (seen 1x since 23 Oct 2026 13:28 Cyprus time)

## Awaiting budget authorisation (model-written topics; not exceptions)

- none

## Scope

- Validation scope: gh / ENVIRONMENT, TRANSPORT, EQUIPMENT. Expansion: OFF (needs confirmation of coverage and budget); proof gate: MET (16 record(s) verified live (need 3), 0 rollback(s) (max 0), 4 clean publication run(s) (need 2))

## Records published by this agent

- pub-20261022T142741Z-1dfa3c: gh-environment-flooding-natural-hazards, gh-transport-road-travel, gh-transport-taxis-and-public-transport, gh-transport-air-travel-and-gateways, gh-equipment-clothing, gh-equipment-mosquito | commit 6465d45e349cedc2a37c929153d24470b0a5a0a7 | verified live 22 Oct 2026 17:36 Cyprus time
- pub-20261022T181139Z-21fa3f: gh-environment-climate-seasonal, gh-environment-flooding-natural-hazards, gh-equipment-clothing, gh-equipment-plugs | commit None | verified live 22 Oct 2026 22:22 Cyprus time
- pub-20261022T202028Z-a48d6e: gh-equipment-clothing, gh-equipment-mosquito, gh-equipment-plugs | commit 136492e432f18448c753cbb6012e8e1761597d66 | verified live 22 Oct 2026 23:21 Cyprus time
- pub-20261022T210636Z-0fc6b4: gh-equipment-clothing, gh-equipment-mosquito, gh-equipment-plugs | commit 35b856ea60e4dcef5fc89bb99c50840a1be17731 | verified live 23 Oct 2026 00:07 Cyprus time

## Drafts and assessments held on this branch

- gh/EQUIPMENT: cib-gh-equipment-00acaa0f49 | HELD_EXCEPTIONS_ROUTED | model: NOT_NEEDED | claims 28 | https://github.com/smitblade-dot/Travel_Intelligence/blob/ti-country-drafts/drafts/gh/EQUIPMENT/cib-gh-equipment-00acaa0f49.json
- gh/EQUIPMENT: cib-gh-equipment-0fc6b4df3a | AUTO_PUBLISHED_AND_VERIFIED | model: NOT_NEEDED | claims 29 | https://github.com/smitblade-dot/Travel_Intelligence/blob/ti-country-drafts/drafts/gh/EQUIPMENT/cib-gh-equipment-0fc6b4df3a.json | superseded by cib-gh-equipment-00acaa0f49
- gh/EQUIPMENT: cib-gh-equipment-a48d6e4474 | AUTO_PUBLISHED_AND_VERIFIED | model: NOT_NEEDED | claims 27 | https://github.com/smitblade-dot/Travel_Intelligence/blob/ti-country-drafts/drafts/gh/EQUIPMENT/cib-gh-equipment-a48d6e4474.json | superseded by cib-gh-equipment-0fc6b4df3a
- gh/EQUIPMENT: cib-gh-equipment-3a0cda8cc9 | ELIGIBLE_AWAITING_PUBLICATION_RUN | model: NOT_NEEDED | claims 29 | https://github.com/smitblade-dot/Travel_Intelligence/blob/ti-country-drafts/drafts/gh/EQUIPMENT/cib-gh-equipment-3a0cda8cc9.json | superseded by cib-gh-equipment-a48d6e4474
- gh/ENVIRONMENT: cib-gh-environment-21fa3f4dcf | AUTO_PUBLISHED_VERIFICATION_PENDING | model: NOT_NEEDED | claims 9 | https://github.com/smitblade-dot/Travel_Intelligence/blob/ti-country-drafts/drafts/gh/ENVIRONMENT/cib-gh-environment-21fa3f4dcf.json
- gh/TRANSPORT: cib-gh-transport-21272f0ba2 | AUTO_PUBLISHED_AND_VERIFIED | model: NOT_NEEDED | claims 31 | https://github.com/smitblade-dot/Travel_Intelligence/blob/ti-country-drafts/drafts/gh/TRANSPORT/cib-gh-transport-21272f0ba2.json
