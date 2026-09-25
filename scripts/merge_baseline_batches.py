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
    added=updated=0
    for p in sorted(BATCH_DIR.glob("*.json")):
        batch=json.loads(p.read_text(encoding="utf-8"))
        for s in batch.get("sources",[]):
            if s.get("id"): sources[s["id"]]=s
        for r in batch.get("records",[]):
            if r.get("category") not in CATEGORIES or not r.get("countryId") or not r.get("id"):
                raise SystemExit(f"Invalid baseline record in {p}: {r.get('id')}")
            if r["id"] in records: updated += 1
            else: added += 1
            records[r["id"]]=r
        for o in batch.get("sourceObservations",[]):
            if o.get("id"): observations[o["id"]]=o
    d["records"]=list(records.values())
    d["sources"]=list(sources.values())
    d["sourceObservations"]=list(observations.values())
    d["meta"]["counts"]["records"]=len(d["records"])
    d["meta"]["counts"]["sources"]=len(d["sources"])
    d["meta"]["counts"]["sourceObservations"]=len(d["sourceObservations"])
    d["meta"]["generated"]=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    DATA.write_text(json.dumps(d,ensure_ascii=False,separators=(",",":"))+"\n",encoding="utf-8")
    print(f"Merged baseline batches: added={added} updated={updated} records={len(d['records'])} sources={len(d['sources'])} observations={len(d['sourceObservations'])}")
if __name__=="__main__": main()
