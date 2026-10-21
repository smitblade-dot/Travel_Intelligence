"""Replays representative cases through the real monitor prompt, gateway and publication guards.

With canned model outputs this tests the harness and the code guards only. Run against a real model
(allowance required) to measure quality; that is a deliberate, separately authorised step.
"""
from __future__ import annotations

import importlib.util
import json
import re
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


# ---- labelled TI cases (tests/fixtures/ops_director/monitor_labelled_cases.json) ----------------------------
EXTRACTION_FIELDS = ("countryId", "category", "incidentDate", "continuingImpact")
LIVE, MOCKED = "LIVE_MODEL", "MOCKED_OUTPUTS"


def _empty_data(countries):
    return {"countries": countries, "records": [], "sources": [], "locations": [], "events": [],
            "sourceObservations": [], "meta": {"counts": {}, "currentIntelligence": {}}}


def measurement_of(spend):
    """LIVE only for the real provider outside dry-run. Anything else measures the harness, not the model."""
    from .models import AnthropicProvider
    live = isinstance(spend.gateway.provider, AnthropicProvider) and not spend.cfg.dry_run
    return LIVE + ":" + spend.cfg.models["haiku"] if live else MOCKED


def score_decision(labels, decision):
    """Relevance = publish flag against the label; extraction only for relevant cases."""
    decision = decision or {}
    row = {"relevance_correct": (decision.get("publish") is True) == labels["relevant"], "extraction": None}
    if labels["relevant"]:
        fields = {f: decision.get(f) == labels.get(f) for f in EXTRACTION_FIELDS}
        key = decision.get("incidentKey")
        fields["incidentKey_valid"] = isinstance(key, str) and bool(re.fullmatch(r"[a-z0-9-]{8,100}", key))
        row["extraction"] = fields
    return row


def evaluate_labelled(doc, spend, monitor):
    """Relevance, extraction and conflicting evidence through the real prompt, gateway and guards."""
    measurement = measurement_of(spend)
    rel = []
    for case in doc["relevance_extraction"]:
        item = {"title": case["title"], "url": "https://example.org/" + case["id"], "date": ""}
        res = monitor.ask_haiku([item], doc["countries"], [], spend)
        row = {"id": case["id"], "status": res.status, "reason": res.reason, "borderline": bool(case.get("borderline"))}
        if res.status in ("ok", "cached"):
            dec = next((d for d in res.output["decisions"] if d.get("index") == 0), None)
            row.update(score_decision(case["labels"], dec))
            row["published_after_guards"] = monitor.publish(_empty_data(doc["countries"]), [item], res.output["decisions"])
        rel.append(row)
    conflicts = []
    for case in doc["conflicting_evidence"]:
        items = [{"title": t, "url": f"https://example.org/{case['id']}/{i}", "date": ""} for i, t in enumerate(case["titles"])]
        res = monitor.ask_haiku(items, doc["countries"], [], spend)
        row = {"id": case["id"], "status": res.status, "reason": res.reason}
        if res.status in ("ok", "cached"):
            data = _empty_data(doc["countries"])
            row["events_created"] = monitor.publish(data, items, res.output["decisions"])
            row["within_limit"] = row["events_created"] <= case["labels"]["max_events"]
            row["statuses"] = sorted({e["eventStatus"] + "/" + e["confidence"] for e in data["events"]})
        conflicts.append(row)
    scored = [r for r in rel if "relevance_correct" in r]
    firm = [r for r in scored if not r["borderline"]]
    extraction = [v for r in scored if r["extraction"] for v in r["extraction"].values()]
    done_conf = [r for r in conflicts if "within_limit" in r]
    complete = len(scored) == len(rel) and len(done_conf) == len(conflicts)
    if not scored and not done_conf:
        measurement = "NOT_RUN"
    return {
        "measurement": measurement,
        "model_quality_measured": measurement.startswith(LIVE) and complete,
        "note": ("Real model outputs scored against manual labels." if measurement.startswith(LIVE) else
                 "No case ran (see each case's reason). Model quality NOT measured." if measurement == "NOT_RUN" else
                 "Mocked outputs: this checks the scorer, parser and guards only. Model quality NOT measured."),
        "relevance": {"cases": len(rel), "scored": len(scored), "correct_firm": sum(r["relevance_correct"] for r in firm),
                      "firm": len(firm), "correct_borderline": sum(r["relevance_correct"] for r in scored if r["borderline"]),
                      "borderline": len(scored) - len(firm)},
        "extraction": {"fields_checked": len(extraction), "fields_correct": sum(extraction)},
        "conflicting_evidence": {"cases": len(conflicts), "within_limit": sum(r["within_limit"] for r in done_conf)},
        "cases": rel, "conflicts": conflicts,
    }


class ScriptedProvider:
    """MOCK provider that returns prepared response strings. Used only to replay malformed responses."""
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def complete(self, **kw):
        self.calls += 1
        text, stop = self.replies.pop(0)
        return {"text": text, "input_tokens": 100, "output_tokens": 20, "stop_reason": stop}


def replay_malformed(doc, monitor, make_spend):
    """Each malformed response is returned twice (first answer and the one corrective retry)."""
    item = {"title": "Heathrow runway closed after incident", "url": "https://example.org/malformed", "date": ""}
    rows = []
    for case in doc["malformed_responses"]:
        reply = (case["raw"], case.get("stop_reason", "end_turn"))
        spend = make_spend(ScriptedProvider([reply, reply]))
        res = monitor.ask_haiku([item], doc["countries"], [], spend)
        accepted = res.status == "ok"
        published = monitor.publish(_empty_data(doc["countries"]), [item], res.output["decisions"]) if accepted else 0
        ok = accepted == case["accept"] and published <= case.get("max_published", 0 if not accepted else 99)
        rows.append({"id": case["id"], "accepted": accepted, "reason": res.reason, "published": published,
                     "calls": res.calls, "as_expected": ok})
    return {"measurement": MOCKED, "cases": rows, "as_expected": sum(r["as_expected"] for r in rows)}


def check_recorded(doc, monitor):
    """Run the verbatim recorded decisions through today's validator and guards. Inputs were not logged."""
    rec = doc["recorded_outputs"]
    items = [{"title": f"(headline not recorded, index {i})", "url": f"https://example.org/recorded/{i}", "date": ""}
             for i in range(len(rec["decisions"]))]
    answer = {"decisions": rec["decisions"]}
    countries = [{"id": c} for c in sorted({d["countryId"] for d in rec["decisions"] if d.get("countryId")})]
    published = monitor.publish(_empty_data(countries), items, rec["decisions"])
    return {"measurement": "RECORDED_OUTPUT:" + rec["model"], "validator_accepts": monitor.decisions_valid(answer),
            "model_publish_true": sum(d.get("publish") is True for d in rec["decisions"]),
            "published_after_current_guards": published,
            "missing_current_schema_fields": sorted({f for d in rec["decisions"] for f in
                                                     ("incidentDate", "continuingImpact", "incidentKey") if f not in d})}
