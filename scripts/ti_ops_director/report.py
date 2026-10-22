"""Operational report. States are separated: implemented, tested, live-checked, deployed, production-verified."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from . import AGENT_NAME, BUSINESS_APPLICATION, COMPANY, STREAM, SUBSTREAM
from .budget import Budget, eur_view
from .scheduling import display, next_daily_run


MONITOR_STATE = Path("data") / "continuous_monitor_state.json"


def monitor_status(repo_root, now):
    """The GitHub Actions monitor, read from its committed state file (as fresh as this checkout)."""
    from .monitor_bridge import committed, spend_section
    path = Path(repo_root) / MONITOR_STATE
    if not path.exists():
        return {"available": False, "detail": f"{MONITOR_STATE} not found in this checkout"}
    st = json.loads(path.read_text(encoding="utf-8"))
    last = st.get("lastOutcome") or {}
    spend = spend_section(json.loads(json.dumps(st)))
    day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    d, m = committed(spend, day, month)
    return {
        "available": True, "last_run_utc": st.get("lastRun"),
        "last_triage": last.get("triage") or ("unknown (state written before outcome tracking)" if st.get("lastRun") else None),
        "last_triage_completed": bool(last.get("triageCompleted")), "last_reason": last.get("reason"),
        "last_completed_triage_utc": st.get("lastTriageCompleted"),
        "headlines_pending": (last.get("headlines") or {}).get("pending"),
        "consecutive_untriaged": st.get("consecutiveUntriaged", 0),
        "blocked": st.get("lastTriageBlocked"),
        "publication": last.get("publication"),
        "spend_committed_usd": {"today": round(d, 6), "month": round(m, 6)},
        "open_reservations": len(spend["open"]), "expired_reservations_charged_in_full": len(spend.get("expired", [])),
        "limits_at_last_run": (last.get("spend") or {}).get("limits"),
        "mode_at_last_run": (last.get("spend") or {}).get("mode"),
        "token_counter": st.get("budget"),
    }


def production_status(repo_root):
    """The production controller's ledger as seen from this host (local file backend), if present."""
    import os
    base = Path(os.environ.get("TI_PC_STATE_DIR") or Path(repo_root) / "var" / "production_controller")
    path = base / "production_controller_state.json"
    if not path.exists():
        return {"available": False, "detail": "no local controller state; on GitHub Actions the ledger and the "
                "latest report are on the branch named by TI_PC_STATE_BRANCH"}
    st = json.loads(path.read_text(encoding="utf-8"))
    open_ = [t for t in st.get("tasks") or [] if t["state"] == "blocked"]
    return {"available": True, "last_run_utc": st.get("lastRun"), "verdict": ((st.get("runs") or [{}])[-1]).get("verdict"),
            "open": [{"severity": t["severity"], "summary": t["summary"], "since": t["first_seen_utc"],
                      "action": t["blocking_reason"]} for t in open_]}


def country_builder_status(repo_root):
    """The country builder's ledger as seen from this host (queue worker's file backend), if present."""
    import os
    base = Path(os.environ.get("TI_CB_STATE_DIR") or Path(repo_root) / "var" / "country_builder")
    path = base / "country_builder_state.json"
    if not path.exists():
        return {"available": False, "detail": "no local builder state; on GitHub Actions the ledger and the latest report are on "
                "the branch named by TI_CB_STATE_BRANCH (default ti-country-drafts)"}
    st = json.loads(path.read_text(encoding="utf-8"))
    tasks = list((st.get("tasks") or {}).values())
    counts = {}
    for t in tasks:
        counts[t["state"]] = counts.get(t["state"], 0) + 1
    return {"available": True, "controls": st.get("controls"), "task_counts": counts, "drafts": len(st.get("drafts") or {}),
            "spend_usd": (st.get("spend") or {}).get("total", 0),
            "last_run_utc": ((st.get("runs") or [{}])[-1]).get("at")}


def build_report(ctl):
    store, cfg = ctl.store, ctl.cfg
    now = store.clock()
    tasks = store.list_tasks()
    counts = {s: 0 for s in ("pending", "running", "blocked", "failed", "completed", "cancelled")}
    for t in tasks:
        counts[t["state"]] += 1
    diag = []
    live_checked = fixture_checked = 0
    for t in tasks:
        if t["task_type"] != "feed_health_diagnostic" or not t["outputs"]:
            continue
        out, ev = json.loads(t["outputs"]), json.loads(t["evidence"] or "[]")
        label = out.get("evidence_mode", "UNKNOWN")
        live_checked += label == "LIVE"
        fixture_checked += label == "FIXTURE"
        diag.append({"task": t["id"], "state": t["state"], "source": out["source_id"], "health": out["health"],
                     "classification": out["classification"], "evidence_mode": label, "detail": out["detail"],
                     "retrieved_at_utc": (ev[0]["retrieved_at_utc"] if ev else None)})
    last_ok = store.query("SELECT ts FROM runs WHERE ok=1 ORDER BY id DESC LIMIT 1")
    last_scan = store.query("SELECT ts FROM runs WHERE kind='feed_health_scan' AND ok=1 ORDER BY id DESC LIMIT 1")
    test_run = store.get_meta("last_test_run")
    scan_rows = store.query("SELECT detail FROM runs WHERE kind='feed_health_scan' ORDER BY id DESC LIMIT 1")
    scan = json.loads(scan_rows[0]["detail"]) if scan_rows else None
    spend = Budget(store, cfg).summary()
    spend_eur = eur_view(store, spend["month_utc"]["gross_estimated_usd"])
    needs_marc = [{"task": t["id"], "source": json.loads(t["inputs"]).get("source", {}).get("id"),
                   "action": t["blocking_reason"]} for t in tasks if t["state"] == "blocked" and t["requires_human"]]
    blocked_other = [{"task": t["id"], "type": t["task_type"], "reason": t["blocking_reason"]}
                     for t in tasks if t["state"] == "blocked" and not t["requires_human"]]
    eligible_now = [t["id"] for t in tasks if t["state"] == "pending"]
    return {
        "agent": AGENT_NAME, "company": COMPANY, "stream": STREAM, "substream": SUBSTREAM,
        "business_application": BUSINESS_APPLICATION,
        "generated_utc": now.isoformat(timespec="seconds"),
        "generated_display": display(now, cfg.timezone),
        "period": {"from_utc": (now - timedelta(hours=24)).isoformat(timespec="seconds"), "to_utc": now.isoformat(timespec="seconds")},
        "mode": "DRY-RUN" if cfg.dry_run else "LIVE (paid execution permitted only within configured allowance)",
        "controls": {"paused": ctl.is_paused(), "stopped": store.get_control("stop", "0") == "1",
                     "publication_enabled": False},
        "latest_successful_run_utc": last_ok[0]["ts"] if last_ok else None,
        "latest_successful_scan_utc": last_scan[0]["ts"] if last_scan else None,
        "task_counts": counts,
        "feed_health": diag, "latest_scan": scan,
        "needs_marc": needs_marc, "blocked_other": blocked_other,
        "unimplemented_workers": [{"worker": w.name, "role": w.description} for w in ctl.registry.unimplemented()],
        "next_eligible_actions": {"pending_tasks": eligible_now,
                                  "next_daily_consolidation": display(next_daily_run(now, cfg.daily_consolidation_local, cfg.timezone), cfg.timezone),
                                  "monitor_interval_minutes": cfg.monitor_interval_minutes},
        "spend": spend, "spend_month_eur": spend_eur,
        "actions_monitor": monitor_status(cfg.repo_root, now),
        "production": production_status(cfg.repo_root),
        "country_builder": country_builder_status(cfg.repo_root),
        "maturity": {
            "implemented": [w.name for w in ctl.registry.all() if w.implemented],
            "tested": test_run or "no recorded test run in this database",
            "live_checked": f"{live_checked} diagnostic task(s) with live evidence; {fixture_checked} FIXTURE task(s) (not live evidence)",
            "deployed": "no evidence: nothing deployed by this build",
            "production_verified": "no evidence",
        },
    }


def render_markdown(r):
    L = [f"# Operational report: {r['agent']}", "",
         f"{r['company']} | {r['stream']} | {r['substream']} | Business application: {r['business_application']}", "",
         f"Generated: {r['generated_utc']} UTC ({r['generated_display']}). Stored times are UTC.",
         f"Reporting period: {r['period']['from_utc']} to {r['period']['to_utc']} (UTC).",
         f"Mode: {r['mode']}. Paused: {r['controls']['paused']}. Stopped: {r['controls']['stopped']}. "
         f"Automatic publication: disabled.",
         f"Latest successful run: {r['latest_successful_run_utc'] or 'none recorded'}. "
         f"Latest successful feed scan: {r['latest_successful_scan_utc'] or 'none recorded'}.", "",
         "## Maturity (these are separate states)", ""]
    m = r["maturity"]
    L += [f"- Implemented: {', '.join(m['implemented'])}",
          f"- Tested: {m['tested']}", f"- Live-checked: {m['live_checked']}",
          f"- Deployed: {m['deployed']}", f"- Production-verified: {m['production_verified']}", "",
          "This is not a production-ready claim.", "", "## Tasks", ""]
    L += [f"- {k}: {v}" for k, v in r["task_counts"].items()]
    L += ["", "## Feed-health exceptions", ""]
    if not r["feed_health"]:
        L.append("No diagnostic results recorded. Sources not diagnosed are NOT thereby healthy.")
    else:
        L += ["| Source | Health | Cause | Evidence | Task state | Detail |", "|---|---|---|---|---|---|"]
        for d in r["feed_health"]:
            L.append(f"| {d['source']} | {d['health'].upper()} | {d['classification']} | {d['evidence_mode']} | {d['state']} | {d['detail']} |")
    sc = r["latest_scan"]
    if sc:
        L += ["", f"Snapshot generated {sc.get('snapshot_generated')} (stale: {sc.get('snapshot_stale')}, missing: "
              f"{sc.get('snapshot_missing')}). Sources reported healthy by the stored snapshot: "
              f"{', '.join(sc.get('healthy_per_snapshot') or []) or 'none'}. These are snapshot claims, not live checks."]
    L += ["", "## Action required from Marc", ""]
    L += ([f"- {a['source'] or a['task']}: {a['action']}" for a in r["needs_marc"]] or ["None."])
    L += ["", "## Other blocked tasks", ""]
    L += ([f"- {b['type']} ({b['task']}): {b['reason']}" for b in r["blocked_other"]] or ["None."])
    L += ["", "## Unimplemented workers (tasks for these are blocked, never faked)", ""]
    L += [f"- {w['worker']}: {w['role']}" for w in r["unimplemented_workers"]]
    n = r["next_eligible_actions"]
    L += ["", "## Next eligible actions", "",
          f"- Pending tasks: {len(n['pending_tasks'])}", f"- Next daily consolidation slot: {n['next_daily_consolidation']}",
          f"- Monitoring interval target: {n['monitor_interval_minutes']} minutes", ""]
    mon = r.get("actions_monitor") or {}
    L += ["## Haiku monitor on GitHub Actions (from the committed state file)", ""]
    if not mon.get("available"):
        L.append(f"- Not available: {mon.get('detail')}")
    else:
        verdict = "COMPLETED" if mon["last_triage_completed"] else "NOT COMPLETED"
        L += [f"- Last run: {mon['last_run_utc'] or 'never'}. A run that executed is not the same as triage completed.",
              f"- Last triage: {verdict} ({mon['last_triage']}); reason: {mon['last_reason'] or 'none'}",
              f"- Last completed triage: {mon['last_completed_triage_utc'] or 'none recorded'}",
              f"- New headlines left untriaged at last run: {mon['headlines_pending']}; consecutive runs untriaged: "
              f"{mon['consecutive_untriaged']}",
              f"- Gateway mode at last run: {mon['mode_at_last_run'] or 'not opened'}; limits: {mon['limits_at_last_run']}",
              f"- Committed spend (USD, estimated, includes open reservations): today {mon['spend_committed_usd']['today']}, "
              f"month {mon['spend_committed_usd']['month']}; open reservations {mon['open_reservations']}; "
              f"abandoned reservations charged in full {mon['expired_reservations_charged_in_full']}",
              f"- Publication at last run: {mon['publication'] or 'not recorded'}",
              f"- Daily token counter (pre-gateway control, still enforced): {mon['token_counter'] or 'none'}", ""]
    pc = r.get("production") or {}
    L += ["## Production controller (03AA-04_Production_Controller_TI)", ""]
    if not pc.get("available"):
        L += [f"- Not available here: {pc.get('detail')}", ""]
    else:
        L += [f"- Last run: {pc['last_run_utc']}; verdict: {pc['verdict']}; open exceptions: {len(pc['open'])}"]
        L += [f"- {o['severity'].upper()} since {o['since']}: {o['summary']} Action: {o['action']}" for o in pc["open"]]
        L.append("")
    cb = r.get("country_builder") or {}
    L += ["## Country intelligence builder (03AA-04_Country_Intelligence_Builder_TI)", ""]
    if not cb.get("available"):
        L += [f"- Not available here: {cb.get('detail')}", ""]
    else:
        L += [f"- Last run: {cb['last_run_utc']}; tasks: {cb['task_counts']}; drafts and assessments recorded (publication state per run in its report): {cb['drafts']}; "
              f"controls: {cb['controls']}; spend recorded: {cb['spend_usd']} USD (estimated)", ""]
    L += ["## API usage and expenditure (this database)", ""]
    s = r["spend"]
    L += [f"- Currency: {s['currency']} (provider currency). Source: {s['cost_source']}.",
          f"- Amounts: {s['amount_status']}.",
          f"- Allowance configured: {s['allowance_configured']}. Limits: {s['limits_usd']}",
          f"- Today (UTC): {s['today_utc']['calls']} calls, estimated {s['today_utc']['gross_estimated_usd']} USD, remaining {s['today_utc']['remaining_usd']}",
          f"- Month (UTC): {s['month_utc']['calls']} calls, estimated {s['month_utc']['gross_estimated_usd']} USD, remaining {s['month_utc']['remaining_usd']}"]
    e = r["spend_month_eur"]
    L.append(f"- Month in EUR: {e['eur']} (rate {e['rate']} on {e['rate_date']}, {e['rate_source']}; original {e['original_usd']} USD)"
             if e.get("eur") is not None else f"- Month in EUR: not converted. {e['flag']}.")
    L.append("- Credits and payable cost: " + ("not verified" if s["payable_usd"] is None else f"{s['payable_usd']} USD payable (confirmed)"))
    L.append("- Claude Code subscription usage is excluded from these figures.")
    return "\n".join(L) + "\n"
