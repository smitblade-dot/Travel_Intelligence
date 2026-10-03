#!/usr/bin/env python3
"""Merge sourced baseline batches without rolling back production provenance."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data.json"
BATCH_DIR = ROOT / "data" / "baseline_batches"
CATEGORIES = {
    "SECURITY", "ENTRY", "HEALTH", "EMERGENCY", "TRANSPORT", "ENVIRONMENT",
    "INSURANCE", "LAWS_CULTURE", "COMMUNICATIONS", "FINANCE", "LANGUAGE",
    "ACCOMMODATION", "EQUIPMENT", "TRAINING",
}


def require_http_url(value, label):
    if not str(value or "").startswith(("https://", "http://")):
        raise SystemExit(f"{label} must include an HTTP(S) URL")


def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    records = {item["id"]: item for item in data["records"] if item.get("id")}
    sources = {item["id"]: item for item in data["sources"] if item.get("id")}
    observations = {item["id"]: item for item in data["sourceObservations"] if item.get("id")}
    active_countries = {
        str(country.get("id", "")).lower()
        for country in data.get("countries", [])
        if country.get("status", "ACTIVE") == "ACTIVE"
    }
    added = updated = preserved = 0

    for path in sorted(BATCH_DIR.glob("*.json")):
        batch = json.loads(path.read_text(encoding="utf-8"))
        batch_records_list = batch.get("records", [])
        batch_sources_list = batch.get("sources", [])
        batch_observations = batch.get("sourceObservations", [])
        batch_records = {item.get("id"): item for item in batch_records_list if item.get("id")}
        batch_sources = {item.get("id"): item for item in batch_sources_list if item.get("id")}
        batch_obs = {item.get("id"): item for item in batch_observations if item.get("id")}
        if len(batch_records) != len(batch_records_list):
            raise SystemExit(f"Duplicate or missing record IDs in {path}")
        if len(batch_sources) != len(batch_sources_list):
            raise SystemExit(f"Duplicate or missing source IDs in {path}")
        if len(batch_obs) != len(batch_observations):
            raise SystemExit(f"Duplicate or missing SourceObservation IDs in {path}")
        if batch_records and path.stem.upper() not in CATEGORIES:
            raise SystemExit(f"Unexpected baseline batch filename: {path.name}")

        batch_source_ids = set(batch_sources)
        observed_pairs = set()
        for observation in batch_observations:
            oid = observation.get("id")
            record_id = observation.get("recordId")
            source_id = observation.get("sourceId")
            if not oid or not record_id or observation.get("eventId"):
                raise SystemExit(f"Baseline SourceObservation in {path} must have an id and recordId only")
            if record_id not in batch_records:
                raise SystemExit(f"SourceObservation {oid} in {path} must target a record in the same batch")
            if source_id not in batch_source_ids:
                raise SystemExit(f"SourceObservation {oid} in {path} must reference a source in the same batch")
            require_http_url(observation.get("sourceUrl"), f"SourceObservation {oid} in {path}")
            if not observation.get("extractedClaim") or not observation.get("independenceGroupId"):
                raise SystemExit(f"SourceObservation {oid} in {path} requires extractedClaim and independenceGroupId")
            observed_pairs.add((record_id, source_id))

        for source_id, source in batch_sources.items():
            require_http_url(source.get("url"), f"Baseline source {source_id} in {path}")
            current = sources.get(source_id)
            if current and str(current.get("url") or "") != str(source.get("url") or ""):
                raise SystemExit(
                    f"Source ID collision with a different URL for {source_id} in {path}; refusing to change provenance"
                )
            incoming_date = source.get("dateChecked") or ""
            current_date = (current or {}).get("dateChecked") or ""
            if current is None or (incoming_date and incoming_date > current_date):
                sources[source_id] = source
            else:
                preserved += 1

        stale_record_ids = set()
        updated_record_ids = set()
        for record_id, record in batch_records.items():
            if record.get("category") not in CATEGORIES or not record.get("countryId"):
                raise SystemExit(f"Invalid baseline record in {path}: {record_id}")
            if str(record["countryId"]).lower() not in active_countries:
                raise SystemExit(f"Baseline record {record_id} in {path} targets an inactive country")
            source_id = record.get("sourceId")
            if not source_id or source_id not in batch_source_ids:
                raise SystemExit(f"Baseline record {record_id} in {path} must include its real source in the batch")
            if (record_id, source_id) not in observed_pairs:
                raise SystemExit(f"Baseline record {record_id} in {path} must include a matching SourceObservation")

            current = records.get(record_id)
            if current is None:
                records[record_id] = record
                added += 1
                continue

            incoming_date = record.get("dateChecked") or ""
            current_date = current.get("dateChecked") or ""
            if incoming_date and incoming_date > current_date:
                records[record_id] = record
                updated_record_ids.add(record_id)
                updated += 1
            elif incoming_date == current_date and record != current:
                raise SystemExit(
                    f"Conflicting baseline record {record_id} in {path} has the same dateChecked "
                    f"({incoming_date}); refusing to append observations for a rejected record revision"
                )
            else:
                if incoming_date < current_date or not incoming_date:
                    stale_record_ids.add(record_id)
                preserved += 1

        for observation_id, observation in batch_obs.items():
            record_id = observation["recordId"]
            current = observations.get(observation_id)
            if record_id in stale_record_ids:
                preserved += 1
                continue
            if current is None:
                observations[observation_id] = observation
                continue
            if record_id in updated_record_ids:
                raise SystemExit(
                    f"Updated record {record_id} reuses existing SourceObservation ID {observation_id}; "
                    "new evidence requires a new immutable observation ID"
                )
            if current != observation:
                raise SystemExit(
                    f"Conflicting SourceObservation ID {observation_id} in {path}; "
                    "use a new immutable observation ID"
                )
            preserved += 1

    data["records"] = list(records.values())
    data["sources"] = list(sources.values())
    data["sourceObservations"] = list(observations.values())
    data["meta"]["counts"]["records"] = len(data["records"])
    data["meta"]["counts"]["sources"] = len(data["sources"])
    data["meta"]["counts"]["sourceObservations"] = len(data["sourceObservations"])
    from datetime import datetime, timezone
    data["meta"]["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    DATA.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(
        f"Merged baseline batches: added={added} updated={updated} preserved_existing={preserved} "
        f"records={len(data['records'])} sources={len(data['sources'])} "
        f"observations={len(data['sourceObservations'])}"
    )


if __name__ == "__main__":
    main()
