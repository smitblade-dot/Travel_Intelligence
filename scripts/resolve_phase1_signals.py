#!/usr/bin/env python3
"""Resolve Phase 1 signals to TI countries/locations and build review queues.

This does not publish events. It creates deterministic, reviewable candidates
from machine-collected signals. Human/AI verification against primary sources
remains a separate step.
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data.json"
SIGNALS = ROOT / "data" / "phase1_signals.json"
OUT = ROOT / "data" / "phase1_review_queue.json"

COUNTRY_ALIASES = {
    "iraq": "iq", "kuwait": "kw", "libya": "ly", "guyana": "gy",
    "cyprus": "cy", "qatar": "qa", "saudi arabia": "sa",
    "united arab emirates": "ae", "uae": "ae", "oman": "om",
    "jordan": "jo", "lebanon": "lb", "iran": "ir", "israel": "il",
    "turkey": "tr", "türkiye": "tr", "greece": "gr", "nigeria": "ng",
    "angola": "ao", "ghana": "gh", "colombia": "co", "ecuador": "ec",
    "peru": "pe", "suriname": "sr", "gabon": "ga", "cameroon": "cm",
    "chad": "td", "tunisia": "tn", "uganda": "ug", "sudan": "sd",
    "south sudan": "ss", "mauritania": "mr", "senegal": "sn",
    "argentina": "ar", "australia": "au", "azerbaijan": "az",
    "bahrain": "bh", "pakistan": "pk", "kuwait": "kw",
    "united kingdom": "gb", "uk": "gb", "france": "fr", "germany": "de",
    "netherlands": "nl", "italy": "it", "spain": "es", "portugal": "pt",
}

KEYWORDS = {
    "AIRPORT": ["airport", "airfield", "terminal", "runway"],
    "AIRSPACE": ["airspace", "notam", "flight restriction", "no-fly"],
    "BORDER": ["border", "border crossing", "checkpoint", "crossing closed"],
    "ROAD": ["road", "highway", "motorway", "bridge", "route", "traffic"],
    "RAIL": ["rail", "railway", "train", "metro"],
    "PORT": ["port", "harbour", "harbor", "ferry"],
    "DRONE": ["drone", "uav", "uas", "unmanned aircraft"],
    "FLOOD": ["flood", "river level", "flooding"],
    "FIRE": ["wildfire", "forest fire", "fire", "burning"],
    "SECURITY": ["attack", "riot", "protest", "explosion", "shooting", "militia"],
    "WEATHER": ["cyclone", "hurricane", "storm", "typhoon", "extreme weather"],
    "EARTHQUAKE": ["earthquake", "seismic", "tsunami"],
}

def norm(s):
    return re.sub(r"\s+", " ", str(s or "").lower()).strip()

def country_ids(text, known):
    t = norm(text)
    hits = []
    for alias, cid in COUNTRY_ALIASES.items():
        if alias in t and cid in known and cid not in hits:
            hits.append(cid)
    return hits

def categories(text):
    t = norm(text)
    return [k for k, words in KEYWORDS.items() if any(w in t for w in words)]

def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    signals = json.loads(SIGNALS.read_text(encoding="utf-8"))
    known = {c.get("id") for c in data.get("countries", []) if c.get("id")}
    queue = []
    now = datetime.now(timezone.utc).isoformat()

    # GDELT: retain article-level leads, without treating them as events.
    gdelt = signals.get("sources", {}).get("gdelt-events", {})
    if gdelt.get("ok"):
        payload = gdelt.get("data", {})
        for article in payload.get("articles", [])[:50]:
            text = " ".join([
                article.get("title", ""), article.get("seendate", ""),
                article.get("domain", ""), article.get("url", "")
            ])
            cids = country_ids(text, known)
            cats = categories(text)
            if cids and cats:
                queue.append({
                    "id": f"sig-gdelt-{len(queue)+1:04d}",
                    "sourceId": "gdelt-events",
                    "sourceUrl": article.get("url"),
                    "discoveryType": "NEWS_EVENT",
                    "countryIds": cids,
                    "candidateCategories": cats,
                    "claim": article.get("title"),
                    "observedAt": article.get("seendate"),
                    "status": "NEEDS_PRIMARY_SOURCE_VERIFICATION",
                    "generatedAt": now,
                })

    # Radiological watchpoints: only candidate anomalies enter the review queue.
    # These remain signals until authoritative corroboration is found.
    radiation_path = ROOT / "data" / "radiological_signals.json"
    if radiation_path.exists():
        try:
            radiation = json.loads(radiation_path.read_text(encoding="utf-8"))
            for wp in radiation.get("watchpoints", []):
                if not wp.get("candidateAnomaly"):
                    continue
                queue.append({
                    "id": f"sig-radiological-{wp.get('id')}",
                    "sourceId": "safecast-radiation",
                    "sourceUrl": wp.get("sourceUrl"),
                    "discoveryType": "RADIATION_ANOMALY",
                    "countryIds": country_ids(wp.get("country", ""), known),
                    "candidateCategories": ["RADIOLOGICAL_NUCLEAR"],
                    "location": {
                        "name": wp.get("name"),
                        "country": wp.get("country"),
                    },
                    "claim": "Safecast measurements near a monitored nuclear/radiological watchpoint show a candidate deviation from the established local baseline.",
                    "analysis": wp.get("analysis"),
                    "status": "NEEDS_PRIMARY_SOURCE_VERIFICATION",
                    "generatedAt": now,
                })
        except Exception as exc:
            print(f"Radiological signal file could not be resolved: {exc}")

    # Other feeds are retained as source-level summaries for later specialist
    # resolvers. This keeps the review queue small and deterministic.
    for source_id, item in signals.get("sources", {}).items():
        if source_id == "gdelt-events" or not item.get("ok"):
            continue
        queue.append({
            "id": f"sig-{source_id}",
            "sourceId": source_id,
            "discoveryType": "MACHINE_SIGNAL",
            "countryIds": [],
            "candidateCategories": [],
            "claim": "Usable source response collected; specialist geographic/event resolver required.",
            "status": "READY_FOR_RESOLUTION",
            "generatedAt": now,
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "schemaVersion": "1.0",
        "generated": now,
        "purpose": "Review queue only; no item is a published TI event.",
        "items": queue
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT} with {len(queue)} review items")

if __name__ == "__main__":
    main()
