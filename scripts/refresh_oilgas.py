#!/usr/bin/env python3
"""
Refresh data.json's `oilGasSummary` from the two sibling flow-intelligence
dashboards (crude-flow-dashboard, gas-lng-flow-dashboard).

Fully automated, deterministic, stdlib-only (no pip install needed on the
Actions runner). Run every week by the GitHub Actions workflow, before the
Claude-driven FCDO/Home Office refresh step, so a single commit at the end
of the run carries both kinds of updates.

Design notes (read this before changing the logic):
  - Country matching uses `oilgas-country-map.json` in this same directory's
    parent (repo root) — a hand-checked, persisted iso2 -> {crude, gas}
    name mapping. Don't try to re-derive the mapping by fuzzy-matching
    names at runtime: that's exactly the kind of thing that silently drifts.
    If a new country is added to Travel_Intelligence's data.json, or a new
    country appears in a dashboard, update oilgas-country-map.json by hand
    (see its own comment) rather than guessing here.
  - This script NEVER partially overwrites oilGasSummary. It either builds
    a complete, sane replacement, or it leaves the existing oilGasSummary
    untouched and exits 0 (never fails the job) — a dashboard outage or
    schema change should not corrupt or wipe this week's site.
  - It does not touch anything else in data.json (meta.generated etc. are
    the Claude-driven refresh step's responsibility, per CLAUDE.md).
"""
import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_JSON = REPO_ROOT / "data.json"
COUNTRY_MAP = REPO_ROOT / "oilgas-country-map.json"

CRUDE_URL = "https://raw.githubusercontent.com/smitblade-dot/crude-flow-dashboard/main/data.json"
GAS_URL = "https://raw.githubusercontent.com/smitblade-dot/gas-lng-flow-dashboard/main/data.json"

STATUS_RANK = {"Red": 0, "Amber": 1, "Blue": 2, "Green": 3}
MIN_EXPECTED_ENTRIES = 20  # sanity floor; today's real count is 28 of 29


def fetch_json(url, timeout=20):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def truncate(text, limit=320):
    if not text:
        return text
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "..."


def build_commodity_entry(dashboard, country_name):
    countries = dashboard.get("countries", [])
    row = next((c for c in countries if c.get("country") == country_name), None)
    if row is None:
        return None

    infra_rows = [i for i in dashboard.get("infrastructure", []) if i.get("country") == country_name]
    status_counts = Counter(i.get("map_status") for i in infra_rows if i.get("map_status"))
    worst_status = None
    if status_counts:
        worst_status = min(status_counts, key=lambda s: STATUS_RANK.get(s, 99))
    infra_sorted = sorted(infra_rows, key=lambda i: STATUS_RANK.get(i.get("map_status"), 99))
    notable_infrastructure = [
        {
            "name": i.get("infrastructure_name"),
            "type": i.get("type"),
            "layer": i.get("layer"),
            "status": i.get("status"),
            "map_status": i.get("map_status"),
            "watch_item": i.get("watch_item"),
        }
        for i in infra_sorted[:4]
    ]

    flows = [f for f in dashboard.get("weekly_flows", []) if f.get("country") == country_name]
    flows.sort(key=lambda f: f.get("week_ending") or "", reverse=True)
    latest_flow = None
    if flows:
        f = flows[0]
        if "mbd" in f:
            value, unit = f.get("mbd"), "mb/d"
        else:
            value, unit = f.get("value"), f.get("unit")
        latest_flow = {
            "route": f.get("route"),
            "value": value,
            "unit": unit,
            "status": f.get("status"),
            "confidence": f.get("confidence"),
            "week_ending": f.get("week_ending"),
        }

    disruptions = [d for d in dashboard.get("disruptions", []) if d.get("country") == country_name]
    # "Active" = not yet marked restarted. Most-recent first, top 3.
    active = [d for d in disruptions if not d.get("actual_restart")]
    active.sort(key=lambda d: d.get("start_date") or d.get("date") or "", reverse=True)
    active_disruptions = [
        {
            "asset": d.get("asset"),
            "event_type": d.get("event_type"),
            "start_date": d.get("start_date") or d.get("date"),
            "status": d.get("status"),
            "summary": truncate(d.get("description")),
        }
        for d in active[:3]
    ]

    meta = dashboard.get("meta", {})
    return {
        "commodity": row.get("commodity"),
        "main_export_points": row.get("main_export_points"),
        "primary_route": row.get("primary_route"),
        "approximate_current_production": row.get("approximate_current_production"),
        "producer_status": row.get("producer_status"),
        "country_notes": row.get("notes"),
        "infrastructure_count": len(infra_rows),
        "infrastructure_status_counts": dict(status_counts),
        "worst_status": worst_status,
        "notable_infrastructure": notable_infrastructure,
        "latest_flow": latest_flow,
        "active_disruptions": active_disruptions,
        "source_generated": meta.get("generated"),
        "source_title": meta.get("title"),
    }


def main():
    if not COUNTRY_MAP.exists():
        print(f"WARNING: {COUNTRY_MAP} not found — skipping oil & gas refresh, leaving data.json untouched.")
        return 0

    country_map = json.loads(COUNTRY_MAP.read_text())

    try:
        crude = fetch_json(CRUDE_URL)
        gas = fetch_json(GAS_URL)
    except Exception as exc:  # noqa: BLE001 - deliberately broad: never fail the job over a fetch hiccup
        print(f"WARNING: could not fetch one or both dashboards ({exc}) — skipping oil & gas refresh, leaving data.json untouched.")
        return 0

    summary = {}
    for iso2, names in country_map.items():
        entry = {}
        if names.get("crude"):
            c = build_commodity_entry(crude, names["crude"])
            if c:
                entry["crude"] = c
        if names.get("gas"):
            g = build_commodity_entry(gas, names["gas"])
            if g:
                entry["gas"] = g
        if entry:
            summary[iso2] = entry

    if len(summary) < MIN_EXPECTED_ENTRIES:
        print(f"WARNING: only built {len(summary)} oil & gas country entries (expected >= {MIN_EXPECTED_ENTRIES}) "
              f"— dashboards may have changed shape. Leaving data.json's oilGasSummary untouched.")
        return 0

    if not DATA_JSON.exists():
        print(f"WARNING: {DATA_JSON} not found — skipping oil & gas refresh.")
        return 0

    data = json.loads(DATA_JSON.read_text())
    before = json.dumps(data.get("oilGasSummary", {}), sort_keys=True)
    after = json.dumps(summary, sort_keys=True)
    data["oilGasSummary"] = summary
    data.setdefault("meta", {}).setdefault("counts", {})["oilGasCountries"] = len(summary)
    data["meta"]["oilGasLinks"] = {
        "crude": "https://smitblade-dot.github.io/crude-flow-dashboard/",
        "gas": "https://smitblade-dot.github.io/gas-lng-flow-dashboard/",
    }
    DATA_JSON.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))

    if before == after:
        print(f"Oil & gas summary refreshed: {len(summary)} countries, no material change this week.")
    else:
        print(f"Oil & gas summary refreshed: {len(summary)} countries, data changed since last run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
