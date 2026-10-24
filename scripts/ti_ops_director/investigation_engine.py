"""03AA-04_Investigation_Verification_TI: take discovery candidates, investigate their evidence, assess each claim, publish what
passes TI's rules through the established path, verify it on the public site, hold the rest with reasons, and keep
reassessing what is published.

Persistent state is one ledger (``investigation_state.json``) on a dedicated state branch (never main), written by
compare-and-swap commits, exactly as the Country Builder and Production Controller do. The Master Agent's SQLite queue is
local-only and is not serviced on GitHub Actions, so tasks live in the ledger in the Director's task schema and dedupe-key
format; held exceptions are mirrored into the queue when it is serviced (see investigation_worker.py).

A run is one bounded pass. It can be killed at any point: the lease expires, the next run recovers running tasks, adopts any
event that reached main without reaching the ledger, and never publishes the same content twice.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .feed_health import HttpResult
from .investigation_assess import assess_hazard, attach_leads
from .investigation_build import build_item, content_hash
from .investigation_candidates import (event_id_for, gdacs_signal_candidates, match_lead, pre_eligibility, represented_event_id,
                                       triage_lead_candidates)
from .investigation_config import AGENT_NAME, WORKER_NAME, IVConfig, load_config
from .investigation_publish import (PublishRefused, head_sha, make_main_backend, owned, publish_items, release_is_ours,
                                    rollback_publication, served_matches, served_without, target_site_url, verify_live)
from .investigation_sources import RequestBudget, fetch_gdacs, iso_utc, make_robots, make_transport, parse_dt
from .monitor_state import FileStateBackend, StateSyncError, dump_state
from .production import http_request
from .sanitise import clean_text
from .store import fingerprint, task_dedupe_key, utc_now

EXIT_OK, EXIT_CONFIG, EXIT_STATE, EXIT_PUBLICATION = 0, 3, 4, 5
SCHEMA = 1
IV_PATHS = {"state": "investigation_state.json", "report": "investigation_report.md"}
TERMINAL = ("INELIGIBLE", "EXPIRED")
APPLICATION = "Travel Intelligence"
TASK_INVESTIGATION, TASK_VERIFICATION = "investigation", "event_reassessment"
CYPRUS = ZoneInfo("Europe/Nicosia")


def cyprus_time(value):
    d = value if isinstance(value, datetime) else parse_dt(value)
    return d.astimezone(CYPRUS).strftime("%Y-%m-%d %H:%M %Z") if d else "n/a"


def empty_state():
    return {"schemaVersion": SCHEMA, "agent": AGENT_NAME, "controls": {"paused": False, "stopped": False}, "lease": None,
            "candidates": {}, "tasks": {}, "events": {}, "publications": {}, "exceptions": {}, "runs": []}


def sections(state):
    state.setdefault("controls", {"paused": False, "stopped": False})
    state.setdefault("lease", None)
    for k, empty in (("candidates", {}), ("tasks", {}), ("events", {}), ("publications", {}), ("exceptions", {}), ("runs", [])):
        if not isinstance(state.get(k), type(empty)):
            state[k] = empty
    return state


def make_backend(env, root, cfg: IVConfig, create=True):
    choice = (env.get("TI_IV_STATE_BACKEND") or "git").strip().lower()
    if choice == "file":
        base = Path(env.get("TI_IV_STATE_DIR") or Path(root) / "var" / "investigation")
        return FileStateBackend({k: base / v for k, v in IV_PATHS.items()})
    if choice != "git":
        cfg.errors.append("TI_IV_STATE_BACKEND must be 'git' or 'file'")
        return None
    if not cfg.state_branch:
        return None
    from .production import BranchStateBackend
    return BranchStateBackend(root, IV_PATHS, branch=cfg.state_branch, mirror_worktree=False, create=create)


class FixtureNet:
    """Serves RECORDED provider answers for a rehearsal. Everything it produces is labelled FIXTURE."""
    def __init__(self, doc):
        self.events, self.calls = doc.get("gdacs_events", {}), []

    def __call__(self, url, **kw):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return HttpResult(404, b"", "http_error")
        import re
        m = re.search(r"eventtype=([A-Z]{2}).*eventid=(\d+)", url)
        body = self.events.get(f"{m.group(1)}:{m.group(2)}") if m else None
        return HttpResult(200, json.dumps(body).encode(), None) if body else HttpResult(404, b"", "http_error")


class Engine:
    def __init__(self, cfg: IVConfig, root, backend, *, http=None, site_http=http_request, main_backend=None, clock=utc_now,
                 env=None, out=print, sleep=time.sleep, data_path=None, signals=None, monitor_state=None):
        self.cfg, self.root, self.backend, self.clock, self.out, self.sleep = cfg, Path(root), backend, clock, out, sleep
        self.env = os.environ if env is None else env
        self.http = http
        self.site_http, self.main_backend = site_http, main_backend
        self.owner = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.data_path = Path(data_path or self.root / "data.json")
        self._signals, self._monitor = signals, monitor_state
        self.stats = {}

    # ---- inputs --------------------------------------------------------------------------------------------------
    def load_data(self):
        return json.loads(self.data_path.read_text(encoding="utf-8"))

    def _read(self, override, rel):
        if override is not None:
            return override
        p = self.root / rel
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except ValueError:
            return {}

    def signals(self):
        return self._read(self._signals, "data/phase1_signals.json")

    def monitor_state(self):
        return self._read(self._monitor, "data/continuous_monitor_state.json")

    def transport(self):
        if self.http is not None:
            return self.http
        hosts = {h for s in self.cfg.sources.values() for h in s["hosts"]}
        return make_transport(hosts, self.cfg.evidence["max_redirects"])

    # ---- publication gate --------------------------------------------------------------------------------------
    def publication_allowed(self):
        cfg = self.cfg
        if not cfg.publication_enabled:
            return False, "publication_disabled_in_mode_or_configuration"
        if cfg.target_branch == "main":
            ev, ref = self.env.get("GITHUB_EVENT_NAME", ""), self.env.get("GITHUB_REF", "")
            if ev not in cfg.publication_cfg["allowed_events"] or ref != cfg.publication_cfg["required_ref"]:
                return False, f"publication to main refused: event {ev or 'none'} on {ref or 'no ref'}"
            if cfg.trial or cfg.fixture:
                return False, "a trial or fixture run never publishes to main"
        return True, "allowed"

    # ---- ledger --------------------------------------------------------------------------------------------------
    def _tx(self, mutate, message="ledger"):
        def fn(snap):
            st = sections(snap.state() or empty_state())
            before = json.dumps(st, sort_keys=True)
            result = mutate(st)
            if snap.text("state") and json.dumps(st, sort_keys=True) == before:
                return None, result                  # nothing changed: no commit to the state branch
            return {"state": dump_state(st)}, result
        return self.backend.transact(fn, message=f"{AGENT_NAME}: {message}")[1]

    def _acquire(self, now):
        def m(st):
            lease = st["lease"]
            if lease and parse_dt(lease["expiresAt"]) > now and lease["owner"] != self.owner:
                return False
            st["lease"] = {"owner": self.owner, "acquiredAt": iso_utc(now),
                           "expiresAt": iso_utc(now + timedelta(minutes=self.cfg.retries["lease_minutes"]))}
            return True
        return self._tx(m, "lease")

    def _release(self):
        def m(st):
            if st["lease"] and st["lease"]["owner"] == self.owner:
                st["lease"] = None
        try:
            self._tx(m, "lease release")
        except StateSyncError:
            pass                                    # the lease expires on its own

    # ---- the run -------------------------------------------------------------------------------------------------
    def run(self, now=None) -> int:
        cfg, out = self.cfg, self.out
        now = now or self.clock()
        if cfg.errors:
            out("ERROR: " + "; ".join(cfg.errors))
            return EXIT_CONFIG
        if cfg.mode == "off":
            out(f"{AGENT_NAME}: OFF ({cfg.mode_source}). Nothing runs.")
            return EXIT_OK
        try:
            state = sections(self.backend.snapshot().state() or empty_state()) if self.backend else empty_state()
        except StateSyncError as exc:
            if cfg.mode in ("draft", "publish"):
                out(f"::error title=Investigation ledger::{exc}")
                return EXIT_STATE
            state = empty_state()
        ctl = state["controls"]
        if ctl.get("stopped") or ctl.get("paused"):
            out(f"{AGENT_NAME}: {'STOPPED' if ctl.get('stopped') else 'PAUSED'} by an operator; nothing was processed.")
            return EXIT_OK
        data = self.load_data()
        if cfg.mode == "observe":
            report = self._observe(state, data, now)
            out(report)
            self._summary(report)
            return EXIT_OK
        try:
            if not self._acquire(now):
                out(f"{AGENT_NAME}: another run holds the lease; this run does nothing (overlap prevented).")
                return EXIT_OK
        except StateSyncError as exc:
            out(f"::error title=Investigation ledger::{exc}")
            return EXIT_STATE
        code = EXIT_OK
        try:
            code = self._run_locked(now, data)
        except StateSyncError as exc:
            out(f"::error title=Investigation ledger::{exc}")
            code = EXIT_STATE
        finally:
            self._release()
        return code

    def _run_locked(self, now, data):
        cfg = self.cfg
        stats = self.stats = {"intake": {}, "tasks": 0, "assessed": 0, "ready": 0, "held": 0, "ineligible": 0, "unchanged": 0, "requests": 0,
                              "published": [], "updated": [], "verifications": [], "failures": [], "recovered": 0, "adopted": 0}
        publication = {"attempted": False, "failures": []}
        # 1. recover from an earlier interrupted run, adopt anything that reached main without reaching the ledger
        def prep(st):
            n = 0
            for t in st["tasks"].values():
                if t["state"] == "running" and (parse_dt(t.get("leaseExpires")) or now) <= now:
                    t.update(state="pending", leaseOwner=None, recovered=t.get("recovered", 0) + 1)
                    n += 1
            return n
        stats["recovered"] = self._tx(prep, "recover")
        stats["adopted"] = self._adopt(data, now)
        # 2. intake discovery candidates, 3. plan tasks
        self._tx(lambda st: stats["intake"].update(self._intake(st, data, now)), "intake")
        tasks = self._tx(lambda st: self._plan(st, now), "plan")
        # 4. investigate and assess (bounded), then persist outcomes before any publication
        results = self._investigate(tasks, data, now)
        self._tx(lambda st: self._record_results(st, results, now), "assessment results")
        # 5. publish what passed
        items = [r["item"] for r in results if r.get("item")]
        if items:
            publication = self._publish(items, now)
        # 6. bounded rechecks of earlier publications whose verification was pending
        stats["verifications"] = self._recheck_pending(now)
        for v in stats["verifications"]:
            publication["failures"] += v.get("failures", [])
        code = self._persist(now, results, publication)
        return code

    # ---- adoption (restart recovery) ---------------------------------------------------------------------------
    def _adopt(self, data, now):
        """An event of ours is on main but the ledger does not know it (the previous run died between push and ledger write)."""
        mine = [e for e in data["events"] if owned(e)]
        if not mine:
            return 0

        def m(st):
            n = 0
            for e in mine:
                if e["id"] in st["events"]:
                    continue
                pid = e["provenance"]["publicationId"]
                st["events"][e["id"]] = {"eventId": e["id"], "groupKey": f"gdacs:{e.get('gdacsEventType')}:{e.get('gdacsEventId')}",
                                         "candidateId": e["provenance"].get("candidateId"), "publicationId": pid,
                                         "contentHash": e["provenance"]["contentHash"], "evidenceHashes": e["provenance"].get("evidenceHashes", {}),
                                         "lifecycle": e.get("lifecycleStage"), "eventStatus": e.get("eventStatus"), "state": "PUBLISHED",
                                         "publishedAt": e.get("lastUpdated"), "lastAssessedAt": e.get("lastUpdated"),
                                         "nextAssessmentAt": iso_utc(now), "reassessFailures": 0, "adopted": True}
                cid = e["provenance"].get("candidateId")
                if cid in st["candidates"]:
                    st["candidates"][cid]["status"] = "PUBLISHED"
                if pid not in st["publications"] or st["publications"][pid].get("state") == "PENDING":
                    st["publications"][pid] = {"id": pid, "at": iso_utc(now), "state": "VERIFICATION_PENDING", "records": [e["id"]], "adopted": True,
                                               "pending": {"verifyItems": [self._verify_item(e)], "rechecks": 0, "generated": None,
                                                           "firstCheckedAt": iso_utc(now), "commit": None}}
                n += 1
            return n
        return self._tx(m, "adopt orphaned publication")

    @staticmethod
    def _verify_item(event, observation_ids=None):
        return {"eventId": event["id"], "event": {"summary": event["summary"]}, "contentHash": event["provenance"]["contentHash"],
                "needsSources": [], "observations": [{"id": o} for o in (observation_ids or event.get("sourceObservationIds", []))]}

    # ---- intake --------------------------------------------------------------------------------------------------
    def _intake(self, st, data, now):
        cfg = self.cfg
        prods = cfg.scope["candidate_producers"]
        info = {}
        hazards, s1 = ([], {"producer": "discovery_signal_gdacs", "disabled": True})
        if "discovery_signal_gdacs" in prods:
            hazards, s1 = gdacs_signal_candidates(self.signals(), data, cfg, now)
        leads, s2 = ([], {"producer": "triage_leads", "disabled": True})
        if "triage_leads" in prods:
            leads, s2 = triage_lead_candidates(self.monitor_state(), data, cfg, now)
        info.update(gdacs=s1, leads=s2, new=0, deferred=0, reopened=0, ineligible=0)
        cap = cfg.scope["max_new_candidates_per_run"]
        C = st["candidates"]
        for c in hazards:
            e = C.get(c["id"])
            if e is None:
                if info["new"] >= cap:
                    info["deferred"] += 1
                    continue
                info["new"] += 1
                ok, why = pre_eligibility(c, cfg, now)
                rep = represented_event_id(data, c)
                known = next((x for x in st["events"].values() if x["groupKey"] == c["groupKey"]), None)
                if rep and not known and not any(owned(ev) and ev["id"] == rep for ev in data["events"]):
                    ok, why = False, f"already_represented_by:{rep}"
                C[c["id"]] = {"id": c["id"], "producer": c["producer"], "kind": c["kind"], "groupKey": c["groupKey"],
                              "eventTypeKey": c["eventTypeKey"], "status": "NEW" if ok else "INELIGIBLE", "reason": None if ok else why,
                              "firstSeen": iso_utc(now), "lastSeen": iso_utc(now), "signalFingerprint": c["signalFingerprint"],
                              "assessedFingerprint": None, "payload": c, "leads": [], "retries": 0}
                if not ok:
                    info["ineligible"] += 1
                elif known:
                    C[c["id"]].update(status="PUBLISHED", reason="known_event")
            else:
                if now - (parse_dt(e["lastSeen"]) or now) > timedelta(hours=12):
                    e["lastSeen"] = iso_utc(now)          # throttled: only prune bookkeeping, so an idle run writes nothing here
                if e["signalFingerprint"] != c["signalFingerprint"]:
                    e["signalFingerprint"], e["payload"] = c["signalFingerprint"], {**c}
                    if e["status"] == "INELIGIBLE":
                        ok, why = pre_eligibility(c, cfg, now)
                        if ok and not str(e.get("reason", "")).startswith("already_represented"):
                            e.update(status="NEW", reason=None)
                            info["reopened"] += 1
        for c in leads:
            if c["id"] in C:
                if now - (parse_dt(C[c["id"]]["lastSeen"]) or now) > timedelta(hours=12):
                    C[c["id"]]["lastSeen"] = iso_utc(now)
                continue
            if info["new"] >= cap:
                info["deferred"] += 1
                continue
            info["new"] += 1
            pool = [x["payload"] for x in C.values() if x["kind"] == "hazard_signal" and x["status"] not in ("EXPIRED",)]
            hit = match_lead(c, pool)
            C[c["id"]] = {"id": c["id"], "producer": c["producer"], "kind": c["kind"], "groupKey": c["groupKey"],
                          "eventTypeKey": c["eventTypeKey"], "firstSeen": iso_utc(now), "lastSeen": iso_utc(now),
                          "signalFingerprint": c["signalFingerprint"], "payload": c, "retries": 0,
                          "status": "ATTACHED" if hit else "WAITING", "attachedTo": hit["id"] if hit else None,
                          "reason": None if hit else "no_primary_event_found_yet"}
            if hit:
                C[hit["id"]].setdefault("leads", []).append(c["id"])
        for e in C.values():                              # a waiting lead is retried until it is too old to matter
            if e["kind"] == "report_lead" and e["status"] == "WAITING":
                rp = parse_dt(e["payload"]["lead"].get("reportPublishedAt"))
                pool = [x["payload"] for x in C.values() if x["kind"] == "hazard_signal" and x["status"] not in ("EXPIRED",)]
                hit = match_lead(e["payload"], pool)
                if hit:
                    e.update(status="ATTACHED", attachedTo=hit["id"], reason=None)
                    C[hit["id"]].setdefault("leads", []).append(e["id"])
                elif rp is None or now - rp > timedelta(hours=cfg.fresh["max_event_age_hours"]):
                    e.update(status="EXPIRED", reason="no_primary_event_found_before_the_report_aged_out")
        self._prune(st, now)
        return info

    def _prune(self, st, now):
        for cid in [k for k, e in st["candidates"].items() if e["status"] in TERMINAL + ("ATTACHED",)
                    and now - (parse_dt(e["lastSeen"]) or now) > timedelta(days=21)]:
            del st["candidates"][cid]
        for k in [k for k, t in st["tasks"].items() if t["state"] == "completed" and now - (parse_dt(t.get("finishedAt")) or now) > timedelta(days=14)]:
            del st["tasks"][k]
        for k in [k for k, e in st["exceptions"].items() if e["state"] == "RESOLVED" and now - (parse_dt(e.get("resolvedAt")) or now) > timedelta(days=30)]:
            del st["exceptions"][k]
        st["runs"] = st["runs"][-100:]

    # ---- planning --------------------------------------------------------------------------------------------------
    def _bucket(self, now):
        return f"{now:%Y-%m-%d}T{now.hour // 2 * 2:02d}"

    def _task(self, st, ttype, cand_id, group, inputs, now, bucket, priority):
        fp = fingerprint(inputs)
        key = task_dedupe_key(ttype, group, fp, bucket, APPLICATION)
        t = st["tasks"].get(key)
        if t and t["state"] in ("pending", "running", "completed"):
            return None, False
        if any(x["type"] == ttype and x["candidateId"] == cand_id and x["state"] in ("pending", "running") for x in st["tasks"].values()):
            return None, False                       # an earlier window's task for this candidate is still open (recovered or deferred)
        task = {"id": "ivt-" + hashlib.sha256(key.encode()).hexdigest()[:10], "dedupeKey": key, "type": ttype, "candidateId": cand_id,
                "groupKey": group, "state": "pending", "attempts": 0, "createdAt": iso_utc(now), "priority": priority,
                "inputs": inputs, "worker": WORKER_NAME, "business_application": APPLICATION,
                "substream": "03AA-04 Intelligence Automation Agents"}
        st["tasks"][key] = task
        return task, True

    def _plan(self, st, now):
        """Create at most one task per candidate per window; return the tasks to run now (pending, bounded)."""
        cfg = self.cfg
        bucket = self._bucket(now)
        for cid, c in st["candidates"].items():
            if c["kind"] != "hazard_signal":
                continue
            known = next((x for x in st["events"].values() if x["groupKey"] == c["groupKey"] and x["state"] == "PUBLISHED"), None)
            level = {"Red": 0, "Orange": 1}.get((c["payload"].get("signal") or {}).get("alertLevel"), 2)
            if known:
                due = (parse_dt(known.get("nextAssessmentAt")) or now) <= now or c["signalFingerprint"] != c.get("assessedFingerprint")
                if due:                              # a published event missing from data.json is investigated too: it becomes a decision
                    self._task(st, TASK_VERIFICATION, cid, c["groupKey"], {"eventId": known["eventId"], "signal": c["signalFingerprint"]},
                               now, bucket, 40 + level)
                continue
            if c["status"] in ("NEW", "HELD", "READY") and c.get("blockedFingerprint") != c["signalFingerprint"]:
                self._task(st, TASK_INVESTIGATION, cid, c["groupKey"], {"candidateId": cid, "signal": c["signalFingerprint"]},
                           now, bucket, 30 + level)
        pending = [t for t in st["tasks"].values() if t["state"] == "pending"]
        pending.sort(key=lambda t: (t["priority"], t["createdAt"]))
        run = pending[: cfg.tasks_per_run]
        for t in run:
            t.update(state="running", attempts=t["attempts"] + 1, startedAt=iso_utc(now), leaseOwner=self.owner,
                     leaseExpires=iso_utc(now + timedelta(minutes=cfg.retries["lease_minutes"])))
        return [json.loads(json.dumps(t)) for t in run]

    # ---- investigation ---------------------------------------------------------------------------------------------
    def _investigate(self, tasks, data, now):
        cfg = self.cfg
        budget = RequestBudget(cfg.evidence["max_requests_per_run"], cfg.evidence["max_requests_per_task"])
        http = self.transport()
        robots = make_robots(http, cfg.evidence["http_timeout_seconds"])
        st = sections(self.backend.snapshot().state() or empty_state())
        by_id = {e["id"]: e for e in data["events"]}
        started = time.monotonic()
        results = []
        for t in tasks:
            self.stats["tasks"] += 1
            res = {"task": t, "item": None}
            results.append(res)
            if time.monotonic() - started > cfg.evidence["run_timeout_seconds"]:
                res.update(outcome="DEFERRED", reasons=["run_timeout_reached"])
                continue
            entry = st["candidates"].get(t["candidateId"])
            if entry is None:
                res.update(outcome="DEFERRED", reasons=["candidate_missing_from_ledger"])
                continue
            cand = entry["payload"]
            eid = event_id_for(cfg, cand)
            known = st["events"].get(eid)
            reassess = known is not None and known["state"] == "PUBLISHED"
            previous = by_id.get(eid) if reassess else None
            if reassess and previous is None:
                res.update(outcome="HOLD", exceptionClass="decision", reasons=["published_event_missing_from_data"], reassess=True, eventId=eid)
                continue
            if reassess and not self._untouched(previous):
                # another process (a person, the nightly reviewer) edited the event after we published it: never overwrite that
                res.update(outcome="HOLD", exceptionClass="decision", reasons=["published_event_modified_by_another_process"], reassess=True,
                           eventId=eid, standDown=True)
                continue
            try:
                doc = fetch_gdacs(cfg, self.root, http, robots, now, cand["eventTypeKey"], cand["gdacs"]["eventId"], t["id"], budget,
                                  retrieval="fixture" if cfg.fixture else "live")
                docs = [doc]
                a = assess_hazard(cand, docs, cfg, data, now, reassess=reassess)
                leads = [st["candidates"][x]["payload"] for x in entry.get("leads", []) if x in st["candidates"]]
                attach_leads(a, leads)
            except Exception as exc:                   # a defect in one task must not stop the others
                res.update(outcome="HOLD", exceptionClass="retry", reasons=[f"task_error:{type(exc).__name__}"], reassess=reassess)
                continue
            self.stats["assessed"] += 1
            res.update(assessment=a, docs=docs, reassess=reassess, eventId=eid, evidenceHashes=a["evidenceHashes"])
            if a["decision"] == "PUBLISH":
                if reassess and known["contentHash"] and a["evidenceHashes"] == known.get("evidenceHashes") and a["lifecycle"] == known["lifecycle"] \
                        and a["eventStatus"] == known["eventStatus"]:
                    res.update(outcome="UNCHANGED", reasons=["evidence_unchanged"])
                    continue
                item = build_item(cfg, cand, a, docs, data, now, "pending", previous=previous)
                if reassess and item["contentHash"] == known["contentHash"]:
                    res.update(outcome="UNCHANGED", reasons=["content_unchanged"])
                    continue
                res.update(outcome="READY", item=item, reasons=[])
            elif a["decision"] == "INELIGIBLE" and not reassess:
                res.update(outcome="INELIGIBLE", reasons=a["reasons"])
            else:                                       # HOLD, or a problem found while reassessing something already published
                res.update(outcome="HOLD", exceptionClass=a.get("exceptionClass") or "retry", reasons=a["reasons"] or ["held"])
        self.stats["requests"] = budget.run_used
        return results

    @staticmethod
    def _untouched(event):
        """True when the event still carries exactly the content this agent published (provenance hash matches its fields)."""
        pv = event.get("provenance") or {}
        try:
            return content_hash(event["title"], event["summary"], event["eventStatus"], event.get("lifecycleStage"),
                                pv.get("evidenceHashes") or {}) == pv.get("contentHash")
        except KeyError:
            return False

    # ---- recording ---------------------------------------------------------------------------------------------------
    def _action(self, reasons, cls):
        r = " ".join(reasons)
        if "BLOCKED_BY_ROBOTS" in r:
            return "The provider's robots.txt does not permit automated access to this endpoint. Decision: approve a documented exception in config (as for other agents) or name another permitted source."
        if "NOT_PERMITTED" in r:
            return "The source is not usable under the source-register rules (see reason). Decision: review the register entry; nothing else can change this."
        if "published_event_modified_by_another_process" in r:
            return ("Someone or something else edited this published event after the agent published it, so the agent has stopped reassessing it and left the "
                    "edit in place. Decision: keep the edited event as it is (nothing to do), or have the edit reverted if it was not intended.")
        if "published_event_missing_from_data" in r:
            return "A published event is no longer in data.json although the ledger holds it. Decision: confirm whether it was removed deliberately."
        return ("Retried automatically on every run; it clears when the evidence is readable, consistent and current, or the event ages out."
                if cls == "retry" else "Needs a decision; it cannot be resolved by retrying.")

    def _upsert_exception(self, st, key, cid, cls, reasons, now, detail=""):
        e = st["exceptions"].get(key)
        if e is None or e["state"] == "RESOLVED":
            e = st["exceptions"][key] = {"key": key, "state": "HELD", "candidateId": cid, "firstSeen": iso_utc(now), "occurrences": 0,
                                         "rechecks": 0, "class": cls}
        e.update(lastSeen=iso_utc(now), reasons=[clean_text(r, 160) for r in reasons][:8], detail=clean_text(detail, 300))
        e["occurrences"] += 1
        e["rechecks"] += 1
        limit = (self.cfg.retries["conflict_rechecks_before_decision"] if any("conflict" in r or "disagree" in r for r in reasons)
                 else self.cfg.retries["collection_failure_rechecks_before_decision"])
        if cls == "retry" and e["rechecks"] >= limit:
            cls = "decision"
            e["escalatedAfterRechecks"] = e["rechecks"]
        e["class"], e["requiresHuman"] = cls, cls == "decision"
        e["action"] = self._action(reasons, cls)
        return e

    def _resolve_exception(self, st, key, now, why):
        e = st["exceptions"].get(key)
        if e and e["state"] == "HELD":
            e.update(state="RESOLVED", resolvedAt=iso_utc(now), resolution=why)

    def _record_results(self, st, results, now):
        for r in results:
            t = st["tasks"].get(r["task"]["dedupeKey"])
            if t is None:
                continue
            c = st["candidates"].get(t["candidateId"])
            key = t["groupKey"]
            out = r.get("outcome")
            t.update(finishedAt=iso_utc(now), outcome=out, reasons=r.get("reasons", []), leaseOwner=None)
            a = r.get("assessment")
            if a and c is not None:
                doc = (r.get("docs") or [{}])[0]
                c["lastAssessment"] = {"at": iso_utc(now), "decision": a["decision"], "outcome": out, "reasons": a["reasons"][:6],
                                       "eventStatus": a.get("eventStatus"), "lifecycle": a.get("lifecycle"), "independentChains": a["independentChains"],
                                       "claims": {x["type"]: x["status"] for x in a["claims"]}, "changedSinceSignal": a["changedSinceSignal"],
                                       "evidence": {"status": doc.get("status"), "retrievedAt": doc.get("retrievedAt"), "retrieval": doc.get("retrieval"),
                                                    "hash": doc.get("evidenceHash"), "alertLevel": (doc.get("structured") or {}).get("alertLevel"),
                                                    "modified": (doc.get("structured") or {}).get("modified"), "to": (doc.get("structured") or {}).get("to"),
                                                    "isCurrent": (doc.get("structured") or {}).get("isCurrent")}}
            if out == "DEFERRED":
                t.update(state="pending")              # not attempted; the next run takes it
                continue
            t["state"] = "completed"
            if c is not None:
                c["assessedFingerprint"] = c["signalFingerprint"]
                c["lastAssessedAt"] = iso_utc(now)
            if out == "INELIGIBLE":
                self.stats["ineligible"] += 1
                c.update(status="INELIGIBLE", reason=";".join(r["reasons"]))
                self._resolve_exception(st, key, now, "no longer eligible: " + ";".join(r["reasons"]))
            elif out == "HOLD":
                self.stats["held"] += 1
                if not r.get("reassess"):
                    c.update(status="HELD", reason=";".join(r["reasons"]), retries=c.get("retries", 0) + 1)
                else:
                    ev = st["events"].get(r.get("eventId") or "")
                    if ev:
                        if r.get("standDown"):
                            ev.update(state="STOOD_DOWN", nextAssessmentAt=None)      # deliberate: not a failed recheck, never reassessed or overwritten again
                        else:
                            ev["reassessFailures"] = ev.get("reassessFailures", 0) + 1
                            ev["nextAssessmentAt"] = iso_utc(now + timedelta(hours=self.cfg.fresh["reassess_after_hours"]["ALERT"]))
                self._upsert_exception(st, key, c["id"] if c else None, r.get("exceptionClass", "retry"), r["reasons"], now)
            elif out == "READY":
                self.stats["ready"] += 1
                if not r.get("reassess"):
                    c.update(status="READY", reason=None)
                self._resolve_exception(st, key, now, "assessment passed")
            elif out == "UNCHANGED":
                self.stats["unchanged"] += 1
                ev = st["events"].get(r.get("eventId") or "")
                if ev:
                    self._schedule(ev, ev["lifecycle"], now)
                    ev["lastAssessedAt"], ev["reassessFailures"] = iso_utc(now), 0
                self._resolve_exception(st, key, now, "reassessed: evidence unchanged")

    def _schedule(self, ev, lifecycle, now):
        hours = self.cfg.fresh["reassess_after_hours"].get(lifecycle)
        ev["nextAssessmentAt"] = iso_utc(now + timedelta(hours=hours)) if hours else None
        if hours is None:
            ev["state"] = "ARCHIVED"

    # ---- publication ---------------------------------------------------------------------------------------------------
    def _publish(self, items, now):
        cfg = self.cfg
        res = {"attempted": False, "items": [i["eventId"] for i in items], "failures": [], "published": []}
        allowed, why = self.publication_allowed()
        if not allowed:
            res.update(skipped=why)
            self.out(f"publication not attempted: {why}")
            return res
        pub = cfg.publication_cfg
        ver = pub["verification"]
        pub_id = f"pub-{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
        for it in items:
            it["event"]["provenance"]["publicationId"] = pub_id
            for o in it["observations"]:
                o["publicationId"] = pub_id
        backend = self.main_backend or make_main_backend(self.root, cfg.target_branch)
        res.update(attempted=True, publicationId=pub_id)
        self._tx(lambda st: st["publications"].__setitem__(pub_id, {"id": pub_id, "at": iso_utc(now), "state": "PENDING",
                                                                    "records": res["items"], "target": cfg.target_branch}), "publication intent")
        try:
            summary = publish_items(backend, items, pub_id, now, pub["max_events_per_run"])
        except PublishRefused as exc:
            res["failures"].append({"reason": "publication_refused", "detail": str(exc)[:300]})
            self._tx(lambda st: st["publications"][pub_id].update(state="REFUSED", detail=str(exc)[:300]), "publication refused")
            return res
        except StateSyncError as exc:
            res["failures"].append({"reason": "publication_push_failed", "detail": str(exc)[:300]})
            self._tx(lambda st: st["publications"][pub_id].update(state="PUSH_FAILED", detail=str(exc)[:300]), "publication push failed")
            return res
        res["summary"] = summary
        changed = [i for i in items if i["eventId"] in summary["added"] + summary["updated"]]
        self._tx(lambda st: self._after_push(st, items, changed, summary, pub_id, now), "publication pushed")
        if not changed:
            self._tx(lambda st: st["publications"][pub_id].update(state="NO_CHANGE", summary=summary), "publication no change")
            return res
        url = target_site_url(cfg, self.root)
        v = verify_live(url, self.site_http, lambda served: served_matches(served, changed), ver["timeout_seconds"], ver["interval_seconds"],
                        sleep=self.sleep)
        res["verification"] = v
        if v["ok"]:
            res["published"] = [i["eventId"] for i in changed]
            self._tx(lambda st: st["publications"][pub_id].update(state="VERIFIED", commit=summary.get("commit"), verifiedAt=iso_utc(self.clock()),
                                                                  summary=summary, served=v), "publication verified")
            return res
        vitems = [{"eventId": i["eventId"], "event": {"summary": i["event"]["summary"]}, "contentHash": i["contentHash"],
                   "needsSources": i["needsSources"], "observations": [{"id": o["id"]} for o in i["observations"]]} for i in changed]
        base = {"commit": summary.get("commit"), "generated": summary.get("generated"), "verifyItems": vitems,
                "firstCheckedAt": iso_utc(self.clock()), "rechecks": 0, "lastDetail": v.get("detail"), "servedGenerated": v.get("served_generated")}
        res["published"] = [i["eventId"] for i in changed]            # on the target branch; the site has not served it yet
        if release_is_ours(v, summary.get("generated")) and ver.get("rollback_on_incorrect_content"):
            res["failures"].append({"reason": "published_content_incorrect", "detail": v.get("detail")})
            self._rollback(res, backend, pub_id, url, ver, base)
            return res
        res["pending"] = True
        self._tx(lambda st: st["publications"][pub_id].update(state="VERIFICATION_PENDING", pending=base, summary=summary), "verification pending")
        return res

    def _after_push(self, st, items, changed, summary, pub_id, now):
        for it in changed:
            cid = it["candidateId"]
            lifecycle = it["lifecycle"]
            ev = st["events"].get(it["eventId"]) or {"eventId": it["eventId"], "groupKey": it["groupKey"], "candidateId": cid,
                                                     "publishedAt": iso_utc(now), "reassessFailures": 0}
            ev.update(publicationId=pub_id, contentHash=it["contentHash"], evidenceHashes=it["event"]["provenance"]["evidenceHashes"],
                      lifecycle=lifecycle, eventStatus=it["event"]["eventStatus"], state="PUBLISHED", lastAssessedAt=iso_utc(now),
                      reassessFailures=0, commit=summary.get("commit"))
            self._schedule(ev, lifecycle, now)
            st["events"][it["eventId"]] = ev
            if cid in st["candidates"]:
                st["candidates"][cid].update(status="PUBLISHED", reason=None, assessedFingerprint=st["candidates"][cid]["signalFingerprint"])
            self._resolve_exception(st, it["groupKey"], now, "published")
        for s in summary["skipped"]:
            it = next((i for i in items if i["eventId"] == s["eventId"]), None)
            if it:
                c = st["candidates"].get(it["candidateId"])
                if c and c["status"] == "READY":
                    c.update(status="HELD", reason=s["reason"])
                self._upsert_exception(st, it["groupKey"], it["candidateId"], "decision" if "not_owned" in s["reason"] else "retry",
                                       [s["reason"]], now)

    def _rollback(self, res, backend, pub_id, url, ver, base):
        try:
            rb = rollback_publication(backend, pub_id, self.clock())
            res["rollback"] = rb
            v2 = verify_live(url, self.site_http, lambda served: served_without(served, rb["removed"]) if rb["removed"] else (True, []),
                             ver.get("recheck_timeout_seconds", 120), ver["interval_seconds"], sleep=self.sleep)
            res["rollback"]["verification"] = v2
            base["rollbackRemoved"] = rb["removed"]
            if not v2["ok"]:
                res["failures"].append({"reason": "rollback_not_yet_served", "detail": v2.get("detail")})
        except (PublishRefused, StateSyncError) as exc:
            res["failures"].append({"reason": "rollback_failed", "detail": str(exc)[:300]})
        state = ("ROLLBACK_PENDING" if "rollback" in res and not res["rollback"]["verification"]["ok"]
                 else "ROLLED_BACK" if "rollback" in res else "VERIFICATION_FAILED")

        def m(st):
            st["publications"][pub_id].update(state=state, pending=base, detail=base.get("lastDetail"), commit=base.get("commit"))
            if state == "ROLLED_BACK":
                for eid in res["rollback"]["removed"] + res["rollback"]["restored"]:
                    ev = st["events"].pop(eid, None) if eid in res["rollback"]["removed"] else None
                    for c in st["candidates"].values():
                        if c["status"] == "PUBLISHED" and (ev and c["groupKey"] == ev["groupKey"]):
                            # no recovery loop: the same signal is not republished after a demonstrated error
                            c.update(status="HELD", reason="rolled_back_after_incorrect_content", blockedFingerprint=c["signalFingerprint"])
            self._upsert_exception(st, "publication:" + pub_id, None, "decision",
                                   [res["failures"][0]["reason"] if res["failures"] else state], self.clock(), detail=base.get("lastDetail") or "")
        self._tx(m, "rollback")

    def _recheck_pending(self, now):
        """Bounded rechecks of publications whose verification (or rollback) is pending. A slow deployment never deletes content."""
        cfg = self.cfg
        ver = cfg.publication_cfg["verification"]
        limit = ver.get("max_rechecks", 6)
        report = []
        try:
            st = sections(self.backend.snapshot().state() or empty_state())
        except StateSyncError:
            return report
        pend_ids = [p for p, v in st["publications"].items() if v.get("state") in ("VERIFICATION_PENDING", "ROLLBACK_PENDING")]
        if not pend_ids:
            return report
        backend = self.main_backend or make_main_backend(self.root, cfg.target_branch)
        for pub_id in pend_ids:
            p = st["publications"][pub_id]
            pend, url = p["pending"], target_site_url(cfg, self.root)
            items = None
            if p["state"] == "ROLLBACK_PENDING":
                removed = pend.get("rollbackRemoved") or []
                v = verify_live(url, self.site_http, lambda served: served_without(served, removed) if removed else (True, []),
                                ver.get("recheck_timeout_seconds", 120), ver["interval_seconds"], sleep=self.sleep)
                if v["ok"]:
                    self._tx(lambda s2: s2["publications"][pub_id].update(state="ROLLED_BACK", pending=None, served=v), "rollback verified")
                    report.append({"publicationId": pub_id, "result": "rollback_verified"})
                    continue
            else:
                try:
                    main = backend.snapshot().data() or {}
                except StateSyncError:
                    main = {}
                evs = {e.get("id"): e for e in main.get("events", [])}
                items = [i for i in pend["verifyItems"] if (evs.get(i["eventId"], {}).get("provenance") or {}).get("publicationId") == pub_id
                         and evs[i["eventId"]]["provenance"].get("contentHash") == i["contentHash"]]
                if not items:
                    self._tx(lambda s2: s2["publications"][pub_id].update(state="SUPERSEDED", pending=None), "publication superseded")
                    report.append({"publicationId": pub_id, "result": "superseded"})
                    continue
                v = verify_live(url, self.site_http, lambda served: served_matches(served, items), ver.get("recheck_timeout_seconds", 120),
                                ver["interval_seconds"], sleep=self.sleep)
                if v["ok"]:
                    self._tx(lambda s2: s2["publications"][pub_id].update(state="VERIFIED", verifiedAt=iso_utc(self.clock()), served=v, pending=None,
                                                                      commit=pend.get("commit")), "publication verified (recheck)")
                    report.append({"publicationId": pub_id, "result": "verified", "records": p.get("records"), "verification": v})
                    continue
            n = pend["rechecks"] + 1
            pend.update(rechecks=n, lastDetail=v.get("detail"), servedGenerated=v.get("served_generated"), lastCheckedAt=iso_utc(self.clock()))
            res = {"failures": []}
            if items is not None and release_is_ours(v, pend.get("generated")) and ver.get("rollback_on_incorrect_content"):
                res["failures"].append({"reason": "published_content_incorrect", "detail": v.get("detail")})
                self._rollback(res, backend, pub_id, url, ver, pend)
                report.append({"publicationId": pub_id, "result": "rolled_back_incorrect_content", "failures": res["failures"]})
            elif n >= limit:
                reason = "verification_unresolved" if items is not None else "rollback_not_yet_served"
                self._tx(lambda s2: (s2["publications"][pub_id].update(state="VERIFICATION_FAILED" if items is not None else "ROLLBACK_PENDING", pending=pend),
                                     self._upsert_exception(s2, "publication:" + pub_id, None, "decision", [reason], self.clock(), v.get("detail") or "")),
                         "verification unresolved")
                report.append({"publicationId": pub_id, "result": "unresolved_after_rechecks", "detail": v.get("detail"),
                               "failures": [{"reason": reason, "detail": v.get("detail")}]})
            else:
                self._tx(lambda s2: s2["publications"][pub_id].update(pending=pend), "recheck recorded")
                report.append({"publicationId": pub_id, "result": "still_pending", "rechecks": n, "detail": v.get("detail")})
        return report

    # ---- operator controls -----------------------------------------------------------------------------------
    def set_controls(self, command):
        def m(st):
            st["controls"]["paused"] = command != "resume"
            st["controls"]["stopped"] = command == "stop"
            return dict(st["controls"])
        return self._tx(m, f"operator {command}")

    def manual_rollback(self, pub_id, by):
        """An operator asks for exactly what one automatic publication changed to be removed or restored. Verified, then recorded."""
        now = self.clock()
        cfg = self.cfg
        backend = self.main_backend or make_main_backend(self.root, cfg.target_branch)
        rb = rollback_publication(backend, pub_id, now)
        v = verify_live(target_site_url(cfg, self.root), self.site_http,
                        lambda served: served_without(served, rb["removed"]) if rb["removed"] else (True, []),
                        cfg.publication_cfg["verification"].get("recheck_timeout_seconds", 120),
                        cfg.publication_cfg["verification"]["interval_seconds"], sleep=self.sleep)
        base = {"commit": rb.get("commit"), "rollbackRemoved": rb["removed"], "verifyItems": [], "rechecks": 0, "lastDetail": v.get("detail")}

        def m(st):
            p = st["publications"].setdefault(pub_id, {"id": pub_id, "records": rb["removed"] + rb["restored"]})
            p.update(state="ROLLED_BACK" if v["ok"] else "ROLLBACK_PENDING", pending=None if v["ok"] else base, rolledBackBy=clean_text(by, 60),
                     rolledBackAt=iso_utc(now))
            for eid in rb["removed"]:
                ev = st["events"].pop(eid, None)
                for c in st["candidates"].values():
                    if ev and c["groupKey"] == ev["groupKey"] and c["status"] == "PUBLISHED":
                        c.update(status="HELD", reason="rolled_back_by_operator", blockedFingerprint=c["signalFingerprint"])
        self._tx(m, "manual rollback")
        return {**rb, "verified": v["ok"]}

    # ---- persistence and reporting -------------------------------------------------------------------------------
    def _persist(self, now, results, publication):
        cfg, stats = self.cfg, self.stats
        fail = publication["failures"]
        code = EXIT_PUBLICATION if fail else EXIT_OK
        run = {"at": iso_utc(now), "atCyprus": cyprus_time(now), "mode": cfg.mode, "trial": cfg.trial, "fixture": bool(cfg.fixture),
               "tasks": stats["tasks"], "assessed": stats["assessed"], "ready": stats["ready"], "held": stats["held"],
               "ineligible": stats["ineligible"], "unchanged": stats["unchanged"], "requests": stats["requests"],
               "published": list((publication.get("summary") or {}).get("added", [])), "updated": list((publication.get("summary") or {}).get("updated", [])),
               "publication": publication.get("publicationId"), "publicationSkipped": publication.get("skipped"), "exit": code}
        report_box = {}
        self.facts = {"tasks": stats["tasks"], "published_events": len(run["published"]) + len(run["updated"]), "exceptions_held": stats["held"],
                      "exceptions_need_decision": 0, "verification_pending": bool(publication.get("pending")) or any(
                          v.get("result") == "still_pending" for v in stats.get("verifications", [])),
                      "publication_failures": [f["reason"] for f in fail], "paused_or_stopped": False}

        def m(st):
            self.facts["exceptions_need_decision"] = sum(1 for e in st["exceptions"].values() if e["state"] == "HELD" and e.get("requiresHuman"))
            st["runs"].append(run)
            report_box["text"] = self._render(st, now, results, publication)
            return None

        def fn(snap):
            st = sections(snap.state() or empty_state())
            m(st)
            return {"state": dump_state(st), "report": report_box["text"]}, None
        self.backend.transact(fn, message=f"{AGENT_NAME}: run {iso_utc(now)}")
        self.out(report_box["text"])
        self._summary(report_box["text"])
        return code

    def _render(self, st, now, results, publication):
        cfg, stats = self.cfg, self.stats
        intake = stats.get("intake", {})
        g, l = intake.get("gdacs", {}), intake.get("leads", {})
        label = "FIXTURE REHEARSAL (recorded provider data, isolated target)" if cfg.fixture else ("TRIAL (isolated target)" if cfg.trial else cfg.mode.upper())
        lines = [f"# {AGENT_NAME}", "",
                 f"Run at {cyprus_time(now)} (Cyprus time); stored timestamps are UTC. Mode: **{label}**. Scope: {', '.join(cfg.event_types)} in "
                 f"{', '.join(cfg.countries)}, GDACS alert {cfg.scope['min_alert_level']} or higher.", "",
                 "## Upstream discovery", "",
                 f"- Discovery signals (GDACS, `data/phase1_signals.json` generated {cyprus_time(g.get('signalGenerated'))}): {g.get('snapshot', 'n/a')}; "
                 f"{g.get('items', 0)} items, {g.get('out_of_scope_type', 0)} other hazard types, {g.get('below_alert', 0)} below alert threshold, "
                 f"{g.get('no_tracked_country', 0)} outside the scope countries.",
                 f"- Haiku-triaged leads: {l.get('items', 0)} stored. {l.get('note') or ''} Paid triage is a separate agent's switch; this agent never changes it.",
                 f"- **Intake path this run:** direct GDACS discovery signals ({g.get('items', 0)} items) only; monitor/triage leads: {l.get('items', 0)}. "
                 + ("The monitor -> Haiku triage -> candidates chain is **not operating** (triage has not completed), so the full discovery chain is not connected end to end."
                    if l.get('items', 0) == 0 else "Triaged leads are flowing from the monitor."), "",
                 "## This run", "",
                 "| Step | Count |", "|---|---|",
                 f"| New candidates taken in | {intake.get('new', 0)} (deferred {intake.get('deferred', 0)}, reopened {intake.get('reopened', 0)}) |",
                 f"| Tasks started | {stats['tasks']} (recovered from an interrupted run: {stats['recovered']}) |",
                 f"| Assessed | {stats['assessed']} |", f"| Passed the rules | {stats['ready']} |", f"| Held (exceptions) | {stats['held']} |",
                 f"| Not eligible (at intake {intake.get('ineligible', 0)}, after evidence {stats['ineligible']}) | {intake.get('ineligible', 0) + stats['ineligible']} |",
                 f"| Unchanged on reassessment | {stats['unchanged']} |",
                 f"| Provider requests | {stats['requests']} of {cfg.evidence['max_requests_per_run']} |",
                 "| Model calls / spend | 0 / none (ordinary code) |", ""]
        lines += ["## Publication", ""]
        if publication.get("attempted"):
            s = publication.get("summary", {})
            lines += [f"- Publication `{publication.get('publicationId')}` to `{cfg.target_branch}`: added {s.get('added', [])}, updated {s.get('updated', [])}, "
                      f"skipped {s.get('skipped', [])}.",
                      f"- Live verification: {'OK' if (publication.get('verification') or {}).get('ok') else 'NOT YET (' + str((publication.get('verification') or {}).get('detail')) + ')'}"]
        else:
            lines.append(f"- Nothing was published this run" + (f" ({publication['skipped']})." if publication.get("skipped") else "."))
        for v in stats.get("verifications", []):
            lines.append(f"- Recheck `{v['publicationId']}`: {v['result']}" + (f" ({v.get('detail')})" if v.get("detail") else ""))
        lines += ["", "## Assessed this run", ""]
        assessed = [c for c in st["candidates"].values() if (c.get("lastAssessment") or {}).get("at") == iso_utc(now)]
        for c in sorted(assessed, key=lambda c: c["id"]):
            la = c["lastAssessment"]
            ev = la["evidence"]
            lines.append(f"- `{c['id']}` ({(c['payload'].get('signal') or {}).get('name') or 'unnamed'}): **{la['decision']}** -> {la['outcome']}"
                         f"{' ' + str(la['eventStatus']) + '/' + str(la['lifecycle']) if la.get('eventStatus') else ''}; evidence {ev['status']} "
                         f"({ev.get('retrieval')}, retrieved {cyprus_time(ev.get('retrievedAt'))} Cyprus; provider last modified {ev.get('modified')} UTC; "
                         f"provider alert {ev.get('alertLevel')}); claims: " + ", ".join(f"{k} {v}" for k, v in la["claims"].items())
                         + (f"; changed since the discovery signal: {', '.join(la['changedSinceSignal'])}" if la["changedSinceSignal"] else "")
                         + (f"; reasons: {', '.join(la['reasons'])}" if la["reasons"] else ""))
        lines += (["- none"] if not assessed else [])
        lines += ["", "## Published events under reassessment", ""]
        evs = sorted(st["events"].values(), key=lambda e: e["eventId"])
        live = [e for e in evs if e["state"] == "PUBLISHED"]
        lines += ([f"- `{e['eventId']}` {e.get('eventStatus')} / {e.get('lifecycle')}: next check {cyprus_time(e.get('nextAssessmentAt'))}"
                   + (f"; {e['reassessFailures']} consecutive failed rechecks (last valid content kept)" if e.get("reassessFailures") else "") for e in live]
                  or ["- none yet"])
        done = [e for e in evs if e["state"] != "PUBLISHED"]
        if done:
            lines += ["", "Not under reassessment: " + "; ".join(
                f"`{e['eventId']}` {e['state'].replace('_', ' ').lower()}" + (" (edited by another process: left as edited, decision needed)" if e["state"] == "STOOD_DOWN" else "")
                for e in done)]
        lines += ["", "## Exceptions", ""]
        open_ex = [e for e in st["exceptions"].values() if e["state"] == "HELD"]
        lines += ([f"- `{e['key']}` **{'NEEDS DECISION' if e.get('requiresHuman') else 'retrying automatically'}**: {', '.join(e['reasons'])}. {e['action']}"
                   for e in open_ex] or ["- none open"])
        cands = {}
        for c in st["candidates"].values():
            cands[c["status"]] = cands.get(c["status"], 0) + 1
        lines += ["", "## Candidate ledger", "", ", ".join(f"{k}: {v}" for k, v in sorted(cands.items())) or "empty", ""]
        return "\n".join(lines) + "\n"

    def _summary(self, text):
        if self.env.get("GITHUB_STEP_SUMMARY"):
            with open(self.env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
                fh.write(text)

    # ---- observe -------------------------------------------------------------------------------------------------
    def _observe(self, st, data, now):
        info = self._intake(json.loads(json.dumps(st)), data, now)
        return (f"# {AGENT_NAME} (observe: plans only, writes nothing)\n\nRun at {cyprus_time(now)}. Intake would take {info['new']} new candidates "
                f"(signals: {info['gdacs'].get('snapshot')}; leads stored: {info['leads'].get('items', 0)}).\n")


# ---- entry ---------------------------------------------------------------------------------------------------------
def run_cli(env=None, root=None, out=print, **kw):
    env = os.environ if env is None else env
    root = Path(root or Path(__file__).resolve().parents[2])
    cfg = load_config(env, root)
    clock, signals, http = kw.pop("clock", utc_now), kw.pop("signals", None), kw.pop("http", None)
    if cfg.fixture:
        fx = Path(root, cfg.fixture).resolve()
        if not str(fx).startswith(str(Path(root, "tests", "fixtures", "investigation").resolve())) or not fx.is_file():
            cfg.errors.append("TI_IV_FIXTURE must name a file under tests/fixtures/investigation/")
        else:
            doc = json.loads(fx.read_text(encoding="utf-8"))
            fixed = parse_dt(doc["now"])
            clock = lambda: fixed                                                       # noqa: E731
            signals, http = doc["signals"], FixtureNet(doc)
            if cfg.target_branch == "main":
                cfg.errors.append("a fixture rehearsal needs an isolated target branch")
    backend = make_backend(env, root, cfg, create=cfg.mode in ("draft", "publish") and not cfg.errors)
    if backend is None and not cfg.errors and cfg.mode != "off":
        cfg.errors.append("no ledger backend: set operation.state_branch")
    eng = Engine(cfg, root, backend, clock=clock, env=env, out=out, signals=signals, http=http, **kw)
    code = eng.run()
    _write_agent_outcome(env, eng, code)
    return code


def _write_agent_outcome(env, eng, code):
    try:
        from . import agent_outcome
        agent_outcome.write(env, AGENT_NAME, {"component": "investigation_verification", "exit_code": code, "mode": eng.cfg.mode,
                                              "trial": eng.cfg.trial, "config_errors": eng.cfg.errors, "facts": getattr(eng, "facts", {})})
    except Exception:
        pass
