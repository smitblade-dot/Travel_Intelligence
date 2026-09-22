#!/usr/bin/env python3
"""Merge the exact Travel Intelligence Claude Artifact export into GitHub data.json.

This is deliberately a data migration, not a reconstruction. The Artifact export is
authoritative for overlapping TI entities; GitHub-only application fields are retained.
No observations are invented for grandfathered baseline records.
"""
from __future__ import annotations
import json, os, sys, tempfile
from collections import Counter

EXPORT = sys.argv[1] if len(sys.argv) > 1 else "ti_full_export.json"
DATA = sys.argv[2] if len(sys.argv) > 2 else "data.json"

COLLECTIONS = ("countries", "records", "sources", "locations", "events", "sourceObservations")
EXPECTED = {"countries":52, "records":360, "sources":123, "locations":14, "events":6, "sourceObservations":6}

def fail(msg):
    print("ERROR:", msg)
    raise SystemExit(1)

def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

artifact = load(EXPORT)
github = load(DATA)

for k in COLLECTIONS:
    if not isinstance(artifact.get(k), list):
        fail(f"Artifact export missing list: {k}")
    actual = len(artifact[k])
    if actual != EXPECTED[k]:
        fail(f"Artifact {k} count {actual}, expected {EXPECTED[k]}")

for k in COLLECTIONS:
    ids=[x.get("id") for x in artifact[k]]
    if any(not i for i in ids):
        fail(f"Artifact {k} contains item without id")
    if len(ids) != len(set(ids)):
        fail(f"Artifact {k} contains duplicate ids")

# Referential integrity in the exact Artifact current-intelligence model.
source_ids={x["id"] for x in artifact["sources"]}
country_ids={x["id"] for x in artifact["countries"]}
event_ids={x["id"] for x in artifact["events"]}
record_ids={x["id"] for x in artifact["records"]}
obs_ids={x["id"] for x in artifact["sourceObservations"]}
for o in artifact["sourceObservations"]:
    if o.get("sourceId") not in source_ids: fail(f"Observation {o['id']} references missing source")
    has_r=bool(o.get("recordId")); has_e=bool(o.get("eventId"))
    if has_r == has_e: fail(f"Observation {o['id']} must target exactly one record or event")
    if has_r and o["recordId"] not in record_ids: fail(f"Observation {o['id']} references missing record")
    if has_e and o["eventId"] not in event_ids: fail(f"Observation {o['id']} references missing event")
for e in artifact["events"]:
    for oid in e.get("sourceObservationIds", []):
        if oid not in obs_ids: fail(f"Event {e['id']} references missing observation {oid}")
for r in artifact["records"]:
    if r.get("countryId") and r["countryId"] not in country_ids:
        fail(f"Record {r['id']} references missing country {r['countryId']}")
    if r.get("sourceId") and r["sourceId"] not in source_ids:
        fail(f"Record {r['id']} references missing source {r['sourceId']}")

def merge_collection(old, new, name):
    old_by={x["id"]:x for x in old if isinstance(x,dict) and x.get("id")}
    conflicts=[]
    added=0
    for item in new:
        iid=item["id"]
        if iid not in old_by:
            old.append(item)
            old_by[iid]=item
            added+=1
            continue
        # Artifact is authoritative for overlapping fields. Preserve GitHub-only fields.
        merged=dict(old_by[iid])
        for key,val in item.items():
            if key in merged and merged[key] != val:
                conflicts.append((iid,key,merged[key],val))
            merged[key]=val
        idx=next(i for i,x in enumerate(old) if x.get("id")==iid)
        old[idx]=merged
    return added, len(new)-added, conflicts

report={}
all_conflicts=[]
for k in COLLECTIONS:
    if not isinstance(github.get(k), list):
        github[k]=[]
    a,o,c=merge_collection(github[k],artifact[k],k)
    report[k]={"added":a,"overlap":o,"fieldConflicts":len(c)}
    all_conflicts.extend((k,)+x for x in c)

# Preserve GitHub-only top-level structures such as change_log and oilGasSummary.
meta=dict(github.get("meta") or {})
meta["schemaVersion"]="2.0"
counts=dict(meta.get("counts") or {})
for k in COLLECTIONS:
    counts[k]=len(github[k])
meta["counts"]=counts
meta["currentIntelligence"]={
    **(meta.get("currentIntelligence") or {}),
    "events": len(github["events"]),
    "sourceObservations": len(github["sourceObservations"]),
}
github["meta"]=meta

fd,tmp=tempfile.mkstemp(prefix="ti-data-",suffix=".json",dir=os.path.dirname(os.path.abspath(DATA)) or ".")
os.close(fd)
try:
    with open(tmp,"w",encoding="utf-8") as f:
        json.dump(github,f,ensure_ascii=False,indent=2)
        f.write("\n")
    os.replace(tmp,DATA)
finally:
    if os.path.exists(tmp): os.unlink(tmp)

print("Artifact export verified:", {k:len(artifact[k]) for k in COLLECTIONS})
print("Merged GitHub counts:", {k:len(github[k]) for k in COLLECTIONS})
print("Merge report:", json.dumps(report,ensure_ascii=False,sort_keys=True))
print("Field conflicts:", len(all_conflicts))
for k,i,key,old,new in all_conflicts[:50]:
    print(f"CONFLICT {k}/{i}: {key} GitHub={old!r} Artifact={new!r}")
if len(all_conflicts)>50:
    print(f"... {len(all_conflicts)-50} additional conflicts omitted")
