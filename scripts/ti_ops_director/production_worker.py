"""Queue worker for the production controller (03AA-04_Production_Controller_TI) inside the Ops Director.

The same checks as the GitHub Actions workflow, run from the Director's queue on one host. Local state is the
file backend beside the queue database; every open exception becomes one blocked queue task. The queue worker
never dispatches a workflow: recovery dispatch exists only on Actions, behind Marc's switch.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from .monitor_state import dump_state
from .production import (GitHubAPI, apply, http_request, load_pc_config, make_pc_backend, render_report,
                         run_checks, sync_queue, verdict)
from .workers import CapabilityError, WorkResult, Worker


class ProductionVerificationWorker(Worker):
    name = "production_verification"
    task_types = ("production_verification",)
    capabilities = frozenset({"network_read"})
    description = ("Production verification: delayed, missed and failed workflow runs, output validity, freshness "
                   "and completeness, and the public release (03AA-04_Production_Controller_TI).")
    transport = staticmethod(http_request)    # replaced in tests

    def hosts_for(self, inputs, cfg):
        pc = load_pc_config({}, cfg.repo_root)
        return frozenset({"api.github.com", urlsplit(pc.site_url).hostname})

    def _guarded(self, ctx):
        def http(url, **kw):
            if "network_read" not in self.capabilities:
                raise CapabilityError(f"{self.name} lacks network_read")
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.hostname not in ctx.allowed_hosts:
                raise CapabilityError("host not in this task's allowed hosts")
            if kw.get("method", "GET") != "GET":
                raise CapabilityError("the queue worker is read-only")
            return self.transport(url, **kw)
        return http

    def run(self, task, ctx):
        env = dict(os.environ, TI_PC_MODE="active", TI_PC_STATE_BACKEND="file", TI_PC_RECOVERY_WORKFLOWS="",
                   TI_PC_STATE_DIR=os.environ.get("TI_PC_STATE_DIR")
                   or str(Path(ctx.cfg.db_path).parent / "production_controller"))
        cfg = load_pc_config(env, ctx.cfg.repo_root)
        if cfg.errors:
            return WorkResult("blocked", blocking_reason="configuration: " + "; ".join(cfg.errors), requires_human=True)
        http = self._guarded(ctx)
        api = GitHubAPI(cfg.repository, env.get("GITHUB_TOKEN"), http)
        backend = make_pc_backend(env, ctx.cfg.repo_root, cfg)
        now = ctx.store.clock()
        results = run_checks(cfg, api, ctx.cfg.repo_root, now, backend.snapshot().state(), http)

        def fn(snap):
            new_state, delta, planned = apply(snap.state(), results, cfg, now)
            return ({"state": dump_state(new_state),
                     "report": render_report(cfg, results, new_state, delta, planned, now)}, (new_state, delta))
        _, (state, delta) = backend.transact(fn)
        follow_ups = sync_queue(ctx.store, state, delta)
        open_ = [t for t in state["tasks"] if t["state"] == "blocked"]
        return WorkResult("completed", outputs={
            "verdict": verdict(results, state["tasks"]), "open_exceptions": len(open_),
            "critical": sum(t["severity"] == "critical" for t in open_), "opened": delta["opened"],
            "resolved": delta["resolved"], "follow_up_tasks": follow_ups,
            "checks": {r.name: r.status for r in results}},
            evidence=[{"check": r.name, "status": r.status, "facts": r.facts, "detail": r.detail} for r in results])
