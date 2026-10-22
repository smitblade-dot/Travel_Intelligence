#!/usr/bin/env python3
"""CLI for 03AA-04_Country_Intelligence_Builder_TI.

  run        one pass in the configured mode (observe, draft or publish); the workflow calls this
  plan       show the gaps and the tasks they would create (read-only); --queue adds them to the Master Agent queue
  status     ledger summary from the state backend
  pause / resume / stop   operator controls stored in the ledger (draft mode backend)
  review     record an operator note/decision on a draft or held item (never publishes or unpublishes)
  route-exceptions  mirror the ledger's held exceptions into the Master Agent queue (TI_OPS_DB_PATH)
  rollback   <publicationId>: remove/restore exactly what that automatic publication changed (main, compare-and-swap)
  eval       quality evaluation against manually assessed cases: --approval FILE for the one capped live run;
             without it, scripted replies check the scorer only and Haiku quality is reported as NOT MEASURED

Only `run` in publish mode from a scheduled or manual run on main (and `rollback`) writes data.json, and only through the
compare-and-swap path with canonical validation. Paid model use needs TI_CB_DRY_RUN=false and all three spending limits.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ti_ops_director.country_config import load_builder_config                      # noqa: E402
from ti_ops_director.country_engine import (EXIT_CONFIG, EXIT_OK, EXIT_STATE, Builder, _sections, empty_state,  # noqa: E402
                                            make_backend, run_cli)
from ti_ops_director.monitor_state import StateSyncError, dump_state                # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _backend(env, cfg, create):
    b = make_backend(env, ROOT, cfg, create)
    if b is None:
        print("ERROR: no state backend (set TI_CB_STATE_BRANCH, or TI_CB_STATE_BACKEND=file)")
        raise SystemExit(EXIT_CONFIG)
    return b


def main(argv=None, env=None):
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run", "plan", "status", "pause", "resume", "stop", "review", "eval", "route-exceptions", "rollback"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--queue", action="store_true", help="plan: add the tasks to the Master Agent queue (TI_OPS_DB_PATH)")
    ap.add_argument("--approval", help="eval: written approval file for one bounded live Haiku evaluation")
    ap.add_argument("--decision", choices=["accepted", "rejected", "needs_changes"])
    ap.add_argument("--by")
    ap.add_argument("--note", default="")
    a = ap.parse_args(argv)
    if a.command == "run":
        return run_cli(env, ROOT)
    cfg = load_builder_config(env, ROOT)
    if cfg.errors:
        print("ERROR: " + "; ".join(cfg.errors))
        return EXIT_CONFIG
    if a.command == "plan":
        b = Builder(cfg, ROOT, None, env=env)
        gaps, planned = b.plan(b.load_data(), datetime.now(timezone.utc).date())
        print(json.dumps([{"country": g["country"], "category": g["category"], "gap": g["gap"], "dedupe_key": t["dedupe_key"]}
                          for g, t in zip(gaps, planned)], indent=2))
        if a.queue:
            from ti_ops_director.config import load_config
            from ti_ops_director.controller import Controller
            from ti_ops_director.store import Store
            mcfg = load_config()
            ctl = Controller(mcfg, Store(mcfg.db_path))
            today = datetime.now(timezone.utc).date()
            from ti_ops_director.country_gaps import recheck_bucket
            for t in planned[: cfg.tasks_per_run]:
                tid, created = ctl.enqueue(task_type="country_content_draft", inputs=t["inputs"],
                                           dedupe_subject=f"{t['inputs']['countryId']}:{t['inputs']['category']}:{t['inputs']['gap']['type']}",
                                           time_bucket=recheck_bucket(today, cfg.raw["schedule"]["recheck_after_days"]), priority=40)
                print(f"queue: {tid} {'created' if created else 'duplicate suppressed'}")
        return EXIT_OK
    if a.command == "eval":
        return run_eval(a, cfg, env)
    backend = _backend(env, cfg, create=a.command not in ("status", "route-exceptions"))
    try:
        if a.command == "status":
            st = _sections(backend.snapshot().state() or empty_state())
            tasks = {v["id"]: {"state": v["state"], "country": v["inputs"]["countryId"], "category": v["inputs"]["category"],
                               "reason": v.get("blockingReason") or v.get("error"), "draft": v.get("draftId")}
                     for v in st["tasks"].values()}
            print(json.dumps({"controls": st["controls"], "tasks": tasks, "drafts": st["drafts"], "lastRuns": st["runs"][-3:],
                              "spend": st.get("spend", {}).get("total", 0)}, indent=2))
            return EXIT_OK
        if a.command == "route-exceptions":
            from ti_ops_director.config import load_config
            from ti_ops_director.country_worker import mirror_exceptions
            from ti_ops_director.store import Store
            st = _sections(backend.snapshot().state() or empty_state())
            mcfg = load_config()
            print(json.dumps(mirror_exceptions(Store(mcfg.db_path), st["exceptions"])))
            return EXIT_OK
        if a.command == "rollback":
            from ti_ops_director.country_publish import make_main_backend, rollback_publication
            if not a.arg or not a.by:
                print("rollback needs: <publicationId> --by NAME")
                return EXIT_CONFIG
            summary = rollback_publication(make_main_backend(ROOT), a.arg, datetime.now(timezone.utc))
            print(json.dumps(summary))
            return EXIT_OK
        if a.command in ("pause", "resume", "stop"):
            def fn(snap):
                st = _sections(snap.state() or empty_state())
                st["controls"]["paused"] = a.command != "resume"
                st["controls"]["stopped"] = a.command == "stop"
                return {"state": dump_state(st)}, st["controls"]
            _, c = backend.transact(fn, message=f"Country builder: {a.command}")
            print(f"{a.command}: {c}")
            return EXIT_OK
        if a.command == "review":
            if not (a.arg and a.decision and a.by):
                print("review needs: <draftId> --decision accepted|rejected|needs_changes --by NAME")
                return EXIT_CONFIG
            def fn(snap):
                st = _sections(snap.state() or empty_state())
                d = st["drafts"].get(a.arg)
                if not d:
                    return None, None
                path = "file:" + d["path"]
                doc = json.loads(snap.text(path))
                doc["review"] = {"decision": a.decision, "by": a.by, "note": a.note[:500],
                                 "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                doc["reviewStatus"] = {"accepted": "REVIEWED_ACCEPTED", "rejected": "REVIEWED_REJECTED",
                                       "needs_changes": "REVIEWED_NEEDS_CHANGES"}[a.decision]
                d["reviewStatus"] = doc["reviewStatus"]
                return {path: json.dumps(doc, indent=2, ensure_ascii=False) + "\n", "state": dump_state(st)}, doc["reviewStatus"]
            _, status = backend.transact(fn, message=f"Country builder: review {a.arg}")
            print(status or "draft not found")
            return EXIT_OK if status else EXIT_CONFIG
    except StateSyncError as exc:
        print(f"ERROR: {exc}")
        return EXIT_STATE
    return EXIT_OK


def run_eval(a, cfg, env):
    from datetime import datetime as dt
    from ti_ops_director.country_eval import make_eval_backend, render_summary, run_approved
    from ti_ops_director.monitor_eval import EvalRefused, fetch_ecb_rate
    if not a.approval:
        print(json.dumps({"refused": "no approval file: no live evaluation is run. Use the unit tests for the scripted harness check."}))
        return 2
    try:
        approval = json.loads(Path(a.approval).read_text())
        rate = fetch_ecb_rate()
        backend = make_eval_backend(env, ROOT, cfg)
        report = run_approved(approval, cfg, ROOT, backend, env, rate, dt.now(timezone.utc).date().isoformat())
    except (EvalRefused, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"refused": str(exc)}))
        if env.get("GITHUB_STEP_SUMMARY"):
            Path(env["GITHUB_STEP_SUMMARY"]).open("a").write(f"## Country builder Haiku evaluation\n\nRefused, no call made: {exc}\n")
        return 2
    print(json.dumps(report, indent=2))
    if env.get("GITHUB_STEP_SUMMARY"):
        Path(env["GITHUB_STEP_SUMMARY"]).open("a").write(render_summary(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
