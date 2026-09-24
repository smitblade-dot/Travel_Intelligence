#!/usr/bin/env python3
"""Validate TI source due-diligence and change-detection artifacts."""
from __future__ import annotations
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def fail(m):
    print("ERROR:",m)
    return 1

def main():
    cfg=json.loads((ROOT/"config/source_registry.json").read_text(encoding="utf-8"))
    legal=set(cfg["legalCategories"]); tiers=set(cfg["tiers"])
    ids=set()
    for s in cfg["sources"]:
        sid=s.get("id")
        if not sid or sid in ids:
            return fail(f"duplicate/missing source id: {sid}")
        ids.add(sid)
        tier=s.get("tier")
        if tier not in tiers:
            return fail(f"{sid}: invalid tier")
        if s.get("legalCategory") not in legal:
            return fail(f"{sid}: invalid legalCategory")
        if not s.get("accessMethod"):
            return fail(f"{sid}: accessMethod required")
        if not s.get("status"):
            return fail(f"{sid}: status required")
        if not s.get("independenceGroupId"):
            return fail(f"{sid}: independenceGroupId required")

        # Enforce the source strategy at the registry boundary. Tier 3 is
        # discovery/OSINT only and must never be represented as a publishable
        # primary source; Tier 4 is commercial and intentionally out of scope
        # for the current product phase.
        if tier == "TIER_3":
            if s.get("legalCategory") != "DISCOVERY_ONLY":
                return fail(f"{sid}: Tier 3 must use DISCOVERY_ONLY")
            if s.get("automationPermission") == "PERMITTED":
                return fail(f"{sid}: Tier 3 cannot be automation-permitted")
        elif tier == "TIER_4":
            if s.get("legalCategory") not in {"PAID_COMMERCIAL","DISCOVERY_ONLY"}:
                return fail(f"{sid}: Tier 4 must be commercial/discovery-only")
        else:
            if s.get("legalCategory") == "PAID_COMMERCIAL":
                return fail(f"{sid}: paid-commercial source cannot be Tier 1-2")

    snap=ROOT/"data/source_snapshots.json"
    if snap.exists():
        data=json.loads(snap.read_text(encoding="utf-8"))
        if str(data.get("schemaVersion"))!="1.0":
            return fail("source_snapshots schemaVersion must be 1.0")
    queue=ROOT/"data/government_change_queue.json"
    if queue.exists():
        q=json.loads(queue.read_text(encoding="utf-8"))
        if str(q.get("schemaVersion"))!="1.0":
            return fail("government_change_queue schemaVersion must be 1.0")
    print(f"VALID: source registry={len(cfg['sources'])} | tiers={len(cfg['tiers'])}")
    return 0

if __name__=="__main__":
    sys.exit(main())
