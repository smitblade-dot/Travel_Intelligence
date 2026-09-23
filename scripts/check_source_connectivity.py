#!/usr/bin/env python3
"""Check reachability of every registered Tier 1-3 TI source.

This is a connectivity/availability test, not an evidence or rights decision.
A source can be reachable while still requiring human review, API approval or
separate verification before its content is used in public TI intelligence.
"""
from __future__ import annotations
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "source_registry.json"
OUT = ROOT / "data" / "source_health.json"
TIMEOUT = 15
UA = "TravelIntelligence/0.1 (https://github.com/smitblade-dot/Travel_Intelligence; contact: smit_blade@hotmail.com)"

TEST_URLS = {
    "fcdo-govuk": "https://www.gov.uk/api/content/foreign-travel-advice/france",
    "us-state-travel-advisories": "https://travel.state.gov/_res/rss/TAsTWs.xml",
    "canada-travel-advice": "https://travel.gc.ca/travelling/advisories",
    "australia-smartraveller": "https://www.smartraveller.gov.au/destinations",
    "new-zealand-safetravel": "https://www.safetravel.govt.nz/",
    "gdacs-ecjrc": "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH",
    "usgs-earthquakes": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
    "nasa-firms": None,
    "who-disease-outbreak-news": "https://www.who.int/emergencies/disease-outbreak-news",
    "ecdc-threats": "https://www.ecdc.europa.eu/en/rss-feeds",
    "noaa-weather": "https://api.weather.gov/alerts/active?limit=1",
    "noaa-aviation-weather": "https://aviationweather.gov/api/data/metar?ids=KJFK&format=json",
    "copernicus-ems": "https://emergency.copernicus.eu/",
    "ocha-hdx": "https://data.humdata.org/api/3/action/package_search?q=travel",
    "ukmto": "https://www.ukmto.org/ukmto-products/warnings",
    "imo-piracy": "https://www.imo.org/en/ourwork/security/pages/piracy-reports-default.aspx",
    "faa-notam": "https://www.faa.gov/about/initiatives/notam",
    "eurocontrol": "https://api-data-app.eurocontrol.int/api/countries?ico2=CY",
    "airport-authorities": "https://www.aci.aero/",
    "national-road-authorities": "https://www.unece.org/transport",
    "port-authorities": "https://www.imo.org/",
    "gdelt-events": "https://api.gdeltproject.org/api/v2/doc/doc?query=airport%20closure&mode=artlist&format=json&maxrecords=1",
    "local-media": None,
    "social-platforms": None,
    "telegram-public": "https://telegram.org/",
    "reddit": "https://www.reddit.com/",
}

# These are legitimate non-automated source classes. Their landing pages are
# still checked where a canonical URL exists; they do not fail strict mode.
NON_AUTOMATED = {
    "new-zealand-safetravel", "who-disease-outbreak-news", "ecdc-threats",
    "copernicus-ems", "ukmto", "imo-piracy", "faa-notam",
    "airport-authorities", "port-authorities",
    "local-media", "social-platforms", "telegram-public", "reddit", "gdelt-events", "nasa-firms",
}

def fetch(url: str):
    req = Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urlopen(req, timeout=TIMEOUT) as r:
        body = r.read(512)
        return r.status, r.headers.get("content-type", ""), len(body)

def main():
    cfg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    checked = datetime.now(timezone.utc).isoformat()
    results = []
    failures = []

    for s in cfg["sources"]:
        tier = s["tier"]
        if tier == "TIER_4":
            continue
        sid = s["id"]
        url = TEST_URLS.get(sid, s.get("url"))
        item = {
            "sourceId": sid,
            "tier": tier,
            "registryStatus": s.get("status"),
            "automationPermission": s.get("automationPermission"),
            "testUrl": url,
            "checkedAt": checked,
        }

        if sid == "nasa-firms":
            key = os.environ.get("FIRMS_MAP_KEY")
            if not key:
                item.update({"status": "BLOCKED_CONFIGURATION", "detail": "FIRMS_MAP_KEY is not configured"})
                failures.append(sid)
            else:
                test = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/world/VIIRS_SNPP_NRT/1"
                item["testUrl"] = test.replace(key, "***")
                try:
                    code, ctype, size = fetch(test)
                    item.update({"status": "OK" if code < 400 else "HTTP_ERROR", "httpStatus": code, "contentType": ctype, "sampleBytes": size})
                    if code >= 400:
                        failures.append(sid)
                except Exception as exc:
                    item.update({"status": "ERROR", "detail": str(exc)})
                    failures.append(sid)
        elif not url:
            item.update({"status": "NOT_APPLICABLE", "detail": "Source class has no single canonical machine endpoint"})
        else:
            try:
                code, ctype, size = fetch(url)
                status = "OK" if code < 400 else "HTTP_ERROR"
                item.update({"status": status, "httpStatus": code, "contentType": ctype, "sampleBytes": size})
                if code >= 400 and sid not in NON_AUTOMATED:
                    failures.append(sid)
            except Exception as exc:
                item.update({"status": "ERROR", "detail": str(exc)})
                if sid not in NON_AUTOMATED:
                    failures.append(sid)

        results.append(item)

    OUT.write_text(json.dumps({
        "schemaVersion": "1.0",
        "checkedAt": checked,
        "purpose": "Tier 1-3 source reachability report. Reachability does not establish evidence quality, reuse rights or publication authority.",
        "results": results,
        "summary": {
            "tested": len(results),
            "ok": sum(1 for x in results if x["status"] == "OK"),
            "notApplicable": sum(1 for x in results if x["status"] == "NOT_APPLICABLE"),
            "blockedConfiguration": sum(1 for x in results if x["status"] == "BLOCKED_CONFIGURATION"),
            "failures": failures,
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Tier 1-3 source connectivity: tested={len(results)} ok={sum(1 for x in results if x['status']=='OK')} failures={len(failures)}")
    for x in results:
        print(f"{x['tier']} {x['sourceId']}: {x['status']}")
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
