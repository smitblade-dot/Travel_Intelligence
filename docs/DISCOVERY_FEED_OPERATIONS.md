# Discovery feed recovery

The daily refresh distinguishes data-model validation from provider coverage.
A structurally valid intelligence update may still have degraded discovery
inputs. Collection warns immediately. The final `Audit discovery feed health`
step writes a source-by-source Actions summary and fails the run when any
configured source is blocked, unavailable or partial, or the airport reference
is stale/empty. Independently verified intelligence can still be saved before
this final coverage check. Never describe such a run as a fully healthy refresh.

## Sources

- **OurAirports:** use the publisher's daily GitHub Pages CSV, then the same
  publisher's raw GitHub mirror. Validate CSV columns and country codes. Share
  the download between scripts through a per-run cache outside the checkout.
  Match CSV ISO alpha-2 against TI country.id, not TI iso3. The transport
  network includes scheduled-service and large/medium airports in tracked
  countries. If refreshing fails, retain previous airport nodes with their
  original fetch timestamp and explicitly stale status. With no previous
  non-empty network, fail without overwriting the output.
- **GDELT:** bounded 24-hour news query, at most three requests on transient
  failures, with backoff and Retry-After handling. A persistent provider outage
  or rate limit remains a reported failure; no attempt to evade it.
- **GDACS:** validate the current GeoJSON features array before accepting it.
- **ADSB.lol:** the unsupported `/v2/all` call is replaced by documented point
  queries, at most one per second, around one scheduled large/medium airport
  per tracked country where available. Each query covers 100 nautical miles.
  A ten-minute overall budget bounds provider stalls. Store exact sampled
  areas and failures, deduplicate observations and cap
  stored aircraft at 200. This is a geographic sample, not global/national
  coverage. Empty aircraft data never proves closure, flight disruption or
  absence of activity. ODbL attribution is retained.
- **NASA FIRMS:** use the public S-NPP VIIRS C2 Global 24-hour CSV without a
  key. If `FIRMS_MAP_KEY` already exists in Actions secrets, try the correctly
  ordered area API first, then the public file. Validate CSV and acquisition
  dates. Credential-bearing API URLs must never enter logs or snapshots.
  A hotspot is an observation, not a confirmed fire or travel incident.
- **ReliefWeb:** v1 is retired. v2 requires a provider-approved appname.
  Obtain approval through the provider's form and set repository Actions
  variable `RELIEFWEB_APPNAME` to the approved value. Do not use another
  organisation's appname or guess an approved value. Missing configuration
  is `blocked`, not a successful empty result. The public RSS endpoint
  returned an empty response during investigation and is not used as a
  bypass or assumed fallback.

The transport step's failure is isolated from intelligence verification. The
final strict feed-health audit still fails the overall run if airport data is
empty or stale. TI model validation and the Phase 1 audit retain their existing
fail-closed behaviour. Approved ReliefWeb appnames are omitted from stored URLs.

## Checks

```
python3 -m unittest discover -s tests -p test_feed_reliability.py -v
python3 scripts/collect_phase1_signals.py
python3 scripts/build_transport_network.py
python3 scripts/audit_feed_health.py
```

The first command is offline. The following commands contact providers and
write discovery/reference outputs; run in a temporary checkout for a probe.
The scripts never create public TI events. Existing source verification,
publication controls, TI data validation and independent PR QA remain in place.
Baseline generation is outside this correction.

For a probe from GitHub's runner, manually dispatch **Discovery feed regression
checks** on main. Its additional live-probe job collects and audits temporary
outputs with read-only repository permission. It never commits or publishes
data and intentionally fails when any configured provider remains degraded.

## Provider references

- https://ourairports.com/data/
- https://github.com/davidmegginson/ourairports-data
- https://api.adsb.lol/docs
- https://api.adsb.lol/api/openapi.json
- https://firms.modaps.eosdis.nasa.gov/active_fire/
- https://firms.modaps.eosdis.nasa.gov/api/area/
- https://apidoc.reliefweb.int/parameters#appname
- https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/

## Live recovery evidence — 20 October 2026

A temporary-output probe recovered all 52 ADS-B sample areas (394 distinct
observed aircraft), 86,258 validated airport reference rows and 3,926 transport
airport nodes across 52 tracked countries. NASA's public CSV returned 62,763
rows with acquisitions dated 20 October. GDACS also returned usable data.
GDELT remained HTTP 503 after bounded retries. ReliefWeb rejected the previously
configured `travel-intelligence` appname with HTTP 403 and an explicit request
for provider approval. The health audit returned 1, correctly reporting those
two coverage gaps instead of declaring the entire refresh healthy.

## Post-merge follow-up — 20 October 2026

[Read-only main-branch probe 39377032375](https://github.com/smitblade-dot/Travel_Intelligence/actions/runs/39377032375)
at commit `45f9bef6420ca5d01bf918953d61fbb770aa0a03` completed collection
at 05:51 UTC. GDELT was healthy at 05:50:40 UTC; the earlier HTTP 503 was
therefore transient, not evidence of a continuing outage. Keep the existing
bounded retries, Retry-After handling and 24-hour query. No alternate endpoint,
unbounded retry or cached-success substitution is justified by this evidence.

OurAirports, GDACS, NASA FIRMS and all 52 ADS-B sample areas also passed;
3,926 airport nodes were built. ReliefWeb alone was blocked because the Actions
variable was empty. The final audit correctly exited 1. Offline regression
checks passed. This was not a production intelligence refresh and published
no intelligence; a successful Pages deployment is likewise not proof of a
successful daily intelligence refresh.

At that follow-up, approval correspondence had not been located. This was a
historical evidence gap, superseded by the approval recorded below. HTTP
401/403 is explicitly blocked; transient outages remain unavailable.

Live probes retain discovery JSON and transport reference artifacts for seven
days, including on audit failure, so source URLs, observation timestamps and
sampling limitations can be inspected independently. Artifacts are diagnostic
evidence, not verified TI events or authority to publish. Existing independent
PR QA, source verification and publication controls remain mandatory.


## Approved application configuration - 21 October 2026

FORGEDTITANIUM received ReliefWeb approval at 01:00 UTC on 21 October 2026.
Marc verified the approved application name directly from the original email.
The exact approved value is now configured as the repository Actions variable
`RELIEFWEB_APPNAME`; the existing daily refresh and read-only probe consume
that variable. Approval is no longer outstanding. Do not replace it with a
guessed name or submit a duplicate application.

The 20 October production run
[39499702808](https://github.com/smitblade-dot/Travel_Intelligence/actions/runs/39499702808)
passed TI model validation and the foundation audit, and saved the validated
update before failing the strict discovery-health audit. This establishes
validation and a saved update, not a fully healthy discovery refresh.

Verification has three separate acceptance points: the read-only probe must
show successful live ReliefWeb access and retain provenance; regression checks
and independent PR QA must pass on the integrated head; and a complete
production refresh must pass collection, data validation, foundation audit,
validated commit/push and the strict discovery-health audit. Confirm Pages
deployment separately where publication occurs. Provider approval or a Pages
deployment alone does not prove those other stages succeeded. Do not weaken
publication safeguards to satisfy discovery coverage.

## Rate limits and completed reviews - 22 October 2026

Production run 39678231653 retained healthy ReliefWeb access but failed strict
coverage: GDELT returned HTTP 429 and 30 of 52 ADS-B area requests returned
HTTP 429. The review action again exited while background researchers were
still running. A successful action process is not evidence of completed work.

ADSB.lol documents dynamic limits, not a guaranteed one-request-per-second
allowance (https://github.com/adsblol/api#rate-limits). Sampling now waits
three seconds between areas and allows one transient retry through the existing
Retry-After-aware transport. Requests, retry waits and socket timeouts share the
ten-minute budget. Failed/unrequested areas remain explicit; partial coverage
still fails the strict audit. No concurrency, alternate identity or rate-limit
bypass is used. Persistent limits require a later run or provider agreement,
not an invented healthy result.

GDELT transport errors retain bounded retries. HTTP-200 HTML/plain-text replies
now receive a fixed non-JSON diagnostic without retaining the response body,
and up to two further requests spaced by 15 seconds. The initial transport
can make three requests; the two content retries make one each. Schema errors,
access denials and persistent outages remain failures. The 24-hour query and
its security, transport and maritime scope are preserved.

The daily reviewer is restricted to direct tools and must return structured
completion evidence for the current run. Before metadata changes or the
safety-net commit, `scripts/refresh_review.py` requires a successful terminal
result, live research calls, all tracked country IDs, active Hot Topic IDs captured before the review,
source URLs, review outcomes and zero pending tasks. Missing,
partial, unverified, stale-run or delegated/background reviews stop publication.
A completed no-material-change review is valid; data edits are not forced.
Attempted edits are always retained as `ti-refresh-candidate.json`, labelled
unapproved in the validation report and never served by Pages. A failed review
also retains the parseable completion report and a fixed diagnostic reason,
including when the action exhausts its turn budget. Recovering a candidate
requires source review and the usual validation; it is not permission to publish.
The completion gate uses the pre-review country/topic inventory so accidental
country removal cannot reduce required coverage. New active topics are included;
a topic resolved during the run still needs its closing review. Bash background
syntax is checked heuristically as well as the SDK background flag; this is an
accidental-work detection measure, not a shell sandbox.
This validates completion evidence, not the truth of claims: source provenance,
SourceObservation requirements and TI data/foundation checks remain necessary.

The action's structured-output and restricted-tool interfaces are documented at
https://github.com/anthropics/claude-code-action/blob/main/action.yml and
https://code.claude.com/docs/en/cli-reference. Production refreshes now retain
discovery/reference snapshots and completed review evidence for seven days even
if a later audit fails. Raw SDK execution logs are not uploaded as artifacts.
