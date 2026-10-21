# Continuous Current Intelligence and Hot Topics — initial agent build

## Scope
- A separate GitHub Actions job polls RSS/Atom discovery sources every 15 minutes, 24/7.
- Python performs collection, URL deduplication and date-window filtering without AI tokens.
- Haiku triages **new** headlines in a single bounded request per cycle (maximum 12).
- Early reports may be published from one attributable report without corroboration, with
  explicit EARLY_REPORT and LOW confidence labels; subsequent verification is separate.
- Existing-event leads are logged rather than duplicating public events. A follow-up
  verification/updating agent is **not yet implemented**.
- The existing 21:00 Europe/Nicosia full-site consolidation is unchanged.

## Rollout and required configuration
1. Review this pull request and run tests before merging.
2. Configure repository Actions secret `ANTHROPIC_API_KEY` using Claude Console API
   credentials (not the Claude Code OAuth token). Never commit API keys.
3. Optionally set Actions variable `TI_HAIKU_MODEL`; default is
   `claude-haiku-4-5-20251001`. Check the model ID available in your API account.
3a. Spending gateway: set Actions variables `TI_OPS_DRY_RUN=false` and `TI_OPS_PER_TASK_LIMIT_USD`,
   `TI_OPS_DAILY_LIMIT_USD`, `TI_OPS_MONTHLY_LIMIT_USD` BEFORE merging the gateway change. Without all of
   them the monitor makes no model call; see "Production transition" in docs/TI_OPS_DIRECTOR.md. The
   default model is now `claude-haiku-5-5`. A green job is not a completed triage: read the job summary.
4. Leave `TI_MONITOR_PUBLISH` unset or `false` initially. This is **dry-run**:
   Haiku classifications and token counts appear in workflow logs, but no event is published.
5. After source quality and classification QA, set `TI_MONITOR_PUBLISH=true` to
   enable public EARLY_REPORT records. Confirm the public site renders them.
6. Add approved RSS/Atom URLs in `TI_MONITOR_FEEDS` (comma separated). Current
   discovery uses Google News RSS search leads, not direct social-platform access.
   No claim is made that social-media monitoring is operational.
7. Monitor API token usage and GitHub Actions minutes. Set a spending limit in
   Claude Console. The repository cannot guarantee a hard monthly cap itself.

## Failure modes and limits
- Missing API key fails the job explicitly. Failed feeds are logged; all-feed outage
  fails the job. A failed Haiku request does not advance the discovery cursor.
- Only dated headlines from the past 36 hours enter triage. RSS publication time is
  **not** asserted as incident time. `eventDate` holds an incident date only when one is
  stated: by the model, or written in the headline after "since" or "on", in the 31 days up to
  and including the report's date. Reopening, resumption and planned dates, and any date after the
  report (including one the model returns), are never used. Otherwise it is empty and
  `incidentDateBasis` is `not_stated`. The report's
  publication time (`reportPublishedAt`) and retrieval time (`retrievedAt`) are kept separately.
- Attribution names the original publisher from the feed's `<source>` metadata. A Google News
  redirect host is never recorded as the publisher; without metadata the publisher is recorded
  as not identified. The article link is kept as the observation's `sourceUrl`.
- A NATURAL_HAZARD label on a man-made incident (for example a chemical-plant fire) is
  re-filed as OTHER / INDUSTRIAL_INCIDENT unless the headline also names a natural hazard.
- No source feed is treated as independently corroborating itself. Headlines are
  not automatically promoted to CONFIRMED.
- The first pass is a *foundation*, not full 24/7 source coverage. It does not
  monitor private social media or guarantee 15-minute delivery (Actions can be late).
- Monitoring writes to main only after canonical validation, as compare-and-swap commits made by the
  monitor itself (docs/TI_OPS_DIRECTOR.md, "Persistent state on GitHub Actions"). A concurrent daily
  refresh makes the push non-fast-forward; the monitor re-reads and re-applies, never force-pushes.
- A successful GitHub Actions job does not prove GitHub Pages has deployed. Public
  rendering and event counts require separate post-deployment verification.
- RSS items are untrusted text. Haiku is instructed not to follow instructions in
  headlines. Structured output is checked for valid indices and known country IDs.
- Automated event follow-up, legal-sensitive review routing and source-specific social
  connectors remain follow-on work. Budget enforcement is in the spending gateway.

## Agent responsibilities
1. **Collector (Python):** bounded RSS discovery and feed health.
2. **Triage (Haiku):** country relevance, materiality and potential duplicates.
3. **Early publisher (Python):** provenance-preserving PUBLISHED EARLY_REPORT,
   controlled by the explicit publish switch.
4. **Verifier/updater (planned):** corroboration, corrections, developing events.
5. **Lifecycle manager (planned):** Alert → Current → Hot Topic → Archived.
6. **Consolidator (existing):** 21:00 full-site refresh.

Do not treat this PR as a completed verification or lifecycle-agent implementation.
