#!/usr/bin/env python3
"""Refresh the global nuclear/radiological inventory.

The script is intentionally source-driven. It creates the inventory and country
profile layer from authoritative source feeds where machine-readable access is
available. It does not invent facilities from country-level associations.

Source classes:
- WNA / IAEA PRIS: power reactors
- IAEA RRDB: research/test reactors
- IAEA NFCIS: civilian fuel-cycle facilities
- IAEA PIEDB: post-irradiation facilities

Where a source is web-only or requires restricted access, the source is retained
as a verification reference and unresolved records are preserved.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ROOT / "config" / "nuclear_global_scope.json"
COUNTRY_SCOPE = ROOT / "config" / "nuclear_country_scope.json"
OUT = ROOT / "data" / "nuclear_global.json"
PROFILES = ROOT / "data" / "nuclear_country_profiles.json"

def load(path):
    return json.loads(path.read_text(encoding="utf-8"))

def now():
    return datetime.now(timezone.utc).isoformat()

def main():
    scope = load(SCOPE)
    country_scope = load(COUNTRY_SCOPE)

    # Do not fabricate facility records. The initial build establishes the
    # global source map and country expansion rules. Individual collectors can
    # append verified records as machine-readable source access is confirmed.
    inventory = {
        "schemaVersion": "1.0",
        "generatedAt": now(),
        "status": "SOURCE_DRIVEN",
        "category": "RADIOLOGICAL_NUCLEAR",
        "coverage": {
            "scope": "GLOBAL",
            "seedPowerReactorCountries": len(country_scope["initialPowerReactorCountries"]),
            "countries": 0,
            "facilities": 0,
            "locations": 0
        },
        "sources": scope["sources"],
        "countries": [],
        "facilities": [],
        "locations": [],
        "unresolved": [
            {
                "sourceId": "iaea-rrdb",
                "reason": "Public database description is available, but the facility dataset is not exposed through a stable machine-readable endpoint in this workflow.",
                "action": "Verify accessible export/API or controlled extraction before importing records."
            },
            {
                "sourceId": "iaea-nfcis",
                "reason": "Global facility database is available through a web interface, but the current public page does not expose a stable machine-readable export through this workflow.",
                "action": "Verify accessible export/API or controlled extraction before importing records."
            },
            {
                "sourceId": "iaea-piedb",
                "reason": "Facility catalogue is public but requires controlled extraction.",
                "action": "Verify accessible export/API or controlled extraction before importing records."
            }
        ]
    }

    profiles = {
        "schemaVersion": "1.0",
        "generatedAt": inventory["generatedAt"],
        "status": "SOURCE_DRIVEN",
        "profiles": []
    }

    OUT.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    PROFILES.write_text(json.dumps(profiles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Global nuclear inventory scaffold refreshed.")
    print("Seed power-reactor countries:", inventory["coverage"]["seedPowerReactorCountries"])
    print("Facility records imported:", 0)
    print("Unresolved source integrations:", len(inventory["unresolved"]))

if __name__ == "__main__":
    main()
