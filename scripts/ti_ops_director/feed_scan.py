"""Read existing feed-health evidence and create one deduplicated diagnostic task per non-healthy source."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ROOT

SNAPSHOT_MAX_AGE_HOURS = 24      # mirrors scripts/audit_feed_health.py


def load_evidence(root=None):
    root = Path(root or ROOT)
    cfg = json.loads((root / "config/phase1_sources.json").read_text())["sources"]
    path = root / "data/phase1_signals.json"
    snap = json.loads(path.read_text()) if path.exists() else None
    return cfg, snap


def scan_feed_health(ctl, mode="live", root=None, fixtures=None, env_override=None):
    sources, snap = load_evidence(root or ctl.cfg.repo_root)
    now = ctl.store.clock()
    generated = snap and datetime.fromisoformat(snap["generated"])
    snapshot_stale = (not generated or generated.tzinfo is None or
                      now - generated > timedelta(hours=SNAPSHOT_MAX_AGE_HOURS))
    bucket = int(now.timestamp() // (ctl.cfg.recheck_after_hours * 3600))
    created, existing, healthy, skipped_no_fixture = [], [], [], []
    for src in sources:
        ev = (snap or {}).get("sources", {}).get(src["id"])
        status = (ev or {}).get("status", "unknown")
        if status == "healthy" and ev.get("ok") and not snapshot_stale:
            healthy.append(src["id"])
            continue
        if snapshot_stale and status == "healthy":
            status = "stale"
        if mode == "fixture" and not (fixtures and src["id"] in fixtures):
            skipped_no_fixture.append(src["id"])
            continue
        inputs = {"source": {k: v for k, v in src.items() if k in
                             ("id", "name", "url", "endpoint", "publicEndpoint", "fallbackEndpoints", "appnameEnvironment",
                              "accessMethod")},
                  "evidence": {k: v for k, v in (ev or {}).items() if k in ("status", "ok", "error", "fetchedAt", "count")}
                              | {"status": status},
                  "mode": "fixture" if fixtures and src["id"] in fixtures else mode}
        # snapshot_generated is excluded from the fingerprint so an unchanged failure dedupes.
        if fixtures and src["id"] in fixtures:
            inputs["fixture_response"] = fixtures[src["id"]]
            inputs["env_override"] = env_override or {}
        elif env_override is not None:
            inputs["env_override"] = env_override
        tid, was_created = ctl.enqueue(task_type="feed_health_diagnostic", inputs=inputs, dedupe_subject=src["id"],
                                       priority=80 if src.get("priority") == "P0" else 50,
                                       mode=inputs["mode"], time_bucket=bucket)
        (created if was_created else existing).append(tid)
    return {"created": created, "already_queued": existing, "healthy_per_snapshot": healthy,
            "snapshot_generated": snap and snap["generated"], "snapshot_stale": bool(snapshot_stale),
            "snapshot_missing": snap is None, "skipped_no_fixture": skipped_no_fixture}
