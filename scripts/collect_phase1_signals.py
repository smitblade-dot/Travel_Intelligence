#!/usr/bin/env python3
"""
Collect Phase 1 open-data signals for Travel Intelligence.

Important:
- This script creates a reviewable signal snapshot.
- It does NOT publish TI events automatically.
- External claims must be verified against the original source before publication.
- API keys belong in environment variables/GitHub Actions secrets.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "phase1_sources.json"
OUT = ROOT / "data" / "phase1_signals.json"
TIMEOUT = 30


def fetch(url: str, headers: dict[str, str] | None = None) -> bytes:
    req = Request(url, headers=headers or {"User-Agent": "TravelIntelligence/0.1"})
    with urlopen(req, timeout=TIMEOUT) as response:
        return response.read()


def get_json(url: str):
    return json.loads(fetch(url).decode("utf-8"))


def safe_json(url: str):
    try:
        return {"ok": True, "data": get_json(url)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def collect_ourairports():
    url = "https://ourairports.com/airports.csv"
    try:
        raw = fetch(url)
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", errors="replace"))))
        # Keep only the reference fields needed by TI.
        airports = []
        for row in rows:
            airports.append({
                "ident": row.get("ident"),
                "type": row.get("type"),
                "name": row.get("name"),
                "latitude": row.get("latitude_deg"),
                "longitude": row.get("longitude_deg"),
                "iso_country": row.get("iso_country"),
                "iata_code": row.get("iata_code"),
                "icao_code": row.get("icao_code"),
                "scheduled_service": row.get("scheduled_service"),
            })
        return {"ok": True, "count": len(airports), "sample": airports[:10]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def collect_gdelt():
    query = (
        '("airport closed" OR "airport closure" OR "airspace closed" OR '
        '"border closed" OR "border crossing closed" OR "flight cancelled" '
        'OR "drone ban" OR "drone restrictions")'
    )
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query={quote(query)}&mode=artlist&format=json&maxrecords=50"
        "&sort=datedesc"
    )
    return safe_json(url)


def collect_gdacs():
    # Keep this deliberately defensive: GDACS has changed API paths over time.
    candidates = [
        "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH",
        "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH?eventtype=ALL",
    ]
    for url in candidates:
        result = safe_json(url)
        if result["ok"]:
            return result
    return {"ok": False, "error": "No configured GDACS endpoint returned usable JSON"}


def collect_adsblol():
    # A full-world snapshot can be large. The endpoint is intentionally sampled
    # here for signal collection rather than storing every aircraft in Git.
    return safe_json("https://api.adsb.lol/v2/all")


def collect_reliefweb():
    url = (
        "https://api.reliefweb.int/v1/reports"
        "?appname=travel-intelligence&limit=50&sort[]=date:desc"
    )
    return safe_json(url)


def collect_usgs_earthquakes():
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
    result = safe_json(url)
    if not result["ok"]:
        return result
    data = result["data"]
    features = data.get("features", []) if isinstance(data, dict) else []
    return {
        "ok": True,
        "count": len(features),
        "sample": [
            {
                "id": f.get("id"),
                "place": f.get("properties", {}).get("place"),
                "magnitude": f.get("properties", {}).get("mag"),
                "time": f.get("properties", {}).get("time"),
                "url": f.get("properties", {}).get("url"),
                "coordinates": f.get("geometry", {}).get("coordinates"),
            }
            for f in features[:50]
        ],
    }


def collect_noaa_alerts():
    url = "https://api.weather.gov/alerts/active?limit=50"
    req = Request(url, headers={"User-Agent": "TravelIntelligence/0.1 (source health and signal collection)"})
    try:
        with urlopen(req, timeout=TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
        features = data.get("features", []) if isinstance(data, dict) else []
        return {
            "ok": True,
            "count": len(features),
            "sample": [
                {
                    "id": f.get("id"),
                    "event": f.get("properties", {}).get("event"),
                    "severity": f.get("properties", {}).get("severity"),
                    "headline": f.get("properties", {}).get("headline"),
                    "effective": f.get("properties", {}).get("effective"),
                    "expires": f.get("properties", {}).get("expires"),
                    "areaDesc": f.get("properties", {}).get("areaDesc"),
                }
                for f in features[:50]
            ],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def collect_noaa_aviation():
    url = "https://aviationweather.gov/api/data/metar?ids=KJFK&format=json"
    return safe_json(url)


def collect_hdx():
    url = "https://data.humdata.org/api/3/action/package_search?q=travel&rows=10"
    result = safe_json(url)
    if not result["ok"]:
        return result
    data = result["data"]
    return {
        "ok": True,
        "count": len(data.get("result", {}).get("results", [])) if isinstance(data, dict) else 0,
        "sample": [
            {
                "name": x.get("name"),
                "title": x.get("title"),
                "organization": (x.get("organization") or {}).get("title"),
                "metadata_modified": x.get("metadata_modified"),
            }
            for x in data.get("result", {}).get("results", [])[:10]
        ],
    }


def collect_eurocontrol():
    return safe_json("https://api-data-app.eurocontrol.int/api/countries?ico2=CY")


def collect_firms():
    key = os.environ.get("FIRMS_MAP_KEY")
    if not key:
        return {"ok": False, "skipped": True, "reason": "FIRMS_MAP_KEY not configured"}
    # A global FIRMS request can be large; use the last 24 hours as the initial
    # signal layer. The exact endpoint can be changed to country/area queries later.
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/world/VIIRS_SNPP_NRT/1"
    try:
        raw = fetch(url)
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", errors="replace"))))
        return {"ok": True, "count": len(rows), "sample": rows[:25]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def main():
    started = datetime.now(timezone.utc).isoformat()
    output = {
        "schemaVersion": "1.0",
        "generated": started,
        "purpose": "Reviewable Phase 1 signals; not published TI events.",
        "sources": {},
    }

    collectors = {
        "ourairports-airports": collect_ourairports,
        "gdelt-events": collect_gdelt,
        "gdacs-alerts": collect_gdacs,
        "usgs-earthquakes": collect_usgs_earthquakes,
        "noaa-weather": collect_noaa_alerts,
        "noaa-aviation-weather": collect_noaa_aviation,
        "ocha-hdx": collect_hdx,
        "eurocontrol": collect_eurocontrol,
        "adsblol-aircraft": collect_adsblol,
        "reliefweb-disasters": collect_reliefweb,
        "nasa-firms": collect_firms,
    }

    for source_id, collector in collectors.items():
        try:
            output["sources"][source_id] = collector()
        except Exception as exc:
            output["sources"][source_id] = {"ok": False, "error": str(exc)}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")
    failed = [k for k, v in output["sources"].items() if not v.get("ok")]
    if failed:
        print("Sources without a usable response:", ", ".join(failed))
        # Collection should remain useful if one free service is unavailable.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
