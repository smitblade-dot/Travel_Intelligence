#!/usr/bin/env python3
"""Refresh the global nuclear/radiological inventory.

The nuclear layer is source-driven. This refresh currently ingests the public
IAEA PRIS country-level operating-reactor table and uses it to build country
profiles. Facility/site records are not invented from country aggregates.

Future collectors can add reactor-level, research-reactor, NFCIS and PIEDB
records to the same inventory. A failed source must never erase a previous
successful inventory.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ROOT / "config" / "nuclear_global_scope.json"
COUNTRY_SCOPE = ROOT / "config" / "nuclear_country_scope.json"
OUT = ROOT / "data" / "nuclear_global.json"
PROFILES = ROOT / "data" / "nuclear_country_profiles.json"

PRIS_URL = "https://pris.iaea.org/pris/WorldStatistics/WorldStatisticsLandingPage.aspx"

ISO2 = {
    "ARGENTINA":"ar","ARMENIA":"am","BELARUS":"by","BELGIUM":"be","BRAZIL":"br",
    "BULGARIA":"bg","CANADA":"ca","CHINA":"cn","CZECH REPUBLIC":"cz","FINLAND":"fi",
    "FRANCE":"fr","HUNGARY":"hu","INDIA":"in","IRAN, ISLAMIC REPUBLIC OF":"ir",
    "JAPAN":"jp","KOREA, REPUBLIC OF":"kr","MEXICO":"mx","NETHERLANDS, KINGDOM OF":"nl",
    "PAKISTAN":"pk","ROMANIA":"ro","RUSSIA":"ru","SLOVAKIA":"sk","SLOVENIA":"si",
    "SOUTH AFRICA":"za","SPAIN":"es","SWEDEN":"se","SWITZERLAND":"ch",
    "UKRAINE":"ua","UNITED ARAB EMIRATES":"ae","UNITED KINGDOM":"gb",
    "UNITED STATES OF AMERICA":"us",
}

ISO3 = {
    "ar":"ARG","am":"ARM","by":"BLR","be":"BEL","br":"BRA","bg":"BGR","ca":"CAN",
    "cn":"CHN","cz":"CZE","fi":"FIN","fr":"FRA","hu":"HUN","in":"IND","ir":"IRN",
    "jp":"JPN","kr":"KOR","mx":"MEX","nl":"NLD","pk":"PAK","ro":"ROU","ru":"RUS",
    "sk":"SVK","si":"SVN","za":"ZAF","es":"ESP","se":"SWE","ch":"CHE","ua":"UKR",
    "ae":"ARE","gb":"GBR","us":"USA",
}

COUNTRY_NAMES = {
    "ar":"Argentina","am":"Armenia","by":"Belarus","be":"Belgium","br":"Brazil",
    "bg":"Bulgaria","ca":"Canada","cn":"China","cz":"Czech Republic","fi":"Finland",
    "fr":"France","hu":"Hungary","in":"India","ir":"Iran","jp":"Japan",
    "kr":"South Korea","mx":"Mexico","nl":"Netherlands","pk":"Pakistan",
    "ro":"Romania","ru":"Russia","sk":"Slovakia","si":"Slovenia","za":"South Africa",
    "es":"Spain","se":"Sweden","ch":"Switzerland","ua":"Ukraine",
    "ae":"United Arab Emirates","gb":"United Kingdom","us":"United States",
}

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.table = None
        self.row = None
        self.cell = None
        self.buf = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in ("td","th") and self.row is not None:
            self.cell = []
            self.buf = []

    def handle_data(self, data):
        if self.cell is not None:
            self.buf.append(data)

    def handle_endtag(self, tag):
        if tag in ("td","th") and self.cell is not None:
            self.row.append(" ".join("".join(self.buf).split()))
            self.cell = None
            self.buf = []
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None

def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))

def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent":"Travel-Intelligence/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")

def parse_pris_country_table(html):
    parser = TableParser()
    parser.feed(html)
    candidates = []
    for table in parser.tables:
        header = [c.upper() for c in (table[0] if table else [])]
        if "COUNTRY" in header and any("NUMBER OF REACTORS" in c for c in header):
            candidates.append(table)
    if not candidates:
        raise RuntimeError("IAEA PRIS country table not found")
    table = candidates[0]
    header = [c.upper() for c in table[0]]
    country_i = header.index("COUNTRY")
    reactor_i = next(i for i,c in enumerate(header) if "NUMBER OF REACTORS" in c)
    capacity_i = next((i for i,c in enumerate(header) if "TOTAL NET ELECTRICAL CAPACITY" in c), None)
    rows = []
    for row in table[1:]:
        if len(row) <= max(country_i, reactor_i):
            continue
        country = row[country_i].strip().upper()
        if country == "TOTAL" or country not in ISO2:
            continue
        reactor_text = re.sub(r"[^0-9]", "", row[reactor_i])
        if not reactor_text:
            continue
        capacity = None
        if capacity_i is not None and len(row) > capacity_i:
            cap = re.sub(r"[^0-9]", "", row[capacity_i])
            capacity = int(cap) if cap else None
        rows.append((country, int(reactor_text), capacity))
    if len(rows) < 25:
        raise RuntimeError(f"IAEA PRIS table sanity check failed: only {len(rows)} country rows")
    return rows

def build_profiles(rows, generated_at):
    profiles = []
    for country, reactor_count, capacity_mw in rows:
        iso2 = ISO2[country]
        profiles.append({
            "iso2": iso2,
            "iso3": ISO3[iso2],
            "name": COUNTRY_NAMES[iso2],
            "nuclearFootprint": True,
            "facilityCount": reactor_count,
            "facilityClasses": ["POWER_REACTOR"],
            "powerReactors": reactor_count,
            "operatingPowerCapacityMWe": capacity_mw,
            "researchReactors": 0,
            "fuelCycleFacilities": 0,
            "otherNuclearFacilities": 0,
            "sourceIds": ["iaea-pris"],
            "sourceStatus": "COUNTRY_AGGREGATE_ONLY",
            "lastRefreshed": generated_at
        })
    return profiles

def main():
    scope = load_json(SCOPE)
    country_scope = load_json(COUNTRY_SCOPE)
    generated_at = datetime.now(timezone.utc).isoformat()

    try:
        html = fetch(PRIS_URL)
        rows = parse_pris_country_table(html)
    except Exception as exc:
        print(f"WARNING: IAEA PRIS refresh failed: {exc}")
        print("Existing nuclear inventory/profile files are preserved.")
        return 0

    profiles = build_profiles(rows, generated_at)
    inventory = {
        "schemaVersion": "1.1",
        "generatedAt": generated_at,
        "status": "SOURCE_DRIVEN",
        "category": "RADIOLOGICAL_NUCLEAR",
        "coverage": {
            "scope": "GLOBAL",
            "countries": len(profiles),
            "locations": 0,
            "facilities": 0,
            "facilityInventoryStatus": "COUNTRY_AGGREGATES_ONLY",
            "note": "Country-level operating power-reactor aggregates are sourced from IAEA PRIS. Facility/site records are only populated when authoritative reactor-level or facility-level records are ingested."
        },
        "sources": scope["sources"],
        "countries": profiles,
        "facilities": [],
        "locations": [],
        "unresolved": [
            {
                "sourceId": "iaea-rrdb",
                "reason": "Public RRDB is authoritative but reactor-level extraction is not yet connected.",
                "action": "Add controlled reactor-level collector."
            },
            {
                "sourceId": "iaea-nfcis",
                "reason": "Public NFCIS interface is authoritative but facility-level extraction is not yet connected.",
                "action": "Add controlled facility-level collector."
            },
            {
                "sourceId": "iaea-piedb",
                "reason": "Public PIEDB is authoritative but facility-level extraction is not yet connected.",
                "action": "Add controlled facility-level collector."
            }
        ]
    }
    profile_doc = {
        "schemaVersion": "1.1",
        "generatedAt": generated_at,
        "status": "SOURCE_DRIVEN",
        "profiles": profiles,
        "note": "Profiles currently contain IAEA PRIS country aggregates. Facility-level counts will expand when RRDB/NFCIS/PIEDB and reactor-level collectors are connected."
    }

    OUT.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    PROFILES.write_text(json.dumps(profile_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"IAEA PRIS nuclear country profiles refreshed: {len(profiles)} countries.")
    print("Facility/site records imported: 0 (by design).")
    return 0

if __name__ == "__main__":
    sys.exit(main())
