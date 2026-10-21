"""Operational report. States are separated: implemented, tested, live-checked, deployed, production-verified."""
from __future__ import annotations

import json
from datetime import timedelta

from . import AGENT_NAME, BUSINESS_APPLICATION, COMPANY, STREAM, SUBSTREAM
from .budget import Budget, eur_view
from .scheduling import display, next_daily_run


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
          f"- Monitoring interval target: {n['monitor_interval_minutes']} minutes", "", "## API usage and expenditure", ""]
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
