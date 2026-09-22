#!/usr/bin/env python3
"""Build a deterministic TI infrastructure relationship graph.

The graph is derived from existing TI locations/countries plus event
infrastructure references. It is a routing/intelligence aid, not a source of
truth and does not publish or alter events.
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data.json"
OUT = ROOT / "data" / "infrastructure_graph.json"

TYPE_RULES = [
    ("AIRPORT", ["airport", "airfield", "aerodrome", "aviation"]),
    ("PORT", ["port", "harbour", "harbor", "terminal", "jetty", "marine"]),
    ("PIPELINE", ["pipeline", "petroline", "corridor", "gas line", "oil line"]),
    ("OILFIELD", ["oilfield", "oil field", "field"]),
    ("GASFIELD", ["gasfield", "gas field"]),
    ("BORDER", ["border", "crossing", "checkpoint"]),
    ("ROAD", ["road", "highway", "motorway", "corridor"]),
    ("RAIL", ["rail", "railway", "station"]),
    ("CITY", ["city", "capital"]),
    ("WORKSITE", ["project", "site", "camp", "facility", "refinery"]),
]

def norm(v):
    return re.sub(r"\s+", " ", str(v or "").lower()).strip()

def location_name(x):
    return x.get("name") or x.get("title") or x.get("locationName") or x.get("label") or ""

def infer_types(x):
    text = norm(" ".join(str(x.get(k, "")) for k in
                         ("name", "title", "locationName", "label", "type",
                          "category", "description", "notes")))
    hits = []
    for typ, words in TYPE_RULES:
        if any(w in text for w in words):
            hits.append(typ)
    return hits or ["OTHER"]

def first(x, *keys):
    for k in keys:
        if x.get(k) not in (None, ""):
            return x[k]
    return None

def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    locations = data.get("locations", [])
    countries = {c.get("id"): c for c in data.get("countries", [])}
    events = data.get("events", [])

    nodes = []
    by_country = {}
    by_type = {}
    for loc in locations:
        lid = loc.get("id")
        if not lid:
            continue
        country_id = first(loc, "countryId", "country_id")
        node = {
            "id": lid,
            "name": location_name(loc),
            "countryId": country_id,
            "types": infer_types(loc),
            "latitude": first(loc, "latitude", "lat"),
            "longitude": first(loc, "longitude", "lon", "lng"),
        }
        nodes.append(node)
        if country_id:
            by_country.setdefault(country_id, []).append(lid)
        for typ in node["types"]:
            by_type.setdefault(typ, []).append(lid)

    # Preserve explicit event -> infrastructure relationships already present.
    event_links = []
    node_ids = {n["id"] for n in nodes}
    for event in events:
        for infra in event.get("affectedInfrastructure", []) or []:
            event_links.append({
                "eventId": event.get("id"),
                "infrastructure": infra,
                "locationId": event.get("locationId"),
                "countryId": event.get("countryId"),
                "status": "EXPLICIT_EVENT_RELATIONSHIP",
                "resolvedLocation": event.get("locationId") in node_ids,
            })

    # Country-level fallback relationships are deliberately broad: they
    # identify candidate infrastructure, not an affected asset.
    country_index = []
    for cid, country in countries.items():
        country_index.append({
            "countryId": cid,
            "countryName": country.get("name"),
            "locationIds": by_country.get(cid, []),
            "infrastructureTypes": sorted({
                t for lid in by_country.get(cid, [])
                for n in nodes if n["id"] == lid for t in n["types"]
            }),
        })

    output = {
        "schemaVersion": "1.0",
        "generated": datetime.now(timezone.utc).isoformat(),
        "purpose": "Deterministic relationship graph for travel-impact resolution; not a source of truth.",
        "nodes": nodes,
        "countryIndex": country_index,
        "eventInfrastructureLinks": event_links,
        "resolutionRules": {
            "explicitLocation": "Highest precision",
            "explicitAffectedInfrastructure": "Use when supplied by verified event",
            "countryFallback": "Candidate scope only; never means the asset is affected",
            "unresolved": "Keep null rather than inventing a location",
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}: {len(nodes)} nodes, {len(event_links)} event links")

if __name__ == "__main__":
    main()
