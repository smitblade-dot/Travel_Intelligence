"""Shared helpers for TI Ops Director tests. All data here is FIXTURE data, never live evidence."""
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ti_ops_director.config import Config                      # noqa: E402
from ti_ops_director.controller import Controller              # noqa: E402
from ti_ops_director.store import Store                        # noqa: E402
from ti_ops_director.workers import PlannedWorker, Registry, WorkResult, Worker, default_registry  # noqa: E402

PRICING = {
    "claude-haiku-5-5": {"input_per_mtok": 0.10, "output_per_mtok": 0.50, "verified_at": "2026-10-21",
              "source_url": "https://platform.claude.com/docs/en/about-claude/pricing"},
    "claude-sonnet-5-5": {"input_per_mtok": 2.0, "output_per_mtok": 10.0, "verified_at": "2026-10-21",
               "source_url": "https://platform.claude.com/docs/en/about-claude/pricing"},
    "claude-opus-5-5": {"input_per_mtok": 4.0, "output_per_mtok": 20.0, "verified_at": "2026-10-21",
             "source_url": "https://platform.claude.com/docs/en/about-claude/pricing"},
}


class Clock:
    def __init__(self, start="2026-10-21T07:00:00+00:00"):
        self.t = datetime.fromisoformat(start)

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


def make_cfg(tmp, **kw):
    cfg = Config(db_path=str(Path(tmp) / "ops.sqlite3"))
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def paid_cfg(tmp, **kw):
    d = dict(dry_run=False, per_task_limit_usd=1.0, daily_limit_usd=5.0, monthly_limit_usd=50.0, pricing=PRICING)
    d.update(kw)
    return make_cfg(tmp, **d)


def make_controller(tmp, cfg=None, clock=None, **kw):
    cfg = cfg or make_cfg(tmp)
    clock = clock or Clock()
    store = Store(cfg.db_path, clock=clock)
    return Controller(cfg, store, **kw), clock


def make_root(tmp, snapshot):
    """A temp repository root holding the real source config and a FIXTURE snapshot."""
    root = Path(tmp) / "repo"
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    shutil.copy(ROOT / "config/phase1_sources.json", root / "config/phase1_sources.json")
    (root / "data/phase1_signals.json").write_text(json.dumps(snapshot))
    return root


def snapshot(generated="2026-10-21T05:00:00+00:00", **overrides):
    sources = {
        "ourairports-airports": {"ok": True, "status": "healthy", "fetchedAt": generated, "count": 100},
        "gdelt-events": {"ok": False, "status": "unavailable", "error": "HTTP 429"},
        "gdacs-alerts": {"ok": True, "status": "healthy"},
        "adsblol-aircraft": {"ok": True, "status": "degraded", "count": 5},
        "reliefweb-disasters": {"ok": False, "status": "blocked", "error": "RELIEFWEB_APPNAME is not configured"},
        "nasa-firms": {"ok": True, "status": "healthy", "count": 10},
    }
    sources.update(overrides)
    return {"generated": generated, "sources": sources}


class CountingHttp:
    def __init__(self, responder):
        self.responder, self.calls = responder, []

    def __call__(self, url, **kw):
        self.calls.append(url)
        return self.responder(url)


class RecordingWorker(Worker):
    name = "recording"
    task_types = ("recording",)
    implemented = True

    def __init__(self, log=None, fail=False, delay=0.0):
        self.log, self.fail, self.delay = log if log is not None else [], fail, delay

    def run(self, task, ctx):
        import time
        time.sleep(self.delay)
        self.log.append(task["id"])
        if self.fail:
            raise RuntimeError("boom")
        return WorkResult("completed", {"ok": True})
