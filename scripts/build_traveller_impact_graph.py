#!/usr/bin/env python3
"""Build the first safe traveller-impact graph for TI.

This layer deliberately models only relationships that are explicit in the
canonical data: location containment, country membership and verified event
location/infrastructure links. It does not invent roads, flight routes or
travel impacts.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data.json"
GRAPH = ROOT / "data" / "infrastructure_graph.json"
OUT = ROOT / "data" / "traveller_impact_graph.json"

def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    graph = json.loads(GRAPH.read_text(encoding="utf-8")) if GRAPH.exists() else {}
    locations = {x.get("id"): x for x in data.get("locations", []) if x.get("id")}
    events = data.get("events", [])

    containment = []
    for loc in locations.values():
        parent = loc.get("parentLocationId")
        if parent and parent in locations:
            containment.append({
                "childLocationId": loc["id"],
                "parentLocationId": parent,
                "relationship": "WITHIN",
            })

    event_impacts = []
    for event in events:
        if event.get("publicationStatus") != "PUBLISHED":
            continue
        location_id = event.get("locationId")
        country_id = event.get("countryId")
        if not location_id and not country_id:
            continue

        chain = []
        cur = location_id
        seen = set()
        while cur and cur in locations and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = locations[cur].get("parentLocationId")

        event_impacts.append({
            "eventId": event.get("id"),
            "countryId": country_id,
            "locationId": location_id,
            "locationChain": chain,
            "affectedInfrastructure": event.get("affectedInfrastructure", []) or [],
            "impactType": event.get("impactType", []) or [],
            "relationshipPrecision": (
                "LOCATION" if location_id else
                "COUNTRY"
            ),
            "travellerImpactStatus": (
                "DIRECT_LOCATION_IMPACT" if location_id else
                "COUNTRY_LEVEL_ONLY"
            ),
        })

    output = {
        "schemaVersion": "1.0",
        "generated": datetime.now(timezone.utc).isoformat(),
        "purpose": "Safe first-stage traveller-impact graph; explicit relationships only.",
        "rules": {
            "locationContainment": "A verified event at a child location may be inherited by its parent locations for discovery.",
            "countryScope": "Country-level events are not assumed to affect every route or infrastructure asset.",
            "routeInference": "No route is inferred until a verified route relationship exists.",
            "travellerImpact": "Direct impact requires an explicit verified event/location relationship.",
        },
        "locationContainment": containment,
        "eventImpacts": event_impacts,
        "infrastructureNodes": graph.get("nodes", []),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}: {len(containment)} containment links, {len(event_impacts)} published event impacts")

if __name__ == "__main__":
    main()
