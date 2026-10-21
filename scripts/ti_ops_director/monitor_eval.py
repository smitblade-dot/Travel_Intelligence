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


def score_decision(labels, decision, normalise=None):
    """Relevance = publish flag against the label; extraction only for relevant cases. ``normalise`` is the
    monitor's own incident-key normalisation, so the score matches what the guard accepts."""
    decision = decision or {}
    row = {"relevance_correct": (decision.get("publish") is True) == labels["relevant"], "extraction": None}
    if labels["relevant"]:
        fields = {f: decision.get(f) == labels.get(f) for f in EXTRACTION_FIELDS}
        key = normalise(decision.get("incidentKey")) if normalise else decision.get("incidentKey")
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
            row.update(score_decision(case["labels"], dec, getattr(monitor, "normalise_incident_key", None)))
            row["decision"] = dec                    # the model's own output, kept for review
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
            row["decisions"] = res.output["decisions"]
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


# ---- separately approved live evaluation (bounded total cap) -------------------------------------------------
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
MAX_RATE_AGE_DAYS = 5          # ECB publishes on TARGET working days; older than this is treated as stale


class EvalRefused(RuntimeError):
    """The approved evaluation must not make any call (no verified rate, bad approval, no key...)."""


def parse_ecb(xml_text):
    """(reference_date, usd_per_eur) from the ECB daily reference-rate XML."""
    day = re.search(r"time=['\"](\d{4}-\d{2}-\d{2})['\"]", xml_text)
    usd = re.search(r"currency=['\"]USD['\"]\s+rate=['\"]([0-9.]+)['\"]", xml_text)
    if not day or not usd:
        raise EvalRefused("ECB reference-rate XML not recognised")
    return day.group(1), float(usd.group(1))


def fetch_ecb_rate(fetch=None):
    from urllib.request import Request, urlopen
    fetch = fetch or (lambda url: urlopen(Request(url, headers={"User-Agent": "TI-Ops-Director/1.0"}), timeout=20)
                      .read(200_000).decode("utf-8"))
    try:
        text = fetch(ECB_URL)
    except Exception as exc:
        raise EvalRefused(f"ECB rate unavailable ({type(exc).__name__}); no call made") from None
    day, rate = parse_ecb(text)
    return {"source": "European Central Bank euro foreign exchange reference rate", "url": ECB_URL,
            "reference_date": day, "usd_per_eur": rate}


def conservative_usd_cap(cap_eur, usd_per_eur, margin, ceiling_usd):
    """EUR cap -> USD cap, rounded DOWN to the cent, reduced by ``margin`` and never above ``ceiling_usd``."""
    import math
    return min(math.floor(cap_eur * usd_per_eur * (1 - margin) * 100) / 100, ceiling_usd)


def eval_fingerprint(doc, monitor, model):
    from .store import fingerprint
    system, _ = monitor.build_request([{"title": "x", "url": "https://example.org/x"}], doc["countries"], [])
    return fingerprint([model, system, doc["relevance_extraction"], doc["conflicting_evidence"]])


def run_approved_eval(approval, backend, env, monitor, doc, rate, today, provider=None):
    """One bounded live run. Every reservation (first calls and corrective retries) is checked against the
    shared ledger's all-time total before the call; a re-run of the same prompt and cases is not repeated."""
    from datetime import date
    from .monitor_bridge import MonitorSpend, spend_section
    from .monitor_state import dump_state
    if approval.get("approvedBy") != "Marc" or approval.get("purpose") != "haiku_quality_evaluation":
        raise EvalRefused("approval file does not authorise this evaluation")
    if (date.fromisoformat(today) - date.fromisoformat(rate["reference_date"])).days > MAX_RATE_AGE_DAYS:
        raise EvalRefused(f"ECB rate dated {rate['reference_date']} is stale; no call made")
    cap = conservative_usd_cap(approval["capEur"], rate["usd_per_eur"], approval["conversion"]["margin"],
                               approval["conversion"]["absoluteUsdCeiling"])
    if cap <= 0:
        raise EvalRefused("computed USD cap is not positive")
    if not env.get("ANTHROPIC_API_KEY") and provider is None:
        raise EvalRefused("ANTHROPIC_API_KEY missing; no call made")
    model = approval["model"]
    fp = eval_fingerprint(doc, monitor, model)
    state = backend.snapshot().state()
    done = (state.get("completed") or {}).get(fp)
    if done:
        return {"skipped": "already evaluated with this prompt, model and case set", "result": done}
    if state.get("completed") and (state.get("approval") or {}).get("approvalId") == approval["approvalId"]:
        # One approval covers one evaluation. A changed prompt or case set needs a new approval from Marc.
        return {"skipped": "approval already used for a completed evaluation; a new approval is required"}
    run_env = {"ANTHROPIC_API_KEY": env.get("ANTHROPIC_API_KEY", ""), "TI_OPS_DRY_RUN": "false",
               "TI_HAIKU_MODEL": model, "TI_OPS_MAX_MODEL_CALLS_PER_TASK": "2",
               **{k: str(cap) for k in ("TI_OPS_PER_TASK_LIMIT_USD", "TI_OPS_DAILY_LIMIT_USD",
                                        "TI_OPS_MONTHLY_LIMIT_USD", "TI_OPS_TOTAL_LIMIT_USD")}}
    spend = MonitorSpend.open(state, env=run_env, backend=backend, provider=provider)
    labelled = evaluate_labelled(doc, spend, monitor)
    usage = spend.store.query("SELECT model, input_tokens, output_tokens, gross_cost_usd FROM usage ORDER BY id")
    charged = spend.store.query("SELECT COALESCE(SUM(actual_usd),0) c, COUNT(*) n FROM reservations "
                                "WHERE task_id!='hydrated' AND state='settled'")[0]
    actual_usd = round(sum(u["gross_cost_usd"] for u in usage), 6)
    # Settled reservations without a usage record are calls whose outcome was unknown: charged at full amount.
    unknown = {"n": charged["n"] - len(usage)}
    charged_unknown = round(max(charged["c"] - actual_usd, 0), 6)
    report = {
        "approval_id": approval["approvalId"], "model": model, "run_at": spend.store.now(),
        "exchange_rate": rate, "cap": {"eur": approval["capEur"], "usd": cap,
                                       "rule": f"floor(EUR x rate x {1 - approval['conversion']['margin']:.2f}) to the cent,"
                                               f" at most {approval['conversion']['absoluteUsdCeiling']} USD"},
        "usage": {"calls_billed": len(usage), "input_tokens": sum(u["input_tokens"] for u in usage),
                  "output_tokens": sum(u["output_tokens"] for u in usage), "actual_usd": actual_usd,
                  "unknown_outcome_calls_charged_in_full": unknown["n"], "unknown_outcome_usd": charged_unknown,
                  "amount_status": "estimated from API usage fields x list price; not invoice-confirmed"},
        "publication": "disabled (evaluation writes no data.json; guards run on an empty in-memory dataset)",
        "labelled": labelled, "recorded": check_recorded(doc, monitor),
    }
    report["usage"]["actual_eur"] = round(actual_usd / rate["usd_per_eur"], 6)

    def finalise(snap):
        st = snap.state()
        spend.settle_into(st)
        committed = round(float(spend_section(st).get("total", 0) or 0)
                          + sum(float(r["usd"]) for r in spend_section(st)["open"]), 6)
        report["ledger"] = {"total_committed_usd": committed, "remaining_usd": round(max(cap - committed, 0), 6),
                            "cap_usd": cap}
        st["approval"] = {k: approval[k] for k in ("approvalId", "approvedBy", "capEur")}
        st["capUsd"] = cap
        if labelled["measurement"].startswith(LIVE) and labelled["model_quality_measured"]:
            st.setdefault("completed", {})[fp] = {"run_at": report["run_at"], "summary": {
                k: labelled[k] for k in ("relevance", "extraction", "conflicting_evidence")}}
        return {"state": dump_state(st), "results": json.dumps(report, indent=2) + "\n"}, None

    backend.transact(finalise, message="Ops Director evaluation ledger and results")
    return report


def render_eval_summary(r):
    if "skipped" in r:
        return "## Haiku quality evaluation\n\nSkipped: " + r["skipped"] + ". No call made.\n"
    lab, u = r["labelled"], r["usage"]
    rel, ext, con = lab["relevance"], lab["extraction"], lab["conflicting_evidence"]
    return "\n".join([
        "## Haiku quality evaluation", "",
        f"- Measurement: {lab['measurement']} (model quality measured: {lab['model_quality_measured']})",
        f"- Relevance: {rel['correct_firm']}/{rel['firm']} firm cases, {rel['correct_borderline']}/{rel['borderline']} borderline",
        f"- Extraction fields: {ext['fields_correct']}/{ext['fields_checked']}",
        f"- Conflicting evidence within limit: {con['within_limit']}/{con['cases']}",
        f"- Calls billed: {u['calls_billed']}; tokens in/out: {u['input_tokens']}/{u['output_tokens']}",
        f"- Cost (estimated): {u['actual_usd']} USD = {u['actual_eur']} EUR at ECB {r['exchange_rate']['usd_per_eur']}"
        f" ({r['exchange_rate']['reference_date']}); unknown-outcome charges {u['unknown_outcome_usd']} USD",
        f"- Cap: EUR {r['cap']['eur']} -> {r['cap']['usd']} USD; ledger committed {r['ledger']['total_committed_usd']} USD",
        f"- Publication: {r['publication']}", ""])
