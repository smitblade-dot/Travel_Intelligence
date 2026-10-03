#!/usr/bin/env python3
"""Regression cases for stale baseline merge and immutable provenance."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent


def fixture(root, incoming_record_date, incoming_obs_id="obs-a", incoming_obs_claim="old claim", source_url="https://example.org/a", include_obs=True):
    (root / "scripts").mkdir()
    (root / "data" / "baseline_batches").mkdir(parents=True)
    shutil.copy2(SOURCE_ROOT / "scripts" / "merge_baseline_batches.py", root / "scripts" / "merge_baseline_batches.py")
    data = {
        "countries": [{"id": "aa", "status": "ACTIVE"}],
        "records": [{"id": "aa-security", "countryId": "aa", "category": "SECURITY", "sourceId": "src-a", "dateChecked": "2026-09-01", "title": "current claim"}],
        "sources": [{"id": "src-a", "url": "https://example.org/a", "dateChecked": "2026-09-01", "sourceName": "current"}],
        "sourceObservations": [{"id": "obs-a", "recordId": "aa-security", "sourceId": "src-a", "sourceUrl": "https://example.org/a/page", "extractedClaim": "current claim", "independenceGroupId": "example"}],
        "meta": {"counts": {}, "generated": "2026-09-01T00:00:00Z"},
    }
    data_path = root / "data.json"
    data_path.write_text(json.dumps(data), encoding="utf-8")
    observations = []
    if include_obs:
        observations.append({"id": incoming_obs_id, "recordId": "aa-security", "sourceId": "src-a", "sourceUrl": "https://example.org/a/page", "extractedClaim": incoming_obs_claim, "independenceGroupId": "example"})
    record = {"id": "aa-security", "countryId": "aa", "category": "SECURITY", "sourceId": "src-a", "dateChecked": incoming_record_date, "title": "new claim"}
    batch = {
        "records": [record],
        "sources": [{"id": "src-a", "url": source_url, "dateChecked": incoming_record_date, "sourceName": "incoming"}],
        "sourceObservations": observations,
    }
    (root / "data" / "baseline_batches" / "SECURITY.json").write_text(json.dumps(batch), encoding="utf-8")
    return data_path


def run(root):
    return subprocess.run([sys.executable, str(root / "scripts" / "merge_baseline_batches.py")], cwd=root, text=True, capture_output=True)


def main():
    with tempfile.TemporaryDirectory(prefix="ti-baseline-merge-") as temp:
        root = Path(temp)
        path = fixture(root, "2026-08-01", incoming_obs_claim="old claim")
        result = run(root)
        if result.returncode:
            raise SystemExit(result.stdout + result.stderr)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["records"][0]["title"] == "current claim"
        assert data["sources"][0]["sourceName"] == "current"
        assert data["sourceObservations"][0]["extractedClaim"] == "current claim"

    with tempfile.TemporaryDirectory(prefix="ti-baseline-merge-") as temp:
        root = Path(temp)
        path = fixture(root, "2026-09-15", incoming_obs_id="obs-a", incoming_obs_claim="new claim")
        before = path.read_bytes()
        result = run(root)
        assert result.returncode != 0 and "new immutable observation ID" in result.stderr
        assert path.read_bytes() == before

    with tempfile.TemporaryDirectory(prefix="ti-baseline-merge-") as temp:
        root = Path(temp)
        path = fixture(root, "2026-09-01", incoming_obs_id="obs-new", incoming_obs_claim="new same-date claim")
        before = path.read_bytes()
        result = run(root)
        assert result.returncode != 0 and "same dateChecked" in result.stderr
        assert path.read_bytes() == before

    with tempfile.TemporaryDirectory(prefix="ti-baseline-merge-") as temp:
        root = Path(temp)
        path = fixture(root, "2026-09-15", source_url="https://other.example/source")
        before = path.read_bytes()
        result = run(root)
        assert result.returncode != 0 and "Source ID collision" in result.stderr
        assert path.read_bytes() == before

    with tempfile.TemporaryDirectory(prefix="ti-baseline-merge-") as temp:
        root = Path(temp)
        path = fixture(root, "2026-09-15", include_obs=False)
        before = path.read_bytes()
        result = run(root)
        assert result.returncode != 0 and "matching SourceObservation" in result.stderr
        assert path.read_bytes() == before

    print("PASS: stale values preserved; conflicting same-date records, observation/source IDs and missing evidence rejected atomically")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
