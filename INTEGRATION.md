# Proposed maintained nuclear collection layout

This folder contains scripts/tests only. Raw exploratory source payloads, contact details, personal paths and runtime artifacts are excluded. Workflow changes remain owned by PR14.

## Recurring collection

1. Capture PRIS/RRDB with `capture_public_inventory.py --collector-package scripts/nuclear_refresh --out NEW_RUN/pris-rrdb`.
2. For each of iaea-nfcis and iaea-piedb, run `browser/collect-directory.mjs SOURCE NEW_RUN/SOURCE-directory`. This launches a clean unauthenticated browser, reads only rendered directory hrefs and clicks the visible Next page button. It creates per-page count/hash/timestamp evidence. No export URL, ID range or hidden application state is guessed.
3. Run `collect_details.py --directory NEW_RUN/SOURCE-directory/directory.json --out NEW_RUN/SOURCE-details`. It validates the whole directory before fetching and projects only facility identity/status/class fields into the result. Raw detail HTML is hashed in memory and not persisted. HTTP redirects, parser failures, new unmapped countries and missing pages fail closed with an explicit failure list.
4. Convert validated source candidates through the `envelope` adapter and run `refresh_review.py`. All four expected source families must be accounted for. Use `--bootstrap-review` only for first baseline review when no accepted per-source snapshot exists. Existing pending snapshots cannot masquerade as accepted snapshots.
5. Retain the run as a review artifact. New data remains INTERNAL_REVIEW/releaseReady=false. Independent acceptance must precede public import. Existing public inventory/profile files remain unchanged.

## Browser dependency strategy

The repository currently has no Node package manifest or browser dependency strategy; its deterministic collectors use standard-library Python. Keep the browser dependency isolated under `scripts/nuclear_refresh/browser`. The proposal pins Playwright 1.62.1 (matching the available bundled package), requires Node 20 or newer, and uses Chromium only. The official npm registry verified version 1.62.1 and its integrity. npm generated the included package-lock.json with lifecycle scripts disabled; its audit reported zero vulnerabilities. Before CI adoption, independently review the lock and use `npm ci --ignore-scripts`, then install Chromium through Playwright's own version-matched installer. Do not use arbitrary browser downloads or an unpinned latest dependency. This is a proposal for the workflow owner, not an applied workflow change.

Official guidance: https://playwright.dev/docs/ci-intro and https://playwright.dev/docs/browsers . The browser binary version must match the pinned Playwright release. A reviewed CI trial must verify launch, directory readiness, pagination, timeouts and output retention before scheduled activation.

## Verification status

Directory accounting unit tests cover zero IDs, changed totals, duplicates, missing pages, foreign origins, malformed ranges and row mismatches. Detail collector tests cover incomplete discovery, fetch failure and no-data HTML. The parser is additionally checked against the actual saved NFC/PIE detail captures. This is not a claim that the headless browser runtime has been tested: current computer interaction is restricted to the root's CUA browser controls. The observed live CUA pagination supplies initial evidence and validates the DOM concept; the staged browser runtime still needs an independent CI trial.

Today's 876 NFC and 53 PIE observed links are a fresh initial directory capture. Future schedules must enumerate anew rather than reusing that fixed list. These are source directory identities, not deduplicated global physical-site completeness.

## Isolated trial workflow

The proposed `.github/workflows/nuclear-review-trial.yml` runs unit checks on pull requests. After those checks pass, it runs public collection on same-repository pull requests and explicit workflow_dispatch. Fork pull requests run unit checks only. This permits a reviewed branch trial before the workflow exists on the default branch. All permissions are contents:read; there are no secrets, git writes or publication steps. It stores run evidence under runner.temp and uploads artifacts for seven days. PR14 daily production workflow remains untouched. The executable entry point is `python3 scripts/nuclear_refresh/run_refresh.py --repo . --out NEW_RUN --accepted-snapshots data/nuclear_accepted_snapshots --bootstrap-review`.
