#!/usr/bin/env python3
"""CLI for 03AA-04_Investigation_Verification_TI.

  run               one pass in the configured mode (observe, draft or publish); the workflow calls this
  plan              show what intake would take in right now (read-only, no ledger write)
  status            ledger summary from the state backend
  pause / resume / stop   operator controls stored in the ledger (a paused or stopped agent processes nothing)
  route-exceptions  mirror the ledger's held exceptions into the Master Agent queue (TI_OPS_DB_PATH)
  rollback          <publicationId> --by NAME: remove/restore exactly what that automatic publication changed (compare-and-swap)

Only `run` in publish mode from a scheduled or manual run on main (and `rollback`) writes data.json, and only through the
compare-and-swap path with canonical validation. This agent makes no model call and has no spending switch to change.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ti_ops_director.investigation_config import load_config                          # noqa: E402
from ti_ops_director.investigation_engine import (EXIT_CONFIG, EXIT_OK, EXIT_STATE, Engine, cyprus_time, empty_state,   # noqa: E402
                                                  make_backend, run_cli, sections)
from ti_ops_director.monitor_state import StateSyncError                              # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None, env=None):
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run", "plan", "status", "pause", "resume", "stop", "route-exceptions", "rollback"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--by")
    a = ap.parse_args(argv)
    if a.command == "run":
        return run_cli(env, ROOT)
    cfg = load_config(env, ROOT)
    if cfg.errors:
        print("ERROR: " + "; ".join(cfg.errors))
        return EXIT_CONFIG
    backend = make_backend(env, ROOT, cfg, create=a.command in ("pause", "resume", "stop", "rollback"))
    if backend is None:
        print("ERROR: no state backend (set operation.state_branch or TI_IV_STATE_BACKEND=file)")
        return EXIT_CONFIG
    try:
        if a.command == "plan":
            cfg.mode = "observe"
            return Engine(cfg, ROOT, backend, env=env).run()
        if a.command == "status":
            st = sections(backend.snapshot().state() or empty_state())
            print(json.dumps({"controls": st["controls"], "lease": st["lease"],
                              "candidates": {k: sum(1 for c in st["candidates"].values() if c["status"] == k) for k in sorted({c["status"] for c in st["candidates"].values()})},
                              "events": {k: {"state": v["state"], "status": v.get("eventStatus"), "lifecycle": v.get("lifecycle"),
                                             "nextCheckCyprus": cyprus_time(v.get("nextAssessmentAt"))} for k, v in st["events"].items()},
                              "openExceptions": [{"key": e["key"], "class": e["class"], "reasons": e["reasons"]} for e in st["exceptions"].values() if e["state"] == "HELD"],
                              "lastRuns": st["runs"][-3:]}, indent=2))
            return EXIT_OK
        if a.command == "route-exceptions":
            from ti_ops_director.config import load_config as load_ops
            from ti_ops_director.investigation_worker import mirror_exceptions
            from ti_ops_director.store import Store
            st = sections(backend.snapshot().state() or empty_state())
            print(json.dumps(mirror_exceptions(Store(load_ops().db_path), st["exceptions"])))
            return EXIT_OK
        if a.command in ("pause", "resume", "stop"):
            print(f"{a.command}: {Engine(cfg, ROOT, backend, env=env).set_controls(a.command)}")
            return EXIT_OK
        if a.command == "rollback":
            if not a.arg or not a.by:
                print("rollback needs: <publicationId> --by NAME")
                return EXIT_CONFIG
            print(json.dumps(Engine(cfg, ROOT, backend, env=env).manual_rollback(a.arg, a.by)))
            return EXIT_OK
    except StateSyncError as exc:
        print(f"ERROR: {exc}")
        return EXIT_STATE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
