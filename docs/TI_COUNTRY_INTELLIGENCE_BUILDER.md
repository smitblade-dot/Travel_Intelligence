# 03AA-04_Country_Intelligence_Builder_TI

FORGEDTITANIUM LTD, Cyprus. Stream: 03 Agents & Automation. Substream: 03AA-04 Intelligence Automation
Agents. Business application: Travel Intelligence. Development, associated IP and agent costs belong under
03AA-04. No agent number, organisational code, cost code or document number has been invented.
Acceptance standard: `docs/AGENT_BUILD_STANDARD.md`.

## What it is

TI is an automated, live intelligence service governed by rules and spending limits. This agent finds a missing or stale
country/category in `data.json`, collects permitted evidence, applies the TI publication rules, **publishes what passes
automatically**, checks that the public site serves it, and holds only the exceptions, with their reasons, routed through the
Master Agent. (Operating model corrected by Marc on 22 October 2026; it replaces the earlier draft-only design with mandatory
human review.)

It is the `country_content_builder` worker of the Master Agent (`03AA-04_Master_Agent_TI_Ops_Director`) and also runs on
GitHub Actions. It reuses: the Director's queue and dedupe-key format, its reserve-before-call spending gateway (through the
monitor's `MonitorSpend` wrapper, so limits are shared across runs), the compare-and-swap commit backend the continuous monitor
uses to write main, the canonical validator `scripts/validate_ti_data.py`, the source register `config/source_registry.json`,
`config/baseline_requirements.json` and the 14 category keys, the Production Controller's site address, GitHub Pages as the
deployment, and `meta.counts` / `meta.generated` / `change_log` upkeep as CLAUDE.md requires. It does not duplicate the Sonnet
bulk generator (`baseline-population.yml`).

## The rules (a matching quotation is not enough)

Content is published only if every rule holds. Rules are in `scripts/ti_ops_director/country_rules.py`.

| Rule | What is checked | On failure |
|---|---|---|
| **Permitted source** | The source is in the TI source register (`fcdos` etc.) at TIER_1, ACTIVE or INTEGRATION_READY, automation YES/REVIEWED, retrieved this run, robots.txt honoured. A source outside the register may corroborate but never establish a claim alone. | claim excluded; `source_not_in_registry` etc. routed |
| **Freshness** | At least one register-backed source states a date inside its limit (FCDO: the later of its substantive-update and page-updated dates, 120 days). An undated page cannot establish freshness. Present-tense claims are not baseline: they belong to Current Intelligence events. | claim excluded |
| **Attribution and dates** | Published text starts with the publisher and its stated date ("UK FCDO travel advice (last updated 2 Jul 2026): ..."). The record keeps source URL, publication date, retrieval date, original language, rights and the observation. | n/a (built in code) |
| **Classification** | The claim comes from a section whose heading belongs to the topic, and each source block goes to the one topic it fits best. Another topic's wording is excluded. | claim excluded |
| **Support** | Extractive claims are the source's own block, unedited. A model-written claim must also pass an independent verification pass (SUPPORTED, not PARTIAL), keep every hedge, negation and named place, and use no number or name the quote lacks. | item held (`model_claim_unverified`, `qualifier_lost`) |
| **Uncertainty and conflict** | Only VERIFIED claims publish. UNCERTAIN or CONFLICT claims do not. Sources that advise opposite things, or new text that advises the opposite of an existing record, hold the item. The existing record is left untouched. | item held, needs a person |
| **Quality** | Self-contained block (not a fragment, pointer such as "see below", link, contact detail, markup or unbalanced bracket); not a duplicate of existing content; confidence at least MEDIUM (one register-backed dated source; HIGH needs two independent ones). | claim excluded / no change |
| **Schema** | The candidate `data.json` passes `validate_ti_data.py` before anything is pushed. | publication refused |
| **Spending** | Every model call is reserved through the existing gateway against the per-task, daily and monthly limits. | work waits |

Ordinary code does the work first. The model is used only for topics the extractive pass cannot cover, with Haiku by
default and Sonnet only as a second opinion on a PARTIAL verdict, only if `sonnet_escalation_enabled`, never Opus.

## Publication path

1. Gap detected (MISSING, or STALE: every active record past review or older than 365 days) for the active scope.
2. Evidence collected (allow-listed HTTPS hosts, robots.txt, size/time/retry bounds, numbered verbatim passages).
3. Claims built and assessed. Items that pass become publication items (record, any new source, one observation per source).
4. **Publish** only from a scheduled or manual run on `refs/heads/main` with mode `publish`, `publication.enabled` true and
   `TI_CB_PUBLISH` not `false`. One compare-and-swap commit to main (`GitStateBackend`): the agent reads the tip, applies the
   items, validates, pushes; if main moved it re-reads and re-applies, never force-pushes.
5. **Duplicate prevention:** a record whose content hash already matches is not rewritten (its check date is refreshed after 7 days).
6. **Safe updates:** only records this agent published are replaced, and the previous version is stored in the `change_log` entry.
   Hand-written and grandfathered records are never touched. A replacement that fails any rule leaves the existing record as
   it was, with its original `dateChecked`, so its age on the site stays honest.
7. **Live verification:** the public `data.json` (cache-busting) is polled for 15 minutes. If the site is merely slow, the
   publication is recorded as `VERIFICATION_PENDING` and nothing is removed: later runs recheck it (bounded: `max_rechecks`,
   default 6, 2 minutes each) and mark it `VERIFIED`. A pending state never exits non-zero.
8. **Rollback is reserved for demonstrated error:** the site is serving a release at least as new as the publication
   (`meta.generated` not older) yet not its content, or a person asks for it. Rollback is by record id (not `git revert`, which
   cannot work on a one-line `data.json`), verified again; anything edited by someone else since is left alone. If rechecks
   run out, the publication is `VERIFICATION_FAILED`, kept on main, escalated as an exception, and the run exits 5 so CI shows
   it. Manual rollback: `python3 scripts/country_builder.py rollback <publicationId> --by NAME`.

## Exceptions

An item or claim that fails a rule is held, never published, and recorded with its reasons in the ledger
(`country_builder_state.json` on the drafts branch) and the report. Each open exception is mirrored into the Master Agent
queue as one deduplicated blocked task (`country_publication_exception`), cancelled automatically when a later run resolves it:
by the queue worker, or with `python3 scripts/country_builder.py route-exceptions`. Exceptions marked "needs decision" are those
only a person can resolve (for example adding a source to the register or approving a robots.txt exception); the rest retry
automatically each run.

## Scope and expansion

Ghana (ENVIRONMENT, TRANSPORT, EQUIPMENT) is the validation scope. It is not a permanent restriction: `scope.expansion`
defines a proof gate (published and live-verified records, no rollbacks, clean publication runs) and the agent reports whether
it is met every run. Expansion stays off until `scope.expansion.enabled` is set, because it also needs confirmed coverage and
budget; nothing widens automatically before the path is proved.

## Spending

The ordinary-code path costs nothing. Model use needs `TI_CB_DRY_RUN=false`, all three limits and `ANTHROPIC_API_KEY`;
without them model-written topics wait in "Awaiting budget authorisation" and no call is made. The Ops Director's `TI_OPS_*`
limits and the earlier evaluation allowance are not read. Proposed budget (not yet authorised): per task $0.05, daily $0.20,
monthly $2.00, Haiku only, Sonnet escalation off. A scripted Ghana drafting call plus verification costs about $0.001.

## Controls

| Control | How |
|---|---|
| Stop everything | repository variable `TI_CB_MODE=off`, or disable the workflow |
| Stop publication only | `TI_CB_PUBLISH=false` (anything but the committed `publish` setting fails closed) |
| Pause / stop | `python3 scripts/country_builder.py pause`, `stop`, `resume` (stored in the ledger) |
| Roll back a publication | `python3 scripts/country_builder.py rollback <publicationId> --by NAME` |
| Modes | `off`, `observe` (plans, writes nothing), `draft` (assesses and saves, never writes main), `publish` |

Pull requests run in `draft` mode with dry-run forced and no model key; the code refuses to write main unless the event is a
schedule or a manual dispatch on main.

## Known limits

- CDC Travelers' Health and TravelHealthPro were registered on 22 Oct 2026 (robots.txt allows the paths; CDC Crawl-delay 20 is
  respected by the one-page-per-run fetch; TravelHealthPro states OGL v3.0). TravelHealthPro pages carry no stated date, so they
  corroborate but cannot establish freshness.
- **Climate (WMO WWIS, registered 22 Oct 2026):** WMO documents programmatic download of `/en/json/[cityId]_en.json` (acknowledge WWIS,
  reproduce accurately, disclaimer applies) and robots.txt allows `/en/`. The builder reads **only** the long-term monthly climate
  averages for Accra (921), Kumasi (922) and Wa (1387), never the feed's short-range forecast, and publishes one seasonal record only
  if all three stations are retrieved (one station would misdescribe a country whose north has a different rainy season). The text says
  "climate averages ... not current conditions or a forecast", names the reference period (1981-2010), and the record note says that
  period is not the most recent WMO normal. Freshness is the end of the stated reference period (limit 20 years); this is an
  interpretation of the freshness rule for climate normals, applied openly. The feed's data-supply dates (2003, 2020) predate or
  conflict with the stated period and are not relied on. NASA POWER and World Bank CCKP stay configured but unused: no robots
  exception is approved.
- **Plugs and electricity (registered 22 Oct 2026):** plug type from the Ghana Standards Authority notice (effective 1 Jan 2019),
  voltage from L.I. 1935 (gazetted 13 Jun 2008, via the PURC), nominal frequency from the Energy Commission's National Electricity
  Grid Code (October 2009, month precision, no day invented). The ECG technical page (undated, not in the register) corroborates
  voltage and frequency but cannot establish them. Each fact publishes only if its own dated source states it; otherwise it is held
  with the reason. Type D is listed in TI's reference fields but no official source states it, so it is not published. These are
  text PDFs read with pypdf (pinned in the workflows); an unreadable PDF is a recorded parse failure, not a crash. Instruments may
  have been amended since the date shown and the record says so.
- Reports show times in Cyprus local time (Europe/Nicosia); stored ledger and data timestamps remain UTC.
- The model path (writer, verifier, Sonnet second opinion) is proven only with scripted replies. Haiku's quality is not
  measured; see `scripts/country_builder.py eval`.
- Only English government pages are used; no translation is performed.
- The extractive path publishes the source's own wording from sections it can classify; it does not summarise, translate or
  combine facts.
