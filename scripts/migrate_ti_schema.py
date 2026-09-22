#!/usr/bin/env python3
"""
One-time TI data-model migration.

This script is intentionally additive. It reads the existing GitHub Pages
data.json, preserves all existing baseline/reference data, change_log and
oilGasSummary, and adds the Current Intelligence collections required by the
new TI architecture.

It does NOT:
- rewrite or delete existing records;
- mass-create SourceObservation rows for grandfathered records;
- invent event/source evidence;
- modify oilGasSummary;
- touch sibling repositories.

Run from the repository root:
    python3 scripts/migrate_ti_schema.py

Use --check to validate the current structure without writing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data.json"

REQUIRED_TOP_LEVEL = (
    "meta",
    "countries",
    "records",
    "sources",
    "locations",
    "events",
    "sourceObservations",
    "change_log",
    "oilGasSummary",
)

EVENT_STATUSES = {
    "EARLY_REPORT",
    "REPORTED",
    "LOCAL_REPORT",
    "UNVERIFIED",
    "CORROBORATED",
    "CONFIRMED",
    "DISPUTED",
    "FALSE",
    "CORRECTED",
    "SUPERSEDED",
    "RESOLVED",
}

OBSERVATION_STATUSES = {
    "UNVERIFIED",
    "REPORTED",
    "CORROBORATED",
    "CONFIRMED",
    "DISPUTED",
    "CORRECTED",
    "WITHDRAWN",
}

SOURCE_QUALITY_GRADES = {"A", "B", "C", "D", "E", "F"}

CONTENT_RIGHTS = {
    "PUBLIC_DOMAIN",
    "GOVERNMENT_OPEN",
    "OPEN_LICENCE",
    "COMMERCIAL_LICENCE",
    "SOURCE_TERMS_PERMIT_REUSE",
    "FACTUAL_SUMMARY_ONLY",
    "LINK_ONLY",
    "RIGHTS_UNKNOWN",
    "RESTRICTED",
    "PROHIBITED",
}

ACCESS_METHODS = {
    "PUBLIC_WEB",
    "API",
    "RSS",
    "GOVERNMENT_FEED",
    "MANUAL",
    "LICENSED_FEED",
    "SOCIAL_PLATFORM",
    "SEARCH_DISCOVERY",
    "OTHER",
}

AUTOMATION_PERMISSIONS = {
    "PERMITTED",
    "LICENSED",
    "UNKNOWN",
    "RESTRICTED",
    "PROHIBITED",
    "HUMAN_REVIEW_REQUIRED",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_shape(data: dict) -> list[str]:
    errors: list[str] = []

    missing = [key for key in REQUIRED_TOP_LEVEL if key not in data]
    if missing:
        errors.append("Missing top-level keys: " + ", ".join(missing))

    for key in ("countries", "records", "sources", "locations", "events", "sourceObservations"):
        value = data.get(key)
        if not isinstance(value, (dict, list)):
            errors.append(f"{key} must be an object or array")

    if not isinstance(data.get("change_log"), list):
        errors.append("change_log must be an array")

    if not isinstance(data.get("oilGasSummary"), (dict, list)):
        errors.append("oilGasSummary must remain an object or array")

    sources = data.get("sources", [])
    source_values = sources.values() if isinstance(sources, dict) else sources
    for source in source_values:
        if not isinstance(source, dict):
            errors.append("source collection contains a non-object")
            continue
        grade = source.get("sourceQualityGrade")
        if grade is not None and grade not in SOURCE_QUALITY_GRADES:
            errors.append(f"Invalid sourceQualityGrade: {grade}")
        rights = source.get("contentRights")
        if rights is not None and rights not in CONTENT_RIGHTS:
            errors.append(f"Invalid contentRights: {rights}")
        access = source.get("accessMethod")
        if access is not None and access not in ACCESS_METHODS:
            errors.append(f"Invalid accessMethod: {access}")
        permission = source.get("automationPermission")
        if permission is not None and permission not in AUTOMATION_PERMISSIONS:
            errors.append(f"Invalid automationPermission: {permission}")

    return errors


def migrate(data: dict) -> tuple[dict, dict]:
    before = {
        "countries": len(data.get("countries", {})),
        "records": len(data.get("records", [])),
        "sources": len(data.get("sources", [])),
        "locations": len(data.get("locations", [])),
        "events": len(data.get("events", [])),
        "sourceObservations": len(data.get("sourceObservations", [])),
    }

    # Preserve the existing source shape and add only additive provenance
    # fields where they are absent. Do not guess source-quality grades.
    sources = data.get("sources", [])
    source_values = sources.values() if isinstance(sources, dict) else sources
    for source in source_values:
        source.setdefault("contentRights", "RIGHTS_UNKNOWN")
        source.setdefault("accessMethod", "PUBLIC_WEB")
        source.setdefault("automationPermission", "UNKNOWN")
        source.setdefault("originalLanguage", "en")
        source.setdefault("geographicScope", "NATIONAL")

    # New collections are intentionally empty until evidence is explicitly
    # migrated/ingested. This prevents invented Current Intelligence.
    data.setdefault("events", [])
    data.setdefault("sourceObservations", [])

    data.setdefault("meta", {})
    data["meta"].setdefault("schemaVersion", "2.0")
    data["meta"]["currentIntelligence"] = {
        "model": "event_source_observation",
        "migration": "additive",
        "grandfatheredRecords": True,
        "newRecordsRequireObservation": True,
        "lastSchemaMigration": utc_now(),
    }

    after = {
        "countries": len(data.get("countries", {})),
        "records": len(data.get("records", [])),
        "sources": len(data.get("sources", [])),
        "locations": len(data.get("locations", [])),
        "events": len(data.get("events", [])),
        "sourceObservations": len(data.get("sourceObservations", [])),
    }

    return data, {"before": before, "after": after}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate without writing")
    args = parser.parse_args()

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    errors = validate_shape(data)
    if errors:
        for error in errors:
            print("ERROR:", error)
        return 1

    migrated, summary = migrate(data)

    print(json.dumps(summary, indent=2))
    print("Existing records are grandfathered; no SourceObservation rows were invented.")

    if args.check:
        print("CHECK ONLY: no file written.")
        return 0

    DATA_PATH.write_text(
        json.dumps(migrated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {DATA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
