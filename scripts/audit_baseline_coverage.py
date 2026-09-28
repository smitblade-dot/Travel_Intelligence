#!/usr/bin/env python3
"""Audit TI baseline coverage across every active country and canonical category."""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data.json"
CATEGORIES = [
    "SECURITY", "ENTRY", "HEALTH", "EMERGENCY", "TRANSPORT", "ENVIRONMENT",
    "INSURANCE", "LAWS_CULTURE", "COMMUNICATIONS", "FINANCE", "LANGUAGE",
    "ACCOMMODATION", "EQUIPMENT", "TRAINING"
]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    d = json.loads(DATA.read_text(encoding="utf-8"))
    countries = d["countries"]
    records = d["records"]
    covered = {
        (str(r.get("countryId")).lower(), r.get("category"))
        for r in records
        if r.get("status", "ACTIVE") == "ACTIVE"
    }
    missing = []
    for c in countries:
        cid = str(c["id"]).lower()
        for cat in CATEGORIES:
            if (cid, cat) not in covered:
                missing.append((cid, c["name"], cat))

    total = len(countries) * len(CATEGORIES)
    covered_count = total - len(missing)
    pct = (100 * covered_count / total) if total else 100.0
    print(f"BASELINE COVERAGE: {covered_count}/{total} ({pct:.1f}%)")

    if missing:
        print(f"MISSING COMBINATIONS: {len(missing)}")
        for cid, name, cat in missing:
            print(f"- {cid}\t{name}\t{cat}")
    else:
        print("PASS: 100% country/category baseline coverage.")
    return 1 if args.strict and missing else 0

if __name__ == "__main__":
    sys.exit(main())
