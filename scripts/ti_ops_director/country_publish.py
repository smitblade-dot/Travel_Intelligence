"""Automatic publication for the country builder: the established TI path, applied safely.

Reused, not reinvented: compare-and-swap commits to main by the agent itself (as the continuous monitor does),
the canonical validator (scripts/validate_ti_data.py) on the candidate before anything is pushed, the Production
Controller's public-site address, GitHub Pages as the deployment, meta.counts / meta.generated / change_log
upkeep as in CLAUDE.md, and a SourceObservation for every record.

Safety properties:
* Duplicate prevention: a record id that already holds the same content hash is never written twice.
* Safe updates: only records this agent published are ever replaced; the previous version is preserved in the
  change_log entry; hand-written and grandfathered records are never touched.
* A failed replacement keeps the existing record untouched; its dateChecked is not advanced, so the public age stays honest.
* Rollback removes or restores records by id (git revert cannot work on a one-line data.json).
* Live verification: the public data.json is fetched (cache-busting) until it serves exactly what was published;
  if it does not within the timeout the publication is rolled back and held.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .country_config import AGENT_NAME
from .country_draft import slug, validate_data
from .country_rules import compose_content, date_phrase, repo_file
from .monitor_state import GitStateBackend, dump_data
from .store import iso

COUNT_KEYS = ("countries", "records", "sources", "locations", "events", "sourceObservations")


class PublishRefused(Exception):
    """The candidate failed validation or a guard. Nothing was pushed."""


def content_hash(text, source_date):
    return hashlib.sha256(f"{text}|{source_date}".encode("utf-8")).hexdigest()[:16]


def _notes(assessed, cited, sources_cfg, pe, primary):
    base = (f"Published automatically by {AGENT_NAME} after the TI publication rules passed. Source wording is quoted verbatim "
            f"({date_phrase(sources_cfg[primary].get('date_verb', 'published'), pe.get('publicationDate'), pe.get('publicationDatePrecision', 'DAY'))}; retrieved "
            f"{pe['retrievedAt'][:10]}). The advice is the named government's guidance and may differ for other nationalities.")
    if assessed.get("recordNote"):
        base = (f"Published automatically by {AGENT_NAME} after the TI publication rules passed (retrieved {pe['retrievedAt'][:10]}). "
                + assessed["recordNote"])
    caveats = [sources_cfg[k]["caveat"] for k in cited if sources_cfg[k].get("caveat")]
    return " ".join([base] + list(dict.fromkeys(caveats)))


# ---- building publication items -----------------------------------------------------------------------------
def build_publication_item(cc, category, tname, tdef, assessed, evidence, sources_cfg, today, pub_cfg, pub_id):
    ev_by_key = {e["sourceKey"]: e for e in evidence}
    claims = assessed["claims"]
    rid = f"{cc}-{category.lower()}-{slug(tname)}"
    content = compose_content(claims, ev_by_key, sources_cfg)
    cited = {}
    for c in claims:
        for x in c["evidence"]:
            cited.setdefault(x["sourceKey"], []).append(x["quote"])
    backed = [k for k in cited if sources_cfg[k].get("registry_id") and ev_by_key[k].get("publicationDate")]
    primary = max(backed or list(cited), key=lambda k: len(cited[k]))
    pe = ev_by_key[primary]
    src_date = max((ev_by_key[k]["publicationDate"] for k in backed), default=None)
    chash = content_hash(content, src_date)
    method = "EXTRACTIVE" if all(c.get("method") == "EXTRACTIVE" for c in claims) else "MODEL_VERIFIED"
    record = {
        "id": rid, "countryId": cc, "category": category, "subcategory": tdef["subcategory"], "dataType": tdef["data_type"],
        "title": tdef["subcategory"], "content": content, "confidence": assessed["confidence"], "status": "ACTIVE",
        "sourceId": pe["sourceId"], "locationId": None, "dateChecked": today.isoformat(),
        "reviewDate": (today + timedelta(days=pub_cfg["review_days"])).isoformat(), "sourcePublicationDate": src_date,
        "notes": _notes(assessed, cited, sources_cfg, pe, primary),
        "provenance": {"builder": AGENT_NAME, "publicationId": pub_id, "method": method, "contentHash": chash, "topic": tname}}
    new_sources, observations = {}, []
    for sk, quotes in cited.items():
        e = ev_by_key[sk]
        if not e["sourceIdIsExisting"]:
            s = sources_cfg[sk]
            new_sources[e["sourceId"]] = {
                "id": e["sourceId"], "countryId": cc, "sourceName": s["title"], "organisation": s["publisher"], "sourceType": s.get("source_type", "GOVERNMENT"),
                "url": e["url"], "jurisdiction": s["jurisdiction"], "confidence": SOURCE_CONFIDENCE.get(s.get("tier"), "LOW"), "active": True, "accessMethod": s["access_method"],
                "contentRights": s["content_rights"], "automationPermission": s["automation_permission"],
                "originalLanguage": s["original_language"], "geographicScope": "NATIONAL", "dateChecked": today.isoformat(),
                "reviewFrequency": "Every 3-6 months, or on any change"}
        observations.append({
            "id": f"obs-{rid}-{sk.replace('_', '-')}-{chash[:6]}", "recordId": rid, "sourceId": e["sourceId"], "sourceUrl": e["url"],
            "extractedClaim": " ".join(dict.fromkeys(quotes))[:1500], "independenceGroupId": e["independenceGroupId"],
            "dateChecked": today.isoformat(), "observationStatus": "DIRECT_OFFICIAL" if sources_cfg[sk].get("registry_id") else "SUPPORTING",
            "originalLanguage": e["originalLanguage"], "translated": False, "translationMethod": "NONE",
            "retrievedAt": e["retrievedAt"], "publicationDate": e.get("publicationDate"),
            "publicationDateStatus": e["publicationDateStatus"], "contentRights": e["contentRights"], "rightsStatus": e["contentRights"],
            "accessMethod": e["accessMethod"], "automationPermission": e["automationPermission"], "publisherName": e["publisher"],
            "legalReviewStatus": "NOT_REQUIRED" if e["contentRights"] in OPEN_RIGHTS else "REVIEW_RECOMMENDED", "publicationId": pub_id})
    needed = {record["sourceId"]} | {o["sourceId"] for o in observations}
    return {"recordId": rid, "record": record, "sources": list(new_sources.values()), "observations": observations,
            "contentHash": chash, "topic": tname, "countryId": cc, "category": category, "needsSources": sorted(needed)}


# ---- applying to data.json ----------------------------------------------------------------------------------
OPEN_RIGHTS = ("GOVERNMENT_OPEN", "OPEN_LICENCE", "PUBLIC_DOMAIN")      # a clear licence; anything else is flagged for a person to confirm
SOURCE_CONFIDENCE = {"A": "HIGH", "B": "MEDIUM", "C": "MEDIUM", "D": "LOW", "E": "LOW", "F": "LOW"}


def owned(record):
    return (record.get("provenance") or {}).get("builder") == AGENT_NAME


def _counts(d):
    d["meta"].setdefault("counts", {}).update({k: len(d[k]) for k in COUNT_KEYS})


def apply_items(data, items, pub_id, now, max_records, refresh_days=7):
    """Pure. -> (new_data, summary). Never mutates the input; skips duplicates; replaces only own records."""
    d = copy.deepcopy(data)
    by_id = {r["id"]: i for i, r in enumerate(d["records"])}
    src_ids = {s["id"] for s in d["sources"]}
    obs_ids = {o["id"] for o in d["sourceObservations"]}
    summary = {"added": [], "updated": [], "unchanged": [], "refreshed": [], "skipped": []}
    next_change = max([c.get("change_id", 0) for c in d["change_log"]] or [0]) + 1
    ts, day = iso(now), now.date().isoformat()
    for it in items:
        if len(summary["added"]) + len(summary["updated"]) >= max_records:
            summary["skipped"].append({"recordId": it["recordId"], "reason": "max_records_per_run"})
            continue
        rec, rid = it["record"], it["recordId"]
        missing = [s for s in it["needsSources"] if s not in src_ids and s not in {x["id"] for x in it["sources"]}]
        if missing:
            summary["skipped"].append({"recordId": rid, "reason": "source_record_missing:" + ",".join(missing)})
            continue
        entry = {"change_id": next_change, "timestamp": ts, "date": day, "countryId": rec["countryId"], "recordId": rid,
                 "confidence": rec["confidence"], "source": rec["sourceId"], "publicationId": pub_id, "builder": AGENT_NAME}
        if rid in by_id:
            old = d["records"][by_id[rid]]
            if not owned(old):
                summary["skipped"].append({"recordId": rid, "reason": "existing_record_not_owned"})
                continue
            if old["provenance"].get("contentHash") == it["contentHash"]:
                summary["unchanged"].append(rid)
                age = (now.date() - datetime.fromisoformat(old["dateChecked"]).date()).days
                if age >= refresh_days:          # re-verified against the unchanged source: the public check date moves
                    old["dateChecked"], old["reviewDate"] = rec["dateChecked"], rec["reviewDate"]
                    d["change_log"].append({**entry, "category": "Re-verification", "summary": f"Re-verified: {rid}",
                                            "detail": "Source retrieved again and unchanged; check date refreshed."})
                    next_change += 1
                    summary["refreshed"].append(rid)
                continue
            d["records"][by_id[rid]] = rec
            entry.update(category="Update", summary=f"Automated update: {rid}",
                         detail="Replaced by newer source text that passed the publication rules. The previous version is kept in this entry.",
                         previousRecord=old)
            summary["updated"].append(rid)
        else:
            d["records"].append(rec)
            by_id[rid] = len(d["records"]) - 1
            entry.update(category="New baseline record", summary=f"Automated baseline record: {rid}",
                         detail=f"Published by {AGENT_NAME} after rule checks (confidence {rec['confidence']}). "
                                f"Source publication date {rec.get('sourcePublicationDate') or 'not stated'}.")
            summary["added"].append(rid)
        entry["addedSourceIds"] = []
        for s in it["sources"]:
            if s["id"] not in src_ids:
                d["sources"].append(s)
                src_ids.add(s["id"])
                entry["addedSourceIds"].append(s["id"])
        for o in it["observations"]:
            if o["id"] not in obs_ids:
                d["sourceObservations"].append(o)
                obs_ids.add(o["id"])
        d["change_log"].append(entry)
        next_change += 1
    if summary["added"] or summary["updated"] or summary["refreshed"]:
        _counts(d)
        d["meta"]["generated"] = ts
        summary["generated"] = ts
    return d, summary


def rollback_items(data, pub_id, now):
    """Pure. Remove what ``pub_id`` added and restore what it replaced. Anything changed since is left alone."""
    d = copy.deepcopy(data)
    restored, removed, left = [], [], []
    logs = [c for c in d["change_log"] if c.get("publicationId") == pub_id and c.get("builder") == AGENT_NAME
            and c.get("category") in ("New baseline record", "Update") and c.get("recordId")]
    next_change = max([c.get("change_id", 0) for c in d["change_log"]] or [0]) + 1
    drop_sources = set()
    for c in logs:
        rid = c["recordId"]
        idx = next((i for i, r in enumerate(d["records"]) if r["id"] == rid), None)
        if idx is None or (d["records"][idx].get("provenance") or {}).get("publicationId") != pub_id:
            left.append(rid)
            continue
        if c["category"] == "Update":
            d["records"][idx] = c["previousRecord"]
            restored.append(rid)
            drop_sources.update(c.get("addedSourceIds", []))
        else:
            d["records"].pop(idx)
            removed.append(rid)
            drop_sources.update(c.get("addedSourceIds", []))
    d["sourceObservations"] = [o for o in d["sourceObservations"] if not (o.get("publicationId") == pub_id and o.get("recordId") not in left)]
    used = {r.get("sourceId") for r in d["records"]} | {o.get("sourceId") for o in d["sourceObservations"]}
    d["sources"] = [s for s in d["sources"] if not (s["id"] in drop_sources and s["id"] not in used)]
    ts = iso(now)
    d["change_log"].append({"change_id": next_change, "timestamp": ts, "date": ts[:10], "category": "Correction",
                            "countryId": logs[0]["countryId"] if logs else None, "summary": f"Automated rollback of publication {pub_id}",
                            "detail": f"Removed {removed or 'none'}; restored {restored or 'none'}; left unchanged because modified since: {left or 'none'}.",
                            "publicationId": pub_id, "builder": AGENT_NAME, "rollbackOf": pub_id})
    _counts(d)
    d["meta"]["generated"] = ts
    return d, {"removed": removed, "restored": restored, "left": left}


# ---- pushing ------------------------------------------------------------------------------------------------
def make_main_backend(root, branch="main", run=subprocess.run):
    return GitStateBackend(root, {"data": "data.json"}, branch=branch, mirror_worktree=False, run=run)


def head_sha(backend):
    p = backend.git("ls-remote", backend.remote, f"refs/heads/{backend.branch}", check=False)
    return (p.stdout.split() or [None])[0]


def publish_items(backend, items, pub_id, now, max_records, refresh_days=7):
    """One compare-and-swap commit to main (re-read and re-applied if main moved). -> summary incl. commit sha."""
    def fn(snap):
        data = snap.data()
        if not data:
            raise PublishRefused("data.json could not be read from the target branch")
        new, summary = apply_items(data, items, pub_id, now, max_records, refresh_days)
        if not (summary["added"] or summary["updated"] or summary["refreshed"]):
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


def _utc(ts):
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def release_is_ours(v, published_generated):
    """True when the site is demonstrably serving a release at or after ours yet not our content (so the content, not the
    deployment, is wrong). A site that is merely behind, unreachable or unreadable proves nothing about the content;
    timestamps that cannot be parsed prove nothing either."""
    served, ours = _utc(v.get("served_generated")), _utc(published_generated)
    return bool(served and ours and served >= ours)


# ---- live verification --------------------------------------------------------------------------------------
def served_matches(served, items):
    """Does the public data.json serve exactly the published records, sources and observations?"""
    recs = {r.get("id"): r for r in served.get("records", [])}
    srcs = {s.get("id") for s in served.get("sources", [])}
    obs = {o.get("id") for o in served.get("sourceObservations", [])}
    missing = []
    for it in items:
        r = recs.get(it["recordId"])
        if not r or r.get("content") != it["record"]["content"] or (r.get("provenance") or {}).get("contentHash") != it["contentHash"]:
            missing.append(it["recordId"])
            continue
        if any(s not in srcs for s in it["needsSources"]):
            missing.append(it["recordId"] + ":source")
        if any(o["id"] not in obs for o in it["observations"]):
            missing.append(it["recordId"] + ":observation")
    return not missing, missing


def served_without(served, record_ids):
    present = {r.get("id") for r in served.get("records", [])}
    return not (present & set(record_ids)), sorted(present & set(record_ids))


def verify_live(site_url, http, check, timeout, interval, sleep=time.sleep, clock=time.monotonic):
    """Poll the public site until ``check(served_json)`` -> (ok, detail) is true or the deadline passes."""
    deadline, attempts, last, last_generated = clock() + timeout, 0, "no response", None
    while True:
        attempts += 1
        url = f"{site_url.rstrip('/')}/data.json?ti-verify={int(time.time())}-{attempts}"
        res = http(url, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"}, timeout=30)
        status = getattr(res, "status", None)
        if status == 200:
            try:
                served = json.loads(res.body.decode("utf-8"))
                last_generated = (served.get("meta") or {}).get("generated")
                ok, detail = check(served)
                if ok:
                    return {"ok": True, "attempts": attempts, "served_generated": (served.get("meta") or {}).get("generated"),
                            "served_sha256": hashlib.sha256(res.body).hexdigest(), "url": site_url}
                last = f"served data.json does not match yet: {detail}"
            except (ValueError, UnicodeDecodeError) as exc:
                last = f"served data.json unreadable ({type(exc).__name__})"
        else:
            last = f"HTTP {status or getattr(res, 'error_kind', 'no response')}"
        if clock() >= deadline:
            return {"ok": False, "attempts": attempts, "detail": last, "url": site_url, "served_generated": last_generated}
        sleep(interval)


def site_url_for(root, pub_cfg):
    p = repo_file(root, pub_cfg["verification"]["site_url_from"])
    return json.loads(p.read_text(encoding="utf-8"))["release"]["site_url"]
