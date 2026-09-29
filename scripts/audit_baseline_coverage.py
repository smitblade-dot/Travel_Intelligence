#!/usr/bin/env python3
"""Audit TI baseline coverage across every active country and canonical category."""
import argparse, hashlib, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data.json"
GRANDFATHERED=ROOT/"config"/"baseline_grandfathered_records.json"
CATEGORIES=["SECURITY","ENTRY","HEALTH","EMERGENCY","TRANSPORT","ENVIRONMENT","INSURANCE","LAWS_CULTURE","COMMUNICATIONS","FINANCE","LANGUAGE","ACCOMMODATION","EQUIPMENT","TRAINING"]

def fingerprint(record, source):
    payload=json.dumps({"record":record,"source":source},ensure_ascii=False,sort_keys=True,separators=(",",":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--strict",action="store_true")
    args=ap.parse_args()
    d=json.loads(DATA.read_text(encoding="utf-8"))
    countries=[c for c in d["countries"] if c.get("status","ACTIVE")=="ACTIVE"]
    records=d["records"]
    sources={s.get("id"):s for s in d.get("sources",[]) if s.get("id")}
    catalog=json.loads(GRANDFATHERED.read_text(encoding="utf-8"))
    grandfathered=catalog.get("records",{})
    observations=d.get("sourceObservations",[])
    covered=set()
    unverified=[]
    for r in records:
        if r.get("status","ACTIVE")!="ACTIVE":
            continue
        key=(str(r.get("countryId")).lower(),r.get("category"))
        source=sources.get(r.get("sourceId"))
        source_url=str((source or {}).get("url", ""))
        has_observation=any(
            o.get("recordId")==r.get("id") and o.get("sourceId")==r.get("sourceId")
            and str(o.get("sourceUrl") or "").startswith(("https://","http://"))
            and o.get("extractedClaim") and o.get("independenceGroupId")
            for o in observations
        )
        is_grandfathered=(source is not None and grandfathered.get(r.get("id"))==fingerprint(r,source))
        if source_url.startswith(("https://", "http://")) and (has_observation or is_grandfathered):
            covered.add(key)
        elif key[1] in CATEGORIES:
            unverified.append((key[0],r.get("id"),r.get("sourceId")))
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
    if unverified:
        print(f"ACTIVE BASELINE RECORDS WITHOUT FINGERPRINTED LEGACY STATUS OR MATCHING SOURCE OBSERVATION: {len(unverified)}")
        for cid,rid,sid in unverified:
            print(f"- {cid}\t{rid}\tsourceId={sid}")
    return 1 if args.strict and (missing or unverified) else 0

if __name__=="__main__":
    sys.exit(main())
