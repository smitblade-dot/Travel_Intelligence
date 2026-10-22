"""Queue worker for 03AA-04_Country_Intelligence_Builder_TI inside the Master Agent (Ops Director).

The Director's queue owns scheduling, leases, deduplication and the operational report. The worker runs one
``country_content_draft`` task through the same engine the GitHub Actions workflow uses, with the file backend
beside the queue database. It reads only the allow-listed source hosts, never writes data.json, never
publishes, and uses the builder's own spending limits (not the Director's).
"""
from __future__ import annotations

import os
from datetime import timezone
from pathlib import Path
from urllib.parse import urlsplit

from .country_config import load_builder_config
from .country_engine import Builder, LocalBackend, _sections, empty_state
from .country_evidence import http_get, resolve_url
from .country_gaps import TASK_TYPE, recheck_bucket, task_for
from .workers import CapabilityError, WorkResult, Worker

EXC_TASK_TYPE = "country_publication_exception"


class CountryContentWorker(Worker):
    name = "country_content_builder"
    task_types = (TASK_TYPE, EXC_TASK_TYPE)
    capabilities = frozenset({"network_read"})
    description = ("Country content builder: finds a missing or stale country/category, collects permitted evidence and "
                   "writes a source-checked DRAFT (never published) (03AA-04_Country_Intelligence_Builder_TI).")
    transport = staticmethod(http_get)       # replaced in tests

    def hosts_for(self, inputs, cfg):
        bc = load_builder_config({}, cfg.repo_root)
        cc, cat = inputs.get("countryId"), inputs.get("category")
        params = bc.raw["country_params"].get(cc)
        if not params or cat not in bc.category_defs:
            return frozenset()
        hosts = {urlsplit(resolve_url(bc.sources[k], params, cc)).hostname for k in bc.category_defs[cat]["sources"]}
        return frozenset(hosts)

    def _guarded(self, ctx):
        def http(url, **kw):
            if "network_read" not in self.capabilities:
                raise CapabilityError(f"{self.name} lacks network_read")
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.hostname not in ctx.allowed_hosts:
                raise CapabilityError("host not in this task's allowed hosts")
            return self.transport(url, **kw)
        return http

    def run(self, task, ctx):
        env = dict(os.environ)
        env.setdefault("TI_CB_STATE_BACKEND", "file")
        env.setdefault("TI_CB_STATE_DIR", str(Path(ctx.cfg.db_path).parent / "country_builder"))
        cfg = load_builder_config(env, ctx.cfg.repo_root)
        if cfg.errors:
            return WorkResult("blocked", blocking_reason="configuration: " + "; ".join(cfg.errors), requires_human=True)
        if cfg.mode != "draft":
            return WorkResult("blocked", blocking_reason=f"mode_{cfg.mode}: set operation.mode to draft in "
                              "config/country_builder.json to let the builder write drafts", requires_human=True)
        inputs = task["inputs"]
        now = ctx.store.clock().astimezone(timezone.utc)
        backend = LocalBackend(env["TI_CB_STATE_DIR"])
        builder = Builder(cfg, ctx.cfg.repo_root, backend, http=self._guarded(ctx), env=env, out=lambda *_: None, clock=ctx.store.clock)
        planned = [task_for({k: inputs[k] for k in ("countryId", "country", "category", "gap", "existingRecordIds")},
                            now.date(), cfg.raw["schedule"]["recheck_after_days"]) | {"gap": inputs}]
        planned[0]["inputs"] = {k: inputs[k] for k in ("countryId", "country", "category", "gap", "existingRecordIds")}
        code = builder._draft_run(_sections(backend.snapshot().state() or empty_state()), builder.load_data(), planned, now,
                                  {"coverage": {"covered": 0, "combinations": 0, "missing": 0}, "gaps": []})
        if code != 0:
            return WorkResult("failed", error=f"builder exit code {code}")
        ledger = backend.snapshot().state()
        t = ledger["tasks"].get(planned[0]["dedupe_key"], {})
        routed = mirror_exceptions(ctx.store, ledger.get("exceptions", {}))
        out = {"ledgerTask": t.get("id"), "ledgerState": t.get("state"), "draftId": t.get("draftId"), "reason": t.get("outcome"),
               "costUsd": t.get("cost_usd", 0), "published": t.get("published", []), "held": t.get("held", []), "exceptionsRouted": routed}
        if t.get("state") == "completed":
            return WorkResult("completed", outputs=out)
        if t.get("state") in ("blocked", "pending"):
            return WorkResult("blocked", outputs=out, blocking_reason=t.get("blockingReason") or t.get("error") or "pending",
                              requires_human=(t.get("blockingReason") or "").startswith("awaiting_paid"))
        return WorkResult("failed", outputs=out, error=t.get("error") or "failed")


def mirror_exceptions(store, exceptions):
    """Route the builder's held exceptions through the Master Agent: one blocked task per open exception (deduplicated by
    key), cancelled when the exception resolves. Returns {'opened': n, 'resolved': n}."""
    from . import BUSINESS_APPLICATION, SUBSTREAM
    opened = resolved = 0
    for e in exceptions.values():
        key = f"{BUSINESS_APPLICATION}|{EXC_TASK_TYPE}|{e['key']}||"
        active = store.query("SELECT id FROM tasks WHERE dedupe_key=? AND state IN ('pending','running','blocked','completed')", (key,))
        if e["state"] == "HELD":
            if active:
                continue
            tid, created = store.create_task(worker="country_content_builder", task_type=EXC_TASK_TYPE, inputs={k: e.get(k) for k in (
                "countryId", "category", "topic", "reasons", "detail", "scope")}, dedupe_key=key, business_application=BUSINESS_APPLICATION,
                substream=SUBSTREAM, priority=60, max_attempts=1)
            if created:
                store.set_blocked(tid, e["action"][:300], requires_human=bool(e.get("requiresHuman")))
                opened += 1
        elif active:
            store.cancel(active[0]["id"], "exception resolved by a later run")
            resolved += 1
    return {"opened": opened, "resolved": resolved}


def enqueue_gaps(ctl, now=None):
    """Queue responsibility: put the configured gaps into the Master Agent queue (deduplicated per recheck window)."""
    bc = load_builder_config({}, ctl.cfg.repo_root)
    if bc.errors or bc.mode == "off":
        return {"skipped": "builder_off_or_misconfigured"}
    b = Builder(bc, ctl.cfg.repo_root, None, env={})
    now = now or ctl.store.clock()
    gaps, planned = b.plan(b.load_data(), now.date())
    made = []
    for t in planned[: bc.tasks_per_run]:
        i = t["inputs"]
        tid, created = ctl.enqueue(task_type=TASK_TYPE, inputs=i, dedupe_subject=f"{i['countryId']}:{i['category']}:{i['gap']['type']}",
                                   time_bucket=recheck_bucket(now.date(), bc.raw["schedule"]["recheck_after_days"]), priority=40)
        made.append({"task": tid, "created": created})
    return {"gaps": len(gaps), "tasks": made}
