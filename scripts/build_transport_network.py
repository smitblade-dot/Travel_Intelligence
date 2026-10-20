#!/usr/bin/env python3
"""Build a conservative, source-derived transport network for TI.

Phase 1 uses airport reference data plus TI locations. It creates nodes and
only explicit country membership/airport-reference relationships. Live routes
are deliberately not inferred from aircraft observations.
"""
from __future__ import annotations
import json
import sys
from feed_support import airport_reference
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data.json"
OUT = ROOT / "data" / "transport_network.json"

def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    countries = {c.get("id"): c for c in data.get("countries", []) if c.get("id")}
    locations = data.get("locations", [])
    airports = []
    reference_health = {'status': 'healthy'}
    # OurAirports uses ISO alpha-2; TI's country.id carries that same code.
    country_map = {cid.upper(): cid for cid in countries}
    try:
        reference = airport_reference()
        for row in reference['rows']:
            cid = country_map.get(row.get('iso_country'))
            # Travel reference scope: scheduled-service and large/medium airports.
            if not cid or not (row.get('scheduled_service') == 'yes' or row.get('type') in ('large_airport', 'medium_airport')):
                continue
            lat, lon = float(row['latitude_deg']), float(row['longitude_deg'])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError('Airport coordinates out of range')
            airports.append({
                'id': row['ident'], 'name': row['name'], 'countryId': cid,
                'iata': row.get('iata_code') or None, 'icao': row.get('icao_code') or None,
                'latitude': lat, 'longitude': lon,
                'scheduledService': row.get('scheduled_service'), 'type': row['type'],
                'sourceId': 'ourairports-airports'
            })
        if not airports:
            raise ValueError('No airports matched tracked countries')
        reference_health.update(sourceUrl=reference['sourceUrl'], fetchedAt=reference['fetchedAt'],
                                scope='Scheduled-service and large/medium airports in tracked countries')
    except Exception:
        # Never replace usable reference nodes with an empty/partial download.
        previous = json.loads(OUT.read_text()) if OUT.exists() else {}
        previous_airports = previous.get('nodes', {}).get('airports', [])
        if not previous_airports:
            print('::error::Airport reference unavailable and no usable previous network exists; output preserved')
            return 1
        airports = previous_airports
        old_health = previous.get('airportReference', {})
        reference_health = {'status': 'stale', 'error': 'Current airport refresh failed; previous nodes retained',
                            'fetchedAt': old_health.get('fetchedAt', previous.get('generated')),
                            'sourceUrl': old_health.get('sourceUrl')}
        print('::warning::Airport refresh failed; previous airport nodes retained with stale provenance')

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
        "airportReference": reference_health,
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
    sys.exit(main())
