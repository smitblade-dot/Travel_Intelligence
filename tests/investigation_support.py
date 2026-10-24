"""Shared offline helpers for the Investigation and Verification agent tests.

Everything here is a MOCK or a recorded fixture: GDACS answers are copies of items recorded from the committed discovery
snapshot (``tests/fixtures/investigation``), the public site is a fake that serves the remote's main branch, and no test
measures model quality. Nothing touches the network."""
import copy
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from country_support import FakeSite                                        # noqa: E402
from ti_ops_director.feed_health import HttpResult                         # noqa: E402
from ti_ops_director.investigation_config import load_config               # noqa: E402
from ti_ops_director.investigation_engine import Engine, empty_state       # noqa: E402
from ti_ops_director.investigation_publish import make_main_backend        # noqa: E402
from ti_ops_director.monitor_state import FileStateBackend                 # noqa: E402

FX = ROOT / "tests" / "fixtures" / "investigation"
NOW = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)
RECORDED = json.loads((FX / "gdacs_recorded_items.json").read_text(encoding="utf-8"))
CHAMPI = RECORDED["champi_tc"]


def feature(eid=1001338, etype="TC", **props):
    """A GDACS feature: the recorded CHAMPI-26 item with property overrides (dotted keys reach into nested dicts)."""
    f = copy.deepcopy(CHAMPI)
    p = f["properties"]
    p["eventtype"], p["eventid"] = etype, eid
    for k, v in props.items():
        if k == "severity":
            p["severitydata"]["severity"] = v
        elif k == "severitytext":
            p["severitydata"]["severitytext"] = v
        else:
            p[k] = v
    return f


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S")


def fresh_feature(now=NOW, eid=1001338, **props):
    """A currently active CHAMPI-like cyclone relative to ``now`` (started 2 days ago, updated an hour ago)."""
    base = dict(fromdate=iso(now - timedelta(days=2)), todate=iso(now - timedelta(hours=1)), datemodified=iso(now - timedelta(hours=1)),
                iscurrent="true")
    base.update(props)
    return feature(eid, **base)


def signals_doc(features, generated=None):
    return {"schemaVersion": "1.0", "generated": (generated or NOW - timedelta(hours=1)).isoformat(),
            "sources": {"gdacs-alerts": {"ok": True, "status": "healthy", "data": {"type": "FeatureCollection", "features": features}}}}


def base_data():
    real = json.loads((ROOT / "data.json").read_text(encoding="utf-8"))
    gd = next(s for s in real["sources"] if s["id"] == "gdacs-ecjrc")
    return {"meta": {"schemaVersion": "2.0", "counts": {}, "generated": "2026-10-21T21:00:00+00:00"}, "countries": real["countries"],
            "records": [], "sources": [gd], "locations": [], "events": [], "sourceObservations": [], "change_log": [], "oilGasSummary": {}}


class FakeGdacs:
    """GDACS stand-in. ``events`` maps 'TC:id' -> feature (or a bytes body). ``fail`` maps 'TC:id' -> HTTP status (0 = network error).
    ``robots`` is the robots.txt text (None = 404)."""
    def __init__(self, events=None, fail=None, robots=None, raw=None):
        self.events, self.fail, self.robots, self.raw, self.calls = events or {}, fail or {}, robots, raw or {}, []

    def __call__(self, url, **kw):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return HttpResult(200, self.robots.encode(), None) if self.robots is not None else HttpResult(404, b"", "http_error")
        import re
        m = re.search(r"eventtype=([A-Z]{2}).*eventid=(\d+)", url)
        key = f"{m.group(1)}:{m.group(2)}" if m else None
        if key in self.fail:
            code = self.fail[key]
            return HttpResult(code or None, b"", "http_error" if code else "network_error:Timeout")
        if key in self.raw:
            return HttpResult(200, self.raw[key], None)
        if key in self.events:
            return HttpResult(200, json.dumps(self.events[key]).encode(), None)
        return HttpResult(404, b"", "http_error")

    def evidence_calls(self):
        return [c for c in self.calls if "geteventdata" in c]


class Harness:
    """A real bare git remote standing in for main, a work clone (the Actions checkout), a file ledger and a fake public site."""
    ENV = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main", "TI_IV_STATE_BACKEND": "file"}

    def __init__(self, tmp, data=None, signals=None, site_mode="live", lag=0, monitor_state=None):
        self.tmp = Path(tmp)
        self.bare, self.work = self.tmp / "remote.git", self.tmp / "work"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.bare)], capture_output=True, check=True)
        subprocess.run(["git", "clone", str(self.bare), str(self.work)], capture_output=True, check=True)
        shutil.copytree(ROOT / "config", self.work / "config")
        (self.work / "data.json").write_text(json.dumps(data or base_data()), encoding="utf-8")
        self.signals = signals if signals is not None else signals_doc([fresh_feature()])
        self.monitor = monitor_state if monitor_state is not None else {}
        self.git(self.work, "add", "-A")
        self.git(self.work, "commit", "-m", "main")
        self.git(self.work, "push", "origin", "HEAD:main")
        self.site = FakeSite(self.bare, site_mode, lag)
        self.state_dir = self.tmp / "ledger"
        self.backend = FileStateBackend({"state": self.state_dir / "investigation_state.json", "report": self.state_dir / "investigation_report.md"})

    def git(self, cwd, *args):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=env, check=True).stdout.strip()

    def config(self, mode="publish", env=None, edit=None):
        e = {"TI_IV_MODE": mode, "TI_IV_STATE_DIR": str(self.state_dir), **self.ENV, **(env or {})}
        cfg = load_config(e, self.work)
        if edit:
            edit(cfg)
        return cfg, e

    def engine(self, net=None, mode="publish", env=None, edit=None, now=None, site=None, backend=None, main_backend=None):
        cfg, e = self.config(mode, env, edit)
        out = []
        eng = Engine(cfg, self.work, backend or self.backend, http=net or FakeGdacs({"TC:1001338": fresh_feature()}), env=e, out=out.append,
                     clock=lambda: (now or NOW), sleep=lambda s: None, data_path=self.work / "data.json", signals=self.signals,
                     monitor_state=self.monitor, site_http=site or self.site, main_backend=main_backend or make_main_backend(self.work))
        eng.captured = out
        return eng

    def main_data(self):
        return json.loads(self.site.show("main"))

    def ledger(self):
        return json.loads(self.backend.snapshot().text("state") or "{}") or empty_state()

    def sync(self):
        # the engine's compare-and-swap reads are shallow fetches, so a plain pull can see divergent history: reset to the remote tip
        self.git(self.work, "fetch", "--quiet", "origin", "main")
        self.git(self.work, "reset", "--quiet", "--hard", "FETCH_HEAD")
