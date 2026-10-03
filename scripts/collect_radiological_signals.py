#!/usr/bin/env python3
"""Collect radiological/nuclear signals for Travel Intelligence.

This layer deliberately produces signals, not public TI events.
A candidate anomaly must be corroborated against authoritative sources
before publication.
"""

from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
WATCHLIST = ROOT / "config" / "radiological_watchlist.json"
OUT = ROOT / "data" / "radiological_signals.json"
HISTORY = ROOT / "data" / "radiological_history.json"

TIMEOUT = 30
LOOKBACK_HOURS = 48
CURRENT_WINDOW_HOURS = 6
HISTORY_RETENTION = 500
MIN_RECENT_READINGS = 3
MIN_BASELINE_READINGS = 20


def fetch_json(url: str):
    req = Request(url, headers={"User-Agent": "TravelIntelligence/0.1"})
    with urlopen(req, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def unwrap_measurements(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("measurements", "data", "results", "features"):
            value = payload.get(key)
            if isinstance(value, list):
                if key == "features":
                    return [x.get("properties", x) for x in value]
                return value
    return []


def parse_time(item):
    raw = item.get("captured_at") or item.get("capturedAt") or item.get("timestamp")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_value(item):
    for key in ("value", "radiation", "cpm", "dose_rate"):
        value = item.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def parse_unit(item):
    value = item.get("unit") or item.get("units")
    return str(value).lower() if value else None


def mad(values):
    if not values:
        return 0.0
    med = statistics.median(values)
    return statistics.median([abs(v - med) for v in values])


def load_history():
    if not HISTORY.exists():
        return {}
    try:
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main():
    watchpoints = json.loads(WATCHLIST.read_text(encoding="utf-8"))["watchpoints"]
    history = load_history()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)

    output = {
        "schemaVersion": "1.0",
        "generated": now.isoformat(),
        "purpose": "Radiological/nuclear signal collection. Candidate anomalies require corroboration before publication.",
        "source": {
            "id": "safecast-radiation",
            "name": "Safecast",
            "url": "https://safecast.org/data/",
            "license": "CC0"
        },
        "watchpoints": []
    }

    for wp in watchpoints:
        params = urlencode({
            "distance": int(wp["radius_km"]),
            "latitude": wp["latitude"],
            "longitude": wp["longitude"],
            "captured_after": cutoff.strftime("%Y-%m-%d+%H:%M:%S"),
            "per_page": 100,
        })
        url = f"https://api.safecast.org/measurements.json?{params}"
        result = {
            "id": wp["id"],
            "name": wp["name"],
            "country": wp["country"],
            "iso3": wp["iso3"],
            "status": "no_data",
            "candidateAnomaly": False,
            "sourceUrl": url,
            "readings": []
        }

        try:
            items = unwrap_measurements(fetch_json(url))
        except Exception as exc:
            result["status"] = "source_error"
            result["error"] = str(exc)
            output["watchpoints"].append(result)
            continue

        recent = []
        for item in items:
            value = parse_value(item)
            ts = parse_time(item)
            unit = parse_unit(item)
            lat = item.get("latitude") or item.get("lat")
            lon = item.get("longitude") or item.get("lon")
            if value is None or ts is None or not unit:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < cutoff:
                continue
            distance = None
            if lat is not None and lon is not None:
                try:
                    distance = haversine_km(wp["latitude"], wp["longitude"], float(lat), float(lon))
                except (TypeError, ValueError):
                    pass
            recent.append({
                "captured_at": ts.isoformat(),
                "value": value,
                "unit": unit,
                "device_id": item.get("device_id"),
                "latitude": lat,
                "longitude": lon,
                "distance_km": round(distance, 2) if distance is not None else None,
            })

        result["readings"] = sorted(recent, key=lambda x: x["captured_at"], reverse=True)[:25]
        if not recent:
            output["watchpoints"].append(result)
            continue

        # Only compare like-for-like units. Deduplicate observations because the
        # hourly collector repeatedly queries an overlapping 48-hour window.
        grouped = {}
        for row in recent:
            grouped.setdefault(row["unit"], []).append(row)

        wp_history = history.setdefault(wp["id"], {})
        best = None
        current_cutoff = now - timedelta(hours=CURRENT_WINDOW_HOURS)
        for unit, rows in grouped.items():
            series = wp_history.setdefault(unit, [])
            existing = {(p.get("ts"), p.get("device_id"), p.get("value")) for p in series}
            for row in rows:
                key = (row["captured_at"], row.get("device_id"), row["value"])
                if key not in existing:
                    series.append({
                        "ts": row["captured_at"],
                        "value": row["value"],
                        "device_id": row.get("device_id"),
                    })
                    existing.add(key)
            # Keep a bounded history and discard malformed/very old records.
            cleaned = []
            for point in series:
                try:
                    ts = datetime.fromisoformat(point["ts"].replace("Z", "+00:00"))
                    if ts >= now - timedelta(days=90):
                        cleaned.append({
                            "ts": ts.isoformat(),
                            "value": float(point["value"]),
                            "device_id": point.get("device_id"),
                        })
                except Exception:
                    continue
            cleaned.sort(key=lambda p: p["ts"])
            cleaned = cleaned[-HISTORY_RETENTION:]
            wp_history[unit] = cleaned

            current_values = [p["value"] for p in cleaned if datetime.fromisoformat(p["ts"].replace("Z", "+00:00")) >= current_cutoff]
            baseline_values = [p["value"] for p in cleaned if datetime.fromisoformat(p["ts"].replace("Z", "+00:00")) < current_cutoff]
            if len(baseline_values) < MIN_BASELINE_READINGS:
                state = "building_baseline"
                score = None
            else:
                baseline = statistics.median(baseline_values)
                dispersion = mad(baseline_values)
                current = statistics.median(current_values)
                threshold = max(dispersion * 5, baseline * 0.25)
                elevated = current > baseline + threshold
                enough_current = len(current_values) >= MIN_RECENT_READINGS
                state = "candidate_anomaly" if elevated and enough_current else "within_baseline"
                score = round((current - baseline) / baseline, 3) if baseline else None
                if state == "candidate_anomaly":
                    best = {
                        "unit": unit,
                        "baseline_median": round(baseline, 3),
                        "current_median": round(current, 3),
                        "change_ratio": score,
                        "mad": round(dispersion, 3),
                        "sample_count": len(current_values),
                    }

            if best:
                break

        if best:
            result["status"] = "candidate_anomaly"
            result["candidateAnomaly"] = True
            result["analysis"] = best
        else:
            result["status"] = "observed"

        output["watchpoints"].append(result)

    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    candidates = [w["name"] for w in output["watchpoints"] if w["candidateAnomaly"]]
    print(f"Radiological watchpoints processed: {len(output['watchpoints'])}")
    print("Candidate anomalies:", ", ".join(candidates) if candidates else "none")


if __name__ == "__main__":
    main()
