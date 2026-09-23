#!/usr/bin/env python3
"""Validate TI source due-diligence and change-detection artifacts."""
from __future__ import annotations
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def fail(m):
    print("ERROR:",m); return 1
def main():
    cfg=json.loads((ROOT/"config/source_registry.json").read_text(encoding="utf-8"))
    legal=set(cfg["legalCategories"]); tiers=set(cfg["tiers"])
    ids=set()
    for s in cfg["sources"]:
        if not s.get("id") or s["id"] in ids: return fail(f"duplicate/missing source id: {s.get('id')}")
        ids.add(s["id"])
        if s.get("tier") not in tiers: return fail(f"{s['id']}: invalid tier")
        if s.get("legalCategory") not in legal: return fail(f"{s['id']}: invalid legalCategory")
        if not s.get("accessMethod"): return fail(f"{s['id']}: accessMethod required")
        if not s.get("status"): return fail(f"{s['id']}: status required")
    snap=ROOT/"data/source_snapshots.json"
    if snap.exists():
        data=json.loads(snap.read_text(encoding="utf-8"))
        if str(data.get("schemaVersion"))!="1.0": return fail("source_snapshots schemaVersion must be 1.0")
    queue=ROOT/"data/government_change_queue.json"
    if queue.exists():
        q=json.loads(queue.read_text(encoding="utf-8"))
        if str(q.get("schemaVersion"))!="1.0": return fail("government_change_queue schemaVersion must be 1.0")
    print(f"VALID: source registry={len(cfg['sources'])} | tiers={len(cfg['tiers'])}")
    return 0
if __name__=="__main__": sys.exit(main())
