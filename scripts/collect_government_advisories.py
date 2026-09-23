#!/usr/bin/env python3
"""Collect structured government travel-advisory changes for TI review.

This is a discovery/change-detection layer. It never writes public TI records
or events. It stores only normalized factual metadata needed to detect a
change and verify the original government page later.
"""
from __future__ import annotations
import hashlib, json, re, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "source_registry.json"
SNAPSHOT = ROOT / "data" / "source_snapshots.json"
QUEUE = ROOT / "data" / "government_change_queue.json"
TIMEOUT = 20
UA = "TravelIntelligence/0.1 (+https://github.com/smitblade-dot/Travel_Intelligence)"

FEEDS = {
    "us-state-travel-advisories": [
        "https://travel.state.gov/_res/rss/TAsTWs.xml",
        "https://travel.state.gov/content/travel/en/rss.xml",
    ],
    "canada-travel-advice": [
        "https://travel.gc.ca/rss",
        "https://travel.gc.ca/rss/travel-advisories",
    ],
    "australia-smartraveller": [
        "https://www.smartraveller.gov.au/destinations-export",
    ],
}

def fetch(url):
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/xml, application/json, text/xml, text/html"})
    with urlopen(req, timeout=TIMEOUT) as r:
        return r.read(), r.headers.get("content-type","")

def clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(s or ""))).strip()

def parse_xml(raw):
    root=ET.fromstring(raw)
    items=[]
    for item in root.iter():
        tag=item.tag.rsplit("}",1)[-1].lower()
        if tag in ("item","entry"):
            d={}
            for child in item:
                k=child.tag.rsplit("}",1)[-1].lower()
                if k in ("title","link","pubdate","published","updated","description","summary","id"):
                    if k=="link":
                        href=child.attrib.get("href") or (child.text or "")
                        d[k]=href
                    else: d[k]=clean(child.text)
            if d.get("title") or d.get("link"): items.append(d)
    return items

def parse_json(raw):
    obj=json.loads(raw)
    items=[]
    def walk(x):
        if isinstance(x, dict):
            keys={str(k).lower() for k in x}
            if keys & {"destination","country","name"} and keys & {"updated","last_updated","overall_advice_level","advice_level","title"}:
                items.append(x)
            for v in x.values(): walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
    walk(obj)
    return items

def extract_level(d):
    for k in ("overall_advice_level","advice_level","level","risk_level"):
        if d.get(k): return clean(d.get(k))
    text=clean(" ".join(str(v) for v in d.values() if isinstance(v,(str,int))))
    m=re.search(r"(Do not travel|Reconsider travel|Exercise a high degree of caution|Exercise increased caution|Exercise normal safety precautions|Avoid all travel|Avoid non-essential travel|Take normal security precautions)", text, re.I)
    return m.group(1) if m else None

def extract_name(d):
    for k in ("destination","country","name","title"):
        if d.get(k): return clean(d.get(k))
    return None

def extract_url(d):
    for k in ("url","link","href","source_url"):
        if d.get(k): return str(d[k])
    return None

def normalize(source_id, item):
    name=extract_name(item) or ""
    url=extract_url(item)
    updated=next((clean(item.get(k)) for k in ("updated","last_updated","pubdate","published","date") if item.get(k)), None)
    level=extract_level(item)
    title=clean(item.get("title") or name)
    basis=json.dumps({"name":name,"url":url,"updated":updated,"level":level,"title":title},sort_keys=True,ensure_ascii=False)
    return {
        "sourceId":source_id,
        "destination":name,
        "title":title,
        "url":url,
        "updated":updated,
        "advisoryLevel":level,
        "contentHash":hashlib.sha256(basis.encode("utf-8")).hexdigest(),
    }

def main():
    now=datetime.now(timezone.utc).isoformat()
    old=json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {"sources":{}}
    new={"schemaVersion":"1.0","generated":now,"sources":{}}
    changes=[]
    for sid,urls in FEEDS.items():
        raw_items=[]; used=None; errors=[]
        for url in urls:
            try:
                raw,ctype=fetch(url)
                if "json" in ctype or raw.lstrip().startswith((b"{",b"[")):
                    raw_items=parse_json(raw)
                else:
                    raw_items=parse_xml(raw)
                if raw_items:
                    used=url; break
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        normalized=[]
        for item in raw_items:
            try:
                n=normalize(sid,item)
                if n["destination"] or n["url"]:
                    normalized.append(n)
            except Exception:
                continue
        by_key={f'{x["destination"].lower()}|{x["url"] or x["title"]}':x for x in normalized}
        if errors and not by_key and used is None:\n            # Preserve the last known snapshot when every fetch attempt failed.\n            # A temporary outage must not turn into false NEW alerts later.\n            previous=old.get("sources",{}).get(sid,{})\n            new["sources"][sid]={\n                "feedUrl":previous.get("feedUrl"),\n                "itemCount":len(previous.get("items",{})),\n                "items":previous.get("items",{}),\n                "errors":errors,\n            }\n        else:\n            new["sources"][sid]={"feedUrl":used,"itemCount":len(by_key),"items":by_key,"errors":errors}
        old_items=old.get("sources",{}).get(sid,{}).get("items",{})
        for key,item in by_key.items():
            prev=old_items.get(key)
            if not prev:
                change="NEW"
            elif prev.get("contentHash") != item.get("contentHash"):
                change="UPDATED"
            else:
                continue
            changes.append({
                "id":f"gov-{sid}-{hashlib.sha1(key.encode()).hexdigest()[:12]}",
                "sourceId":sid,
                "changeType":change,
                "destination":item["destination"],
                "title":item["title"],
                "sourceUrl":item["url"],
                "observedUpdated":item["updated"],
                "advisoryLevel":item["advisoryLevel"],
                "status":"NEEDS_PRIMARY_SOURCE_VERIFICATION",
                "generatedAt":now
            })
    new["generated"]=now
    SNAPSHOT.write_text(json.dumps(new,ensure_ascii=False,indent=2),encoding="utf-8")
    QUEUE.write_text(json.dumps({
        "schemaVersion":"1.0",
        "generated":now,
        "purpose":"Government advisory change queue. Discovery only; no public TI event or record is created automatically.",
        "items":changes
    },ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Government advisory collection complete: {len(changes)} changes across {len(FEEDS)} sources")
    for sid in FEEDS:
        info=new["sources"].get(sid,{})
        print(f"{sid}: {info.get('itemCount',0)} items; feed={info.get('feedUrl') or 'NONE'}")
    return 0

if __name__=="__main__":
    sys.exit(main())
