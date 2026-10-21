"""The Master Agent controller: ordinary code, persistent queue, bounded concurrency, no model loop."""
from __future__ import annotations

import json
import os
import socket
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from . import BUSINESS_APPLICATION, SUBSTREAM
from .budget import Budget, BudgetBlocked, price_for
from .feed_health import allowed_hosts, http_get as live_http_get
from .models import ModelGateway
from .sanitise import clean_text
from .scheduling import interval_slot, last_daily_slot
from .store import Store, fingerprint
from .workers import CapabilityError, Registry, WorkContext, default_registry

SCHEDULER_LOCK = "ops_director_scheduler"


class Controller:
    def __init__(self, cfg, store: Store, registry: Registry = None, provider=None, http=None, owner=None):
        self.cfg, self.store = cfg, store
        self.registry = registry or default_registry()
        self.gateway = ModelGateway(store, cfg, provider)
        self.budget = Budget(store, cfg)
        self.http = http
        self.owner = owner or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"

    # ---- enqueue -----------------------------------------------------------
    def enqueue(self, *, task_type, inputs, dedupe_subject, priority=50, depends_on=(), mode="none", paid=False,
                time_bucket=None):
        worker = self.registry.for_type(task_type)
        fp = fingerprint(inputs)
        key = "|".join([BUSINESS_APPLICATION, task_type, dedupe_subject, fp, str(time_bucket or "")])
        return self.store.create_task(
            worker=worker.name if worker else "unregistered", task_type=task_type, inputs=inputs,
            dedupe_key=key, business_application=BUSINESS_APPLICATION, substream=SUBSTREAM, priority=priority,
            depends_on=depends_on, mode=mode, paid=paid, max_attempts=self.cfg.max_attempts, input_fingerprint=fp)

    # ---- control -----------------------------------------------------------
    def pause(self):
        self.store.set_control("paused", "1")

    def resume(self):
        self.store.set_control("paused", "0")
        self.store.set_control("stop", "0")

    def stop(self):
        """Stop: no new tasks start and running workers are asked to stop at their next check."""
        self.store.set_control("stop", "1")
        self.store.set_control("paused", "1")

    def is_paused(self):
        return self.store.get_control("paused", "0") == "1"

    # ---- selection ---------------------------------------------------------
    def _reevaluate_blocked(self):
        for t in self.store.list_tasks(("blocked",)):
            reason = t["blocking_reason"] or ""
            w = self.registry.get(t["worker"])
            if reason.startswith("worker_not_implemented") and w and w.implemented:
                self.store.unblock(t["id"])
            elif reason.startswith("budget:") and self._budget_ok(t) is None:
                self.store.unblock(t["id"])

    def _budget_ok(self, task):
        """None if eligible, else a blocking reason. Deterministic tasks are never budget-blocked."""
        if not task["paid"] or self.cfg.dry_run:
            return None
        if not self.cfg.paid_allowance_configured:
            return "budget:allowance_not_configured"
        try:
            price_for(self.cfg, "haiku")
        except BudgetBlocked as exc:
            return "budget:" + exc.reason
        if self.budget.summary()["today_utc"]["remaining_usd"] <= 0:
            return "budget:daily_allowance_exhausted"
        return None

    def eligible(self):
        """Pending tasks that may start now, with blocking decisions applied. Priority order, then age."""
        self._reevaluate_blocked()
        out = []
        by_id = {t["id"]: t for t in self.store.list_tasks()}
        for t in self.store.list_tasks(("pending",)):
            w = self.registry.get(t["worker"])
            if w is None or not w.implemented:
                self.store.set_blocked(t["id"], f"worker_not_implemented:{t['worker']}")
                continue
            deps = json.loads(t["depends_on"])
            bad = [d for d in deps if by_id.get(d, {}).get("state") in ("failed", "cancelled", None)]
            if bad:
                self.store.set_blocked(t["id"], f"dependency_failed:{bad[0]}")
                continue
            if any(by_id[d]["state"] != "completed" for d in deps):
                continue
            reason = self._budget_ok(t)
            if reason:
                self.store.set_blocked(t["id"], reason, requires_human=reason.endswith("not_configured"))
                continue
            out.append(t)
        return out

    # ---- execution ---------------------------------------------------------
    def tick(self):
        """One bounded orchestration pass. Safe to call from several processes: one scheduler lease only."""
        summary = {"started": [], "recovered": [], "skipped": None}
        if self.store.get_control("stop", "0") == "1":
            summary["skipped"] = "stopped"
            return summary
        if not self.store.acquire_lock(SCHEDULER_LOCK, self.owner, self.cfg.lease_seconds):
            summary["skipped"] = "another_scheduler_active"
            return summary
        try:
            summary["recovered"] = self.store.recover_expired()
            if self.is_paused():
                summary["skipped"] = "paused"
                return summary
            batch = self.eligible()[: self.cfg.max_concurrency]
            claimed = [t for t in batch if self.store.claim(t["id"], self.owner, self.cfg.lease_seconds)]
            with ThreadPoolExecutor(max_workers=max(1, self.cfg.max_concurrency)) as pool:
                for t, _ in zip(claimed, pool.map(self._execute, claimed)):
                    summary["started"].append(t["id"])
        finally:
            self.store.release_lock(SCHEDULER_LOCK, self.owner)
        return summary

    def _execute(self, task):
        task = self.store.get(task["id"])
        worker = self.registry.get(task["worker"])
        inputs = json.loads(task["inputs"])
        task["inputs"] = inputs
        hosts = allowed_hosts(inputs["source"]) if "source" in inputs else frozenset()
        ctx = WorkContext(self.cfg, self.store, self.gateway, worker, task["id"], self.owner,
                          http=self.http or live_http_get, allowed_hosts=hosts)
        try:
            if ctx.should_stop():
                self.store.finish(task["id"], self.owner, "pending", blocking_reason="stopped_by_operator")
                return
            res = worker.run(task, ctx)
            self.store.finish(task["id"], self.owner, res.state, outputs=res.outputs, evidence=res.evidence,
                              blocking_reason=res.blocking_reason, requires_human=res.requires_human, error=res.error)
        except CapabilityError as exc:
            self.store.finish(task["id"], self.owner, "failed", error="capability_denied: " + clean_text(exc, 200))
        except Exception as exc:   # bounded attempts, never an unbounded retry loop
            retry = task["attempts"] < task["max_attempts"]
            self.store.finish(task["id"], self.owner, "pending" if retry else "failed",
                              error=f"{type(exc).__name__}: {clean_text(exc, 200)}")

    # ---- scheduled responsibilities ---------------------------------------
    def run_due(self, snapshot_dir=None, mode="live", fixtures=None, env_override=None):
        """Fire scheduled responsibilities once each; overlapping or already-done slots are skipped."""
        from .feed_scan import scan_feed_health
        now = self.store.clock()
        done = {}
        slot = interval_slot(now, self.cfg.monitor_interval_minutes)
        last = self.store.get_meta("last_slot:feed_health_scan")
        if last != slot.isoformat():
            if self.store.acquire_lock("responsibility:feed_health_scan", self.owner, 600):
                try:
                    done["feed_health_scan"] = scan_feed_health(self, mode=mode, root=snapshot_dir, fixtures=fixtures,
                                                       env_override=env_override)
                    self.store.set_meta("last_slot:feed_health_scan", slot.isoformat())
                    self.store.record_run("feed_health_scan", True, done["feed_health_scan"])
                finally:
                    self.store.release_lock("responsibility:feed_health_scan", self.owner)
            else:
                done["feed_health_scan"] = "overlap_skipped"
        dslot = last_daily_slot(now, self.cfg.daily_consolidation_local, self.cfg.timezone)
        if self.store.get_meta("last_slot:daily_consolidation") != dslot.isoformat():
            tid, created = self.enqueue(task_type="daily_consolidation", inputs={"slot_utc": dslot.isoformat()},
                                        dedupe_subject=dslot.isoformat(), priority=20)
            self.store.set_meta("last_slot:daily_consolidation", dslot.isoformat())
            done["daily_consolidation"] = {"task": tid, "created": created}
        return done
