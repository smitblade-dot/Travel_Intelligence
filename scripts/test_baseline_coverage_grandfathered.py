#!/usr/bin/env python3
"""Ensure only fingerprint-identical legacy records bypass observations."""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent
BASE_RECORD = {"id": "aa-security", "countryId": "aa", "category": "SECURITY",
              "sourceId": "src-aa", "status": "ACTIVE", "title": "Existing sourced advice"}
BASE_SOURCE = {"id": "src-aa", "url": "https://example.org/travel-advice"}


def run_audit(root, record=BASE_RECORD, source=BASE_SOURCE, observations=None):
    (root / "scripts").mkdir(exist_ok=True)
    (root / "config").mkdir(exist_ok=True)
    shutil.copy2(SOURCE_ROOT / "scripts" / "audit_baseline_coverage.py", root / "scripts")
    (root / "data.json").write_text(json.dumps({
        "countries": [{"id": "aa", "name": "Example", "status": "ACTIVE"}],
        "records": [record], "sources": [source],
        "sourceObservations": observations or [],
    }), encoding="utf-8")
    canonical = json.dumps({"record": BASE_RECORD, "source": BASE_SOURCE},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    (root / "config" / "baseline_grandfathered_records.json").write_text(json.dumps({
        "formatVersion": 1, "baselineMainCommit": "fixture",
        "records": {BASE_RECORD["id"]: hashlib.sha256(canonical.encode()).hexdigest()},
    }), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "audit_baseline_coverage.py"), "--strict"],
        cwd=root, text=True, capture_output=True,
    )


def main():
    with tempfile.TemporaryDirectory(prefix="ti-grandfathered-coverage-") as temp:
        root = Path(temp)
        result = run_audit(root)
        output = result.stdout + result.stderr
        if result.returncode != 1 or "BASELINE COVERAGE: 1/14" not in output:
            print(output, file=sys.stderr)
            raise SystemExit("FAIL: exact production fingerprint should be grandfathered while strict gaps remain")

    with tempfile.TemporaryDirectory(prefix="ti-grandfathered-coverage-") as temp:
        root = Path(temp)
        changed = dict(BASE_RECORD, title="Unobserved changed claim")
        result = run_audit(root, record=changed)
        output = result.stdout + result.stderr
        if "BASELINE COVERAGE: 0/14" not in output or "WITHOUT FINGERPRINTED LEGACY STATUS" not in output:
            print(output, file=sys.stderr)
            raise SystemExit("FAIL: changed legacy record bypassed its fingerprint")

    with tempfile.TemporaryDirectory(prefix="ti-grandfathered-coverage-") as temp:
        root = Path(temp)
        fresh = dict(BASE_RECORD, id="aa-security-new", title="New sourced claim")
        observation = {"id": "obs-new", "recordId": fresh["id"], "sourceId": "src-aa",
                       "sourceUrl": "https://example.org/travel-advice#security",
                       "extractedClaim": "New sourced claim", "independenceGroupId": "example"}
        result = run_audit(root, record=fresh, observations=[observation])
        if result.returncode != 1 or "BASELINE COVERAGE: 1/14" not in result.stdout:
            print(result.stdout + result.stderr, file=sys.stderr)
            raise SystemExit("FAIL: new evidence should be accepted, with incomplete strict coverage preserved")

    print("PASS: exact legacy fingerprint is accepted; changed records need observations; strict 100% gaps still fail")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
