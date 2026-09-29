#!/usr/bin/env python3
"""Audit TI baseline coverage across every active country and canonical category."""
import argparse, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data.json"
CATEGORIES=["SECURITY","ENTRY","HEALTH","EMERGENCY","TRANSPORT","ENVIRONMENT","INSURANCE","LAWS_CULTURE","COMMUNICATIONS","FINANCE","LANGUAGE","ACCOMMODATION","EQUIPMENT","TRAINING"]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--strict",action="store_true")
    args=ap.parse_args()
    d=json.loads(DATA.read_text(encoding="utf-8"))
    countries=[c for c in d["countries"] if c.get("status","ACTIVE")=="ACTIVE"]
    records=d["records"]
    sources={s.get("id"):s for s in d.get("sources",[]) if s.get("id")}
    observations=d.get("sourceObservations",[])
    evidenced=set()
    for observation in observations:
        record_id=observation.get("recordId")
        source_id=observation.get("sourceId")
        source_url=str(observation.get("sourceUrl") or "")
        if record_id and source_id and source_url.startswith(("https://","http://")) and observation.get("extractedClaim"):
            evidenced.add((record_id,source_id))
    # Count a country/category as covered only when an active baseline record
    # points to an actual source with a retrievable URL. This keeps the gate
    # from accepting placeholder records or dangling provenance.
    covered=set()
    unevidenced=[]
    for r in records:
        if r.get("status","ACTIVE")!="ACTIVE":
            continue
        key=(str(r.get("countryId")).lower(),r.get("category"))
        source=sources.get(r.get("sourceId"))
        source_url=str((source or {}).get("url", ""))
        if source_url.startswith(("https://", "http://")) and (r.get("id"),r.get("sourceId")) in evidenced:
            covered.add(key)
        elif key[1] in CATEGORIES:
            unevidenced.append((key[0],r.get("id"),r.get("sourceId")))
    missing=[]
    for c in countries:
        cid=str(c["id"]).lower()
        for cat in CATEGORIES:
            if (cid,cat) not in covered:
                missing.append((cid,c["name"],cat))
    total=len(countries)*len(CATEGORIES)
    covered_count=total-len(missing)
    pct=(100*covered_count/total) if total else 100.0
    print(f"BASELINE COVERAGE: {covered_count}/{total} ({pct:.1f}%)")
    if missing:
        print(f"MISSING COMBINATIONS: {len(missing)}")
        for cid,name,cat in missing:
            print(f"- {cid}\t{name}\t{cat}")
    else:
        print("PASS: 100% country/category baseline coverage.")
    if unevidenced:
        print(f"ACTIVE BASELINE RECORDS WITHOUT SOURCE URL AND MATCHING OBSERVATION: {len(unevidenced)}")
        for cid,rid,sid in unevidenced:
            print(f"- {cid}\t{rid}\tsourceId={sid}")
    return 1 if args.strict and (missing or unevidenced) else 0

if __name__=="__main__":
    sys.exit(main())
