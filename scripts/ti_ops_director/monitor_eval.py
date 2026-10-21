"""Replays representative cases through the real monitor prompt, gateway and publication guards.

With canned model outputs this tests the harness and the code guards only. Run against a real model
(allowance required) to measure quality; that is a deliberate, separately authorised step.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def load_monitor(root):
    spec = importlib.util.spec_from_file_location("ti_monitor_eval", Path(root) / "scripts" / "continuous_intelligence_monitor.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def evaluate(cases_doc, spend, monitor):
    """Triage one case at a time (index 0) so each decision is judged alone. Returns a result per case."""
    results = []
    for case in cases_doc["cases"]:
        item = {"title": case["title"], "url": case["url"], "date": ""}
        res = monitor.ask_haiku([item], cases_doc["countries"], [], spend)
        row = {"id": case["id"], "status": res.status, "reason": res.reason, "model": res.model,
               "expected_published": case["expect_published"], "published": None, "correct": None}
        if res.status in ("ok", "cached"):
            data = {"countries": cases_doc["countries"], "records": [], "sources": [], "locations": [], "events": [],
                    "sourceObservations": [], "meta": {"counts": {}, "currentIntelligence": {}}}
            row["published"] = monitor.publish(data, [item], res.output["decisions"]) == 1
            row["correct"] = row["published"] == case["expect_published"]
        results.append(row)
    done = [r for r in results if r["correct"] is not None]
    return {"cases": results, "evaluated": len(done), "correct": sum(r["correct"] for r in done),
            "not_run": len(results) - len(done)}
