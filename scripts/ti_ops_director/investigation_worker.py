"""Queue worker for 03AA-04_Investigation_Verification_TI inside the Master Agent (Ops Director).

The Director's queue is local-only and is not serviced on GitHub Actions (docs/TI_OPS_DIRECTOR.md), so this worker is the
way the agent runs when a host does service the queue (the NUC daemon, not deployed). It runs one ``investigation`` task as a
pass of the same engine the Actions workflow runs, in **draft** mode with a file ledger beside the queue database: it
retrieves permitted evidence and assesses, but never publishes (publication happens only in the guarded Actions run). Held
exceptions are mirrored into the queue as one deduplicated blocked task each, cancelled when a later run resolves them.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from .investigation_config import AGENT_NAME, WORKER_NAME, load_config
from .investigation_engine import Engine, IV_PATHS, sections
from .investigation_sources import make_transport
from .monitor_state import FileStateBackend
from .workers import CapabilityError, WorkResult, Worker

TASK_TYPE = "investigation"
EXC_TASK_TYPE = "investigation_exception"


class InvestigationVerificationWorker(Worker):
    name = WORKER_NAME
    task_types = (TASK_TYPE, EXC_TASK_TYPE)
    capabilities = frozenset({"network_read"})
    description = ("Investigation and verification: takes discovery candidates, retrieves bounded evidence from register-backed sources, assesses "
                   "each claim, and records what passes or holds the rest as routed exceptions; publication to main happens only in the guarded "
                   "scheduled run (03AA-04_Investigation_Verification_TI).")
    transport = staticmethod(lambda hosts: make_transport(hosts))      # replaced in tests

    def hosts_for(self, inputs, cfg):
        c = load_config({}, cfg.repo_root)
        return frozenset(h for s in c.sources.values() for h in s["hosts"])

    def _guarded(self, ctx):
        raw = self.transport(ctx.allowed_hosts)

        def http(url, **kw):
            if "network_read" not in self.capabilities:
                raise CapabilityError(f"{self.name} lacks network_read")
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.hostname not in ctx.allowed_hosts:
                raise CapabilityError("host not in this task's allowed hosts")
            return raw(url, **kw)
        return http

    def run(self, task, ctx):
        if task["task_type"] == EXC_TASK_TYPE:
            return WorkResult("blocked", blocking_reason="exception_awaiting_decision", requires_human=True)
        base = Path(ctx.cfg.db_path).parent / "investigation"
        env = dict(os.environ, TI_IV_STATE_BACKEND="file", TI_IV_STATE_DIR=str(base), TI_IV_MODE="draft")
        cfg = load_config(env, ctx.cfg.repo_root)
        if cfg.errors:
            return WorkResult("blocked", blocking_reason="configuration: " + "; ".join(cfg.errors), requires_human=True)
        probe = load_config({}, ctx.cfg.repo_root)
        if probe.mode == "off":
            return WorkResult("blocked", blocking_reason="mode_off: operation.mode is off in config/investigation_verification.json", requires_human=True)
        backend = FileStateBackend({k: base / v for k, v in IV_PATHS.items()})
        lines = []
        eng = Engine(cfg, ctx.cfg.repo_root, backend, http=self._guarded(ctx), clock=ctx.store.clock, env=env, out=lines.append)
        code = eng.run()
        ledger = sections(backend.snapshot().state() or {})
        routed = mirror_exceptions(ctx.store, ledger["exceptions"])
        out = {"exit": code, "stats": {k: v for k, v in eng.stats.items() if k in ("tasks", "assessed", "ready", "held", "ineligible", "requests")},
               "exceptionsRouted": routed, "published": 0, "note": "draft mode: this worker never publishes"}
        return WorkResult("completed" if code == 0 else "failed", outputs=out, error=None if code == 0 else f"engine exit code {code}")


def mirror_exceptions(store, exceptions):
    """One blocked, deduplicated queue task per open exception; cancelled when it resolves. -> {'opened': n, 'resolved': n}."""
    from . import BUSINESS_APPLICATION, SUBSTREAM
    opened = resolved = 0
    for e in exceptions.values():
        key = f"{BUSINESS_APPLICATION}|{EXC_TASK_TYPE}|{e['key']}||"
        active = store.query("SELECT id FROM tasks WHERE dedupe_key=? AND state IN ('pending','running','blocked','completed')", (key,))
        if e["state"] == "HELD":
            if active:
                continue
            tid, created = store.create_task(worker=WORKER_NAME, task_type=EXC_TASK_TYPE,
                                             inputs={k: e.get(k) for k in ("candidateId", "class", "reasons", "detail")}, dedupe_key=key,
                                             business_application=BUSINESS_APPLICATION, substream=SUBSTREAM, priority=60, max_attempts=1)
            if created:
                store.set_blocked(tid, e["action"][:300], requires_human=bool(e.get("requiresHuman")))
                opened += 1
        elif active:
            store.cancel(active[0]["id"], "exception resolved by a later run")
            resolved += 1
    return {"opened": opened, "resolved": resolved}
