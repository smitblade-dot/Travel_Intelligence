#!/usr/bin/env python3
"""Production release audit for Travel Intelligence.

Read-only release gate. Fails on structural, provenance or publication-integrity
problems; it never modifies data.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data.json"
NUCLEAR = ROOT / "data" / "nuclear_global.json"
NUCLEAR_PROFILES = ROOT / "data" / "nuclear_country_profiles.json"

FAILURES = []

def fail(msg):
    FAILURES.append(msg)

def main():
    try:
        d = json.loads(DATA.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"FAIL: data.json invalid JSON: {exc}")
        return 1

    required = ["meta", "countries", "records", "sources", "locations",
                "events", "sourceObservations", "change_log", "oilGasSummary"]
    for key in required:
        if key not in d:
            fail(f"missing root collection: {key}")

    countries = d.get("countries", [])
    records = d.get("records", [])
    sources = d.get("sources", [])
    locations = d.get("locations", [])
    events = d.get("events", [])
    observations = d.get("sourceObservations", [])

    country_ids = [str(x.get("id", "")).lower() for x in countries]
    source_ids = {x.get("id") for x in sources if x.get("id")}
    record_ids = {x.get("id") for x in records if x.get("id")}
    event_ids = {x.get("id") for x in events if x.get("id")}

    if len(country_ids) != len(set(country_ids)):
        fail("duplicate country IDs")
    if len(record_ids) != len(records):
        fail("duplicate or missing record IDs")
    if len(source_ids) != len(sources):
        fail("duplicate or missing source IDs")
    if len(event_ids) != len(events):
        fail("duplicate or missing event IDs")

    for r in records:
        if r.get("countryId") not in country_ids:
            fail(f"record {r.get('id')} has invalid countryId")
        if r.get("sourceId") and r["sourceId"] not in source_ids:
            fail(f"record {r.get('id')} has invalid sourceId")

    for o in observations:
        if o.get("sourceId") not in source_ids:
            fail(f"observation {o.get('id')} has invalid sourceId")
        if not o.get("sourceUrl") or not o.get("extractedClaim"):
            fail(f"observation {o.get('id')} missing sourceUrl/extractedClaim")
        targets = int(bool(o.get("recordId"))) + int(bool(o.get("eventId")))
        if targets != 1:
            fail(f"observation {o.get('id')} must target exactly one record/event")
        if o.get("recordId") and o["recordId"] not in record_ids:
            fail(f"observation {o.get('id')} has invalid recordId")
        if o.get("eventId") and o["eventId"] not in event_ids:
            fail(f"observation {o.get('id')} has invalid eventId")
        if not o.get("independenceGroupId"):
            fail(f"observation {o.get('id')} missing independenceGroupId")

    published = {e.get("id") for e in events if e.get("publicationStatus") == "PUBLISHED"}
    published_obs = [o for o in observations if o.get("eventId") in published]
    ci = d.get("meta", {}).get("currentIntelligence", {})
    if ci.get("events") != len(published):
        fail("currentIntelligence.events does not match published events")
    if ci.get("sourceObservations") != len(published_obs):
        fail("currentIntelligence.sourceObservations does not match published observations")

    meta_counts = d.get("meta", {}).get("counts", {})
    expected = {
        "countries": len(countries),
        "records": len(records),
        "sources": len(sources),
        "locations": len(locations),
        "events": len(events),
        "sourceObservations": len(observations),
    }
    for key, value in expected.items():
        if meta_counts.get(key) != value:
            fail(f"meta.counts.{key}={meta_counts.get(key)!r}; expected {value}")

    for path, label in [(NUCLEAR, "nuclear_global.json"),
                        (NUCLEAR_PROFILES, "nuclear_country_profiles.json")]:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            fail(f"{label} invalid/unreadable: {exc}")

    if FAILURES:
        print(f"FAIL: production audit found {len(FAILURES)} issue(s)")
        for item in FAILURES:
            print(f"- {item}")
        return 1

    print(
        "PASS: production release audit | "
        f"countries={len(countries)} records={len(records)} sources={len(sources)} "
        f"locations={len(locations)} events={len(events)} observations={len(observations)} "
        f"publishedEvents={len(published)}"
    )
    return 0

if __name__ == "__main__":
    sys.exit(main())
