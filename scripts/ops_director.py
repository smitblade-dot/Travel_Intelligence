#!/usr/bin/env python3
"""CLI for 03AA-04_Master_Agent_TI_Ops_Director. Dry-run by default; no paid call without an allowance."""
import argparse
import contextlib
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ti_ops_director.config import ROOT, load_config          # noqa: E402
from ti_ops_director.controller import Controller              # noqa: E402
from ti_ops_director.feed_scan import scan_feed_health         # noqa: E402
from ti_ops_director.report import build_report, render_markdown  # noqa: E402
from ti_ops_director.store import Store                        # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config")
    ap.add_argument("command", choices=["init", "scan", "tick", "run-once", "report", "status", "pause", "resume",
                                        "stop", "cancel", "daemon", "selftest", "fx-set", "demo", "healthcheck", "eval-monitor"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--mode", choices=["live", "fixture"], default="live")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--rate"), ap.add_argument("--rate-date"), ap.add_argument("--rate-source")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    store = Store(cfg.db_path)
    ctl = Controller(cfg, store)

    if a.command == "init":
        print(f"Initialised {cfg.db_path} (dry_run={cfg.dry_run})")
    elif a.command == "scan":
        print(json.dumps(scan_feed_health(ctl, mode=a.mode), indent=2))
    elif a.command == "tick":
        print(json.dumps(ctl.tick(), indent=2))
    elif a.command in ("run-once", "demo"):
        fx = None
        if a.mode == "fixture" or a.command == "demo" and a.mode == "fixture":
            fx = json.loads((Path(cfg.repo_root) / "tests/fixtures/ops_director/fixture_responses.json").read_text())
        result = None
        ctl.run_due(mode=a.mode, fixtures=fx, env_override={} if fx else None)
        for _ in range(10):
            t = ctl.tick()
            if not t["started"]:
                break
        rep = build_report(ctl)
        print(json.dumps(rep, indent=2) if a.json else render_markdown(rep))
    elif a.command in ("report", "status"):
        rep = build_report(ctl)
        print(json.dumps(rep, indent=2) if a.json or a.command == "status" else render_markdown(rep))
    elif a.command == "pause":
        ctl.pause(); print("Paused: no new tasks will start.")
    elif a.command == "resume":
        ctl.resume(); print("Resumed.")
    elif a.command == "stop":
        ctl.stop(); print("Stop requested: no new tasks; running workers stop at their next check.")
    elif a.command == "cancel":
        print("cancelled" if store.cancel(a.arg) else "not cancellable")
    elif a.command == "daemon":
        print(f"Daemon: tick every 30s; scan interval {cfg.monitor_interval_minutes} min. Ctrl-C or 'stop' to end.")
        while store.get_control("stop", "0") != "1":
            ctl.run_due(mode=a.mode)
            ctl.tick()
            store.set_meta("heartbeat", store.now())
            time.sleep(30)
    elif a.command == "eval-monitor":
        # Real-model quality check over manually labelled TI cases. Goes through the spending gateway: nothing
        # runs unless an allowance is configured, and the output says whether model quality was measured.
        from ti_ops_director.monitor_bridge import MonitorSpend
        from ti_ops_director.monitor_eval import check_recorded, evaluate_labelled, load_monitor
        doc = json.loads((ROOT / "tests/fixtures/ops_director/monitor_labelled_cases.json").read_text())
        monitor = load_monitor(ROOT)
        with contextlib.redirect_stdout(sys.stderr):        # guard log lines must not mix with the JSON
            report = {"labelled": evaluate_labelled(doc, MonitorSpend.open({}), monitor),
                      "recorded": check_recorded(doc, monitor)}
        print(json.dumps(report, indent=2))
    elif a.command == "healthcheck":
        from datetime import datetime
        hb = store.get_meta("heartbeat")
        ok = bool(hb) and (store.clock() - datetime.fromisoformat(hb)).total_seconds() < 180
        print("healthy" if ok else "no recent heartbeat"); return 0 if ok else 1
    elif a.command == "selftest":
        p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests"),
                            "-p", "test_ti_ops_director*.py"], capture_output=True, text=True)
        tail = (p.stderr or p.stdout).strip().splitlines()[-3:]
        store.set_meta("last_test_run", {"at_utc": store.now(), "ok": p.returncode == 0, "summary": " | ".join(tail)})
        print("\n".join(tail)); return p.returncode
    elif a.command == "fx-set":
        with store.tx() as db:
            db.execute("INSERT OR REPLACE INTO fx_rates VALUES(?,?,?,?,?)",
                       (a.rate_date, "USD", "EUR", float(a.rate), a.rate_source))
        print("Stored dated USD->EUR rate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
