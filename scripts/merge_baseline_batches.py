#!/usr/bin/env python3
"""Merge generated baseline batch files into data.json without replacing existing TI data."""
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data.json"
BATCH_DIR=ROOT/"data"/"baseline_batches"
CATEGORIES=["SECURITY","ENTRY","HEALTH","EMERGENCY","TRANSPORT","ENVIRONMENT","INSURANCE","LAWS_CULTURE","COMMUNICATIONS","FINANCE","LANGUAGE","ACCOMMODATION","EQUIPMENT","TRAINING"]

def main():
    d=json.loads(DATA.read_text(encoding="utf-8"))
    records={r["id"]:r for r in d["records"] if r.get("id")}
    sources={s["id"]:s for s in d["sources"] if s.get("id")}
    observations={o["id"]:o for o in d["sourceObservations"] if o.get("id")}
    active_countries={str(c.get("id","")).lower() for c in d.get("countries",[]) if c.get("status","ACTIVE")=="ACTIVE"}
    added=updated=preserved_newer=0
    for p in sorted(BATCH_DIR.glob("*.json")):
        batch=json.loads(p.read_text(encoding="utf-8"))
        batch_sources={s.get("id"):s for s in batch.get("sources",[]) if s.get("id")}
        if len(batch_sources)!=len(batch.get("sources",[])):
            raise SystemExit(f"Duplicate or missing source IDs in {p}")
        if batch.get("records",[]) and p.stem.upper() not in CATEGORIES:
            raise SystemExit(f"Unexpected baseline batch filename: {p.name}")
        batch_source_ids=set(batch_sources)
        batch_observations=batch.get("sourceObservations",[])
        observed={(o.get("recordId"),o.get("sourceId")) for o in batch_observations
                  if str(o.get("sourceUrl") or "").startswith(("https://","http://"))
                  and o.get("extractedClaim")}
        for s in batch_sources.values():
            sid=s.get("id")
            if not sid:
                continue
            if not str(s.get("url") or "").startswith(("https://","http://")):
                raise SystemExit(f"Baseline source {sid} in {p} must include an HTTP(S) URL")
            current=sources.get(sid)
            if current and str(current.get("url") or "") != str(s.get("url") or ""):
                raise SystemExit(f"Source ID collision with a different URL for {sid} in {p}; refusing to change provenance")
            incoming_date=s.get("dateChecked") or ""
            current_date=(current or {}).get("dateChecked") or ""
            if current is None or (incoming_date and incoming_date>current_date):
                sources[sid]=s
            else:
                preserved_newer+=1
        for r in batch.get("records",[]):
            if r.get("category") not in CATEGORIES or not r.get("countryId") or not r.get("id"):
                raise SystemExit(f"Invalid baseline record in {p}: {r.get('id')}")
            if str(r["countryId"]).lower() not in active_countries:
                raise SystemExit(f"Baseline record {r['id']} in {p} targets a country that is not active")
            if not r.get("sourceId") or r["sourceId"] not in batch_source_ids:
                raise SystemExit(f"Baseline record {r['id']} in {p} must include its real source in the batch")
            if (r["id"],r["sourceId"]) not in observed:
                raise SystemExit(f"Baseline record {r['id']} in {p} must include a matching SourceObservation")
            current=records.get(r["id"])
            if current is None:
                records[r["id"]]=r
                added+=1
            else:
                incoming_date=r.get("dateChecked") or ""
                current_date=current.get("dateChecked") or ""
                # Artifacts are generated from an earlier main snapshot. Do
                # not let them roll back a production item; only a strictly
                # newer source-check date can replace the current record.
                if incoming_date and incoming_date>current_date:
                    records[r["id"]]=r
                    updated+=1
                else:
                    preserved_newer+=1
        for o in batch.get("sourceObservations",[]):
            if o.get("id") and o["id"] not in observations:
                observations[o["id"]]=o
            elif o.get("id"):
                preserved_newer+=1
    d["records"]=list(records.values())
    d["sources"]=list(sources.values())
    d["sourceObservations"]=list(observations.values())
    d["meta"]["counts"]["records"]=len(d["records"])
    d["meta"]["counts"]["sources"]=len(d["sources"])
    d["meta"]["counts"]["sourceObservations"]=len(d["sourceObservations"])
    d["meta"]["generated"]=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    DATA.write_text(json.dumps(d,ensure_ascii=False,separators=(",",":"))+"\n",encoding="utf-8")
    print(f"Merged baseline batches: added={added} updated={updated} preserved_existing={preserved_newer} records={len(d['records'])} sources={len(d['sources'])} observations={len(d['sourceObservations'])}")
if __name__=="__main__": main()
