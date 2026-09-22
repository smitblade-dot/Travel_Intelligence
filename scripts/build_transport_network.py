#!/usr/bin/env python3
"""Build a conservative, source-derived transport network for TI.

Phase 1 uses airport reference data plus TI locations. It creates nodes and
only explicit country membership/airport-reference relationships. Live routes
are deliberately not inferred from aircraft observations.
"""
from __future__ import annotations
import csv, io, json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data.json"
OUT = ROOT / "data" / "transport_network.json"

def fetch(url):
    req = Request(url, headers={"User-Agent": "TravelIntelligence/0.1"})
    with urlopen(req, timeout=30) as r:
        return r.read()

def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    countries = {c.get("id"): c for c in data.get("countries", []) if c.get("id")}
    locations = data.get("locations", [])
    airports = []
    try:
        rows = csv.DictReader(io.StringIO(fetch("https://ourairports.com/airports.csv").decode("utf-8", errors="replace")))
        for row in rows:
            if row.get("iso_country") in {countries[c].get("iso3") for c in countries}:
                airports.append({
                    "id": row.get("ident"),
                    "name": row.get("name"),
                    "countryId": next((cid for cid, c in countries.items() if c.get("iso3") == row.get("iso_country")), None),
                    "iata": row.get("iata_code") or None,
                    "icao": row.get("icao_code") or None,
                    "latitude": float(row["latitude_deg"]) if row.get("latitude_deg") else None,
                    "longitude": float(row["longitude_deg"]) if row.get("longitude_deg") else None,
                    "scheduledService": row.get("scheduled_service"),
                    "type": row.get("type"),
                    "sourceId": "ourairports-airports"
                })
    except Exception as exc:
        print(f"Airport reference fetch failed: {exc}")

    location_nodes = [{
        "id": x.get("id"),
        "name": x.get("name"),
        "countryId": x.get("countryId"),
        "type": x.get("type"),
        "parentLocationId": x.get("parentLocationId")
    } for x in locations if x.get("id")]

    # Only explicit relationships are emitted. No airline routes are guessed.
    country_edges = [
        {"from": a["id"], "to": a["countryId"], "relationship": "LOCATED_IN"}
        for a in airports if a.get("countryId")
    ]

    output = {
        "schemaVersion": "1.0",
        "generated": datetime.now(timezone.utc).isoformat(),
        "purpose": "Conservative transport reference network. Not a live route database.",
        "nodes": {
            "airports": airports,
            "tiLocations": location_nodes
        },
        "edges": {
            "airportCountry": country_edges,
            "scheduledRoutes": [],
            "roadRoutes": [],
            "railRoutes": [],
            "portRoutes": []
        },
        "rules": {
            "airportReference": "OurAirports supplies reference identity/location only.",
            "liveOperations": "Do not infer closure or operational status from reference data.",
            "flightRoutes": "No route is created without an explicit route source.",
            "aircraftSignals": "ADSB observations may support investigation but do not create routes."
        }
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}: {len(airports)} airports, {len(location_nodes)} TI locations")

if __name__ == "__main__":
    main()
