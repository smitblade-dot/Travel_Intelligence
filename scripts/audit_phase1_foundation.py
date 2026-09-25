#!/usr/bin/env python3
"""Phase 1 foundation audit for Travel Intelligence."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/"data.json"
CATEGORIES=["SECURITY","ENTRY","HEALTH","EMERGENCY","TRANSPORT","ENVIRONMENT","INSURANCE","LAWS_CULTURE","COMMUNICATIONS","FINANCE","LANGUAGE","ACCOMMODATION","EQUIPMENT","TRAINING"]
EVENT_STATUSES={"EARLY_REPORT","REPORTED","LOCAL_REPORT","UNVERIFIED","CORROBORATED","CONFIRMED","DISPUTED","FALSE","CORRECTED","SUPERSEDED","RESOLVED"}
PUBLICATION_STATUSES={"DRAFT","INTERNAL_REVIEW","PUBLISHED","WITHDRAWN"}
def fail(msg): print("FAIL:",msg); return 1
def main():
    try: d=json.loads(DATA.read_text(encoding="utf-8"))
    except Exception as e: return fail(f"data.json is not valid JSON: {e}")
    req=["meta","countries","records","sources","locations","events","sourceObservations","change_log","oilGasSummary"]
    missing=[k for k in req if k not in d]
    if missing: return fail("missing root collections: "+", ".join(missing))
    if str(d["meta"].get("schemaVersion"))!="2.0": return fail("meta.schemaVersion is not 2.0")
    cs=d["countries"]
    if not isinstance(cs,list): return fail("countries must be an array")
    cids=[str(c.get("id","")).lower() for c in cs]
    if not all(cids): return fail("one or more countries has no id")
    if len(cids)!=len(set(cids)): return fail("duplicate country id detected")
    rs,ss,es,os=d["records"],d["sources"],d["events"],d["sourceObservations"]
    sids={s.get("id") for s in ss if s.get("id")}; rids={r.get("id") for r in rs if r.get("id")}; eids={e.get("id") for e in es if e.get("id")}
    for r in rs:
        if r.get("countryId") not in cids: return fail(f"record {r.get('id')} has invalid countryId")
        if r.get("sourceId") and r["sourceId"] not in sids: return fail(f"record {r.get('id')} has invalid sourceId")
        if r.get("category") not in CATEGORIES: return fail(f"record {r.get('id')} has invalid category {r.get('category')!r}")
    for e in es:
        if e.get("countryId") and e["countryId"] not in cids: return fail(f"event {e.get('id')} has invalid countryId")
        if e.get("eventStatus") and e["eventStatus"] not in EVENT_STATUSES: return fail(f"event {e.get('id')} has invalid eventStatus")
        if e.get("publicationStatus") and e["publicationStatus"] not in PUBLICATION_STATUSES: return fail(f"event {e.get('id')} has invalid publicationStatus")
    for o in os:
        if o.get("sourceId") not in sids: return fail(f"observation {o.get('id')} has invalid sourceId")
        if not o.get("sourceUrl") or not o.get("extractedClaim"): return fail(f"observation {o.get('id')} is missing sourceUrl/extractedClaim")
        if int(bool(o.get("recordId")))+int(bool(o.get("eventId")))!=1: return fail(f"observation {o.get('id')} must target exactly one record or event")
        if o.get("recordId") and o["recordId"] not in rids: return fail(f"observation {o.get('id')} has invalid recordId")
        if o.get("eventId") and o["eventId"] not in eids: return fail(f"observation {o.get('id')} has invalid eventId")
        if not o.get("independenceGroupId"): return fail(f"observation {o.get('id')} is missing independenceGroupId")
    expected={"countries":len(cs),"records":len(rs),"sources":len(ss),"locations":len(d["locations"]),"events":len(es),"sourceObservations":len(os)}
    counts=d["meta"].get("counts",{})
    for k,v in expected.items():
        if counts.get(k)!=v: return fail(f"meta.counts.{k}={counts.get(k)!r}, expected {v}")
    published=[e for e in es if e.get("publicationStatus")=="PUBLISHED"]; pids={e.get("id") for e in published}; pob=[o for o in os if o.get("eventId") in pids]
    ci=d["meta"].get("currentIntelligence",{})
    if ci.get("events")!=len(published) or ci.get("sourceObservations")!=len(pob): return fail("meta.currentIntelligence counts do not match published event layer")
    print(f"PASS: Phase 1 foundation | countries={len(cs)} records={len(rs)} sources={len(ss)} locations={len(d['locations'])} events={len(es)} observations={len(os)} publishedEvents={len(published)}")
    return 0
if __name__=="__main__": sys.exit(main())
