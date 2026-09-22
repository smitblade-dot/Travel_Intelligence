#!/usr/bin/env python3
"""Validate the Travel Intelligence canonical data.json structure.

This is intentionally stdlib-only so it can run in GitHub Actions without
installing dependencies. It validates structure and invariants; it does not
invent or backfill observations for grandfathered records.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data.json"

OBS_TARGETS = ("recordId", "eventId")
EVENT_STATUSES = {
    "EARLY_REPORT", "REPORTED", "LOCAL_REPORT", "UNVERIFIED",
    "CORROBORATED", "CONFIRMED", "DISPUTED", "FALSE", "CORRECTED",
    "SUPERSEDED", "RESOLVED",
}
PUBLICATION_STATUSES = {"DRAFT", "INTERNAL_REVIEW", "PUBLISHED", "WITHDRAWN"}

def fail(msg):
    print(f"ERROR: {msg}")
    return 1

def main():
    try:
        data = json.loads(DATA.read_text(encoding="utf-8"))
    except Exception as exc:
        return fail(f"cannot parse {DATA}: {exc}")

    required = [
        "meta", "countries", "records", "sources", "locations",
        "events", "sourceObservations", "change_log", "oilGasSummary",
    ]
    missing = [k for k in required if k not in data]
    if missing:
        return fail("missing root collections: " + ", ".join(missing))

    if str(data.get("meta", {}).get("schemaVersion")) != "2.0":
        return fail("meta.schemaVersion must be 2.0")

    events = data["events"]
    observations = data["sourceObservations"]
    records = data["records"]
    sources = data["sources"]

    # The public UI joins every collection through country.id / countryId.
    # Countries may be serialised as an array, so numeric JSON keys are not
    # valid country identifiers. Catch this class of UI-breaking export before
    # deployment.
    countries = data["countries"]
    if not isinstance(countries, (list, dict)):
        return fail("countries must be an array or object")
    country_values = countries if isinstance(countries, list) else list(countries.values())
    country_ids = set()
    for country in country_values:
        cid = str(country.get("id", "")).lower()
        if not cid:
            return fail("country is missing id")
        if cid in country_ids:
            return fail(f"duplicate country id: {cid}")
        country_ids.add(cid)

    for record in records:
        cid = str(record.get("countryId", "")).lower()
        if cid not in country_ids:
            return fail(f"record {record.get('id')}: countryId does not reference an existing country: {cid!r}")

    for source in sources:
        cid = str(source.get("countryId", "")).lower()
        if cid and cid not in country_ids:
            return fail(f"source {source.get('id')}: countryId does not reference an existing country: {cid!r}")

    for location in data["locations"]:
        cid = str(location.get("countryId", "")).lower()
        if cid and cid not in country_ids:
            return fail(f"location {location.get('id')}: countryId does not reference an existing country: {cid!r}")

    for event in events:
        cid = str(event.get("countryId", "")).lower()
        if cid and cid not in country_ids:
            return fail(f"event {event.get('id')}: countryId does not reference an existing country: {cid!r}")

    if not isinstance(events, list) or not isinstance(observations, list):
        return fail("events and sourceObservations must be arrays")

    event_ids = set()
    for event in events:
        eid = event.get("id")
        if not eid or eid in event_ids:
            return fail(f"duplicate/missing event id: {eid!r}")
        event_ids.add(eid)
        if event.get("eventStatus") and event["eventStatus"] not in EVENT_STATUSES:
            return fail(f"invalid eventStatus on {eid}: {event['eventStatus']}")
        if event.get("publicationStatus") and event["publicationStatus"] not in PUBLICATION_STATUSES:
            return fail(f"invalid publicationStatus on {eid}: {event['publicationStatus']}")

    source_ids = {s.get("id") for s in sources if s.get("id")}
    record_ids = {r.get("id") for r in records if r.get("id")}

    for obs in observations:
        oid = obs.get("id")
        if not obs.get("sourceId") or obs["sourceId"] not in source_ids:
            return fail(f"{oid}: sourceId must reference an existing source")
        if not obs.get("sourceUrl"):
            return fail(f"{oid}: sourceUrl is required")
        if not obs.get("extractedClaim"):
            return fail(f"{oid}: extractedClaim is required")
        targets = [obs.get(k) for k in OBS_TARGETS if obs.get(k)]
        if len(targets) != 1:
            return fail(f"{oid}: exactly one of recordId or eventId is required")
        if obs.get("recordId") and obs["recordId"] not in record_ids:
            return fail(f"{oid}: recordId does not exist")
        if obs.get("eventId") and obs["eventId"] not in event_ids:
            return fail(f"{oid}: eventId does not exist")
        if obs.get("eventStatus") and obs["eventStatus"] not in EVENT_STATUSES:
            return fail(f"{oid}: invalid eventStatus")
        if obs.get("translationMethod") in {"AI_TRANSLATION", "HUMAN_REVIEWED"} and not obs.get("originalLanguage"):
            return fail(f"{oid}: originalLanguage required for translated observation")

    print(
        f"VALID: schema 2.0 | countries={len(data['countries'])} "
        f"records={len(records)} sources={len(sources)} locations={len(data['locations'])} "
        f"events={len(events)} observations={len(observations)}"
    )
    return 0

if __name__ == "__main__":
    sys.exit(main())
