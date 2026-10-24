"""Automatic publication for 03AA-04_Investigation_Verification_TI: the established TI path, applied to events.

Reused unchanged from the Country Builder's publication module: the compare-and-swap commit backend (the agent reads the tip,
applies, validates with scripts/validate_ti_data.py, pushes; if main moved it re-reads and re-applies, never force-pushes), the
cache-busting live-site poll, the "release is at least as new as ours" test that separates a slow deployment from wrong
content, the production controller's site address, and change_log / meta upkeep as CLAUDE.md requires.

Safety properties
* Duplicate prevention: an event id whose content hash is already published is not written again; an event that another
  process already represents (same GDACS event id) is never duplicated.
* Safe updates: only events this agent published are replaced; the previous version is kept in the change_log entry and the
  earlier observations stay in sourceObservations, so history is preserved. Hand-written and daily-refresh events are untouched.
* A failed replacement leaves the existing event exactly as it was.
* Rollback is by event id and restores or removes only what one publication id changed, and only if nothing changed it since.
"""
from __future__ import annotations

import copy

from .country_draft import validate_data
from .country_publish import (head_sha, make_main_backend, release_is_ours, site_url_for, verify_live)   # noqa: F401  (re-exported)
from .investigation_config import AGENT_NAME
from .investigation_sources import iso_utc
from .monitor_state import dump_data

COUNT_KEYS = ("countries", "records", "sources", "locations", "events", "sourceObservations")


class PublishRefused(Exception):
    """The candidate failed validation or a guard. Nothing was pushed."""


def owned(event):
    return (event.get("provenance") or {}).get("builder") == AGENT_NAME


def _meta(d, ts):
    d["meta"].setdefault("counts", {}).update({k: len(d[k]) for k in COUNT_KEYS})
    pub = {e["id"] for e in d["events"] if e.get("publicationStatus") == "PUBLISHED"}
    d["meta"]["currentIntelligence"] = {"events": len(pub),
                                        "sourceObservations": sum(o.get("eventId") in pub for o in d["sourceObservations"])}
    d["meta"]["generated"] = ts


def apply_items(data, items, pub_id, now, max_events):
    """Pure. -> (new_data, summary). Never mutates its input."""
    d = copy.deepcopy(data)
    by_id = {e["id"]: i for i, e in enumerate(d["events"])}
    src_ids = {s["id"] for s in d["sources"]}
    obs_ids = {o["id"] for o in d["sourceObservations"]}
    summary = {"added": [], "updated": [], "unchanged": [], "skipped": []}
    nxt = max([c.get("change_id", 0) for c in d["change_log"]] or [0]) + 1
    ts, day = iso_utc(now), now.date().isoformat()
    for it in items:
        if len(summary["added"]) + len(summary["updated"]) >= max_events:
            summary["skipped"].append({"eventId": it["eventId"], "reason": "max_events_per_run"})
            continue
        ev, eid = it["event"], it["eventId"]
        gd = (ev.get("gdacsEventType"), ev.get("gdacsEventId"))
        other = next((e["id"] for e in d["events"] if e["id"] != eid and not owned(e)
                      and (e.get("gdacsEventType"), e.get("gdacsEventId")) == gd), None)
        if other:
            summary["skipped"].append({"eventId": eid, "reason": f"already_represented_by:{other}"})
            continue
        missing = [s for s in it["needsSources"] if s not in src_ids and s not in {x["id"] for x in it["sources"]}]
        if missing:
            summary["skipped"].append({"eventId": eid, "reason": "source_record_missing:" + ",".join(missing)})
            continue
        entry = {"change_id": nxt, "timestamp": ts, "date": day, "countryId": ev["countryId"], "eventId": eid,
                 "confidence": ev["confidence"], "publicationId": pub_id, "builder": AGENT_NAME}
        if eid in by_id:
            old = d["events"][by_id[eid]]
            if not owned(old):
                summary["skipped"].append({"eventId": eid, "reason": "existing_event_not_owned"})
                continue
            if old["provenance"].get("contentHash") == it["contentHash"]:
                summary["unchanged"].append(eid)
                continue
            d["events"][by_id[eid]] = ev
            entry.update(category="Current-intelligence update", summary=f"Automated update: {eid}",
                         detail="Replaced after reassessment of changed or expired evidence; the previous version is kept in this entry.",
                         previousEvent=old)
            summary["updated"].append(eid)
        else:
            d["events"].append(ev)
            by_id[eid] = len(d["events"]) - 1
            entry.update(category="New current-intelligence event", summary=f"Automated event: {eid}",
                         detail=f"Published by {AGENT_NAME} after claim-level assessment (status {ev['eventStatus']}, confidence {ev['confidence']}).")
            summary["added"].append(eid)
        entry["addedSourceIds"] = []
        for s in it["sources"]:
            if s["id"] not in src_ids:
                d["sources"].append(s)
                src_ids.add(s["id"])
                entry["addedSourceIds"].append(s["id"])
        entry["addedObservationIds"] = []
        for o in it["observations"]:
            if o["id"] not in obs_ids:
                d["sourceObservations"].append(o)
                obs_ids.add(o["id"])
                entry["addedObservationIds"].append(o["id"])
        d["change_log"].append(entry)
        nxt += 1
    if summary["added"] or summary["updated"]:
        _meta(d, ts)
        summary["generated"] = ts
    return d, summary


def rollback_items(data, pub_id, now):
    """Pure. Remove what ``pub_id`` added and restore what it replaced. Anything changed since is left alone."""
    d = copy.deepcopy(data)
    removed, restored, left = [], [], []
    logs = [c for c in d["change_log"] if c.get("publicationId") == pub_id and c.get("builder") == AGENT_NAME
            and c.get("category") in ("New current-intelligence event", "Current-intelligence update") and c.get("eventId")]
    nxt = max([c.get("change_id", 0) for c in d["change_log"]] or [0]) + 1
    drop_src, drop_obs = set(), set()
    for c in logs:
        idx = next((i for i, e in enumerate(d["events"]) if e["id"] == c["eventId"]), None)
        if idx is None or (d["events"][idx].get("provenance") or {}).get("publicationId") != pub_id:
            left.append(c["eventId"])
            continue
        drop_src.update(c.get("addedSourceIds", []))
        drop_obs.update(c.get("addedObservationIds", []))
        if c["category"] == "Current-intelligence update":
            d["events"][idx] = c["previousEvent"]
            restored.append(c["eventId"])
        else:
            d["events"].pop(idx)
            removed.append(c["eventId"])
    d["sourceObservations"] = [o for o in d["sourceObservations"] if o["id"] not in drop_obs]
    used = {o.get("sourceId") for o in d["sourceObservations"]} | {r.get("sourceId") for r in d["records"]}
    d["sources"] = [s for s in d["sources"] if not (s["id"] in drop_src and s["id"] not in used)]
    ts = iso_utc(now)
    d["change_log"].append({"change_id": nxt, "timestamp": ts, "date": ts[:10], "category": "Correction",
                            "countryId": logs[0]["countryId"] if logs else None, "summary": f"Automated rollback of publication {pub_id}",
                            "detail": f"Removed {removed or 'none'}; restored {restored or 'none'}; left unchanged because modified since: {left or 'none'}.",
                            "publicationId": pub_id, "builder": AGENT_NAME, "rollbackOf": pub_id})
    _meta(d, ts)
    return d, {"removed": removed, "restored": restored, "left": left}


# ---- pushing ---------------------------------------------------------------------------------------------------
def publish_items(backend, items, pub_id, now, max_events):
    """One compare-and-swap commit (re-read and re-applied if the branch moved). -> summary incl. commit sha."""
    def fn(snap):
        data = snap.data()
        if not data:
            raise PublishRefused("data.json could not be read from the target branch")
        new, summary = apply_items(data, items, pub_id, now, max_events)
        if not (summary["added"] or summary["updated"]):
            return None, summary
        ok, out = validate_data(new)
        if not ok:
            raise PublishRefused("canonical validation failed: " + out[:200])
        return {"data": dump_data(new)}, summary
    writes, summary = backend.transact(fn, message=f"{AGENT_NAME}: publication {pub_id} ({len(items)} item(s))")
    summary["commit"] = head_sha(backend) if writes else None
    return summary


def rollback_publication(backend, pub_id, now):
    def fn(snap):
        new, summary = rollback_items(snap.data(), pub_id, now)
        ok, out = validate_data(new)
        if not ok:
            raise PublishRefused("rollback failed validation: " + out[:200])
        return {"data": dump_data(new)}, summary
    _, summary = backend.transact(fn, message=f"{AGENT_NAME}: rollback of publication {pub_id}")
    summary["commit"] = head_sha(backend)
    return summary


# ---- live verification -----------------------------------------------------------------------------------------
def served_matches(served, items):
    """Does the public data.json serve exactly the published events, sources and observations?"""
    evs = {e.get("id"): e for e in served.get("events", [])}
    srcs = {s.get("id") for s in served.get("sources", [])}
    obs = {o.get("id") for o in served.get("sourceObservations", [])}
    missing = []
    for it in items:
        e = evs.get(it["eventId"])
        if (not e or e.get("publicationStatus") != "PUBLISHED" or e.get("summary") != it["event"]["summary"]
                or (e.get("provenance") or {}).get("contentHash") != it["contentHash"]):
            missing.append(it["eventId"])
            continue
        if any(s not in srcs for s in it["needsSources"]):
            missing.append(it["eventId"] + ":source")
        if any(o["id"] not in obs for o in it["observations"]):
            missing.append(it["eventId"] + ":observation")
    return not missing, missing


def served_without(served, event_ids):
    present = {e.get("id") for e in served.get("events", [])}
    return not (present & set(event_ids)), sorted(present & set(event_ids))


def target_site_url(cfg, root):
    return cfg.site_url_override if cfg.trial else site_url_for(root, cfg.publication_cfg)
