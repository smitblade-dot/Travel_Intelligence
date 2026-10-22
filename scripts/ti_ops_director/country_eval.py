"""Quality evaluation of the country builder against manually assessed cases.

With scripted (mock) model replies this checks the scorer, the grounding checks and the controls only. Run
against the real model under a written, capped approval it measures whether Haiku is suitable. The two are
labelled differently in every report; a mocked run never claims model quality.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT
from .country_config import BuilderConfig
from .country_draft import PROMPT_VERSION, SYSTEM_PROMPT
from .country_engine import Builder, BuilderSpend, DynamicPaths, LocalBackend, _no_cost, _sections, empty_state
from .feed_health import HttpResult
from .models import AnthropicProvider
from .monitor_bridge import spend_section
from .monitor_eval import EvalRefused, conservative_usd_cap, fetch_ecb_rate
from .monitor_state import dump_state
from .store import fingerprint

CASES = ROOT / "tests" / "fixtures" / "country_builder" / "labelled_cases.json"
FIXTURES = ROOT / "tests" / "fixtures" / "country_builder"
EVAL_NOW = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)      # the recorded pages are dated; judge them as of retrieval
LIVE, MOCKED = "LIVE_MODEL", "MOCKED_OUTPUTS"
DEFAULT_PAGES = [("safety-and-security", "gh/fcdo_safety.html"), ("/health", "gh/fcdo_health.html"),
                 ("travelhealthpro", "gh/travelhealthpro.html"), ("destinations/traveler", "gh/cdc_travel.html"),
                 ("power.larc.nasa.gov", "gh/nasa_power.json"), ("cckpapi", "gh/worldbank_cckp.json")]
PURPOSE = "country_builder_haiku_evaluation"
EVAL_PATHS = {"state": "country_builder_eval_ledger.json"}
RESULTS_KEY = "file:eval/country_builder_haiku_eval.json"


class FixtureNet:
    """Serves the recorded pages. ``pages`` maps a URL fragment to a fixture file replacing the default."""
    def __init__(self, pages=None):
        self.map = dict(DEFAULT_PAGES)
        for frag, name in (pages or {}).items():
            for k in list(self.map):
                if frag in k or k in frag:
                    self.map[k] = name
        self.calls = 0

    def __call__(self, url, **kw):
        self.calls += 1
        if url.endswith("/robots.txt"):
            return HttpResult(404, b"", "http_error")
        for frag, name in self.map.items():
            if frag in url:
                return HttpResult(200, (FIXTURES / name).read_bytes(), None)
        return HttpResult(404, b"", "http_error")


def load_cases(path=CASES):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def measurement_of(spend):
    live = isinstance(spend.gateway.provider, AnthropicProvider) and not spend.cfg.dry_run
    return LIVE + ":" + spend.cfg.models["haiku"] if live else MOCKED


def score_case(case, outcome):
    d = outcome.get("draft") or {}
    items = [i for i in d.get("items", []) if i.get("derivation") is None]          # model-written items only
    by_topic = {}
    for i in items:
        by_topic.setdefault(i["topic"], []).extend(c["text"] for c in i["claims"])
    cover = [{"topic": m["topic"], "any": m["any"],
              "met": any(re.search("|".join(m["any"]), t, re.I) for t in by_topic.get(m["topic"], []))} for m in case["must_cover"]]
    all_text = " ".join(t for ts in by_topic.values() for t in ts) + " " + " ".join(i["title"] for i in items)
    forbidden = [p for p in case["must_not"] if re.search(p, all_text)]
    missing_topics = {m["topic"] for m in (d.get("missingEvidence") or outcome.get("missing") or [])}
    missing_ok = [t for t in case["must_report_missing"] if t in missing_topics]
    rejected = len(d.get("rejectedClaims", []))
    kept = sum(len(v) for v in by_topic.values())
    row = {"id": case["id"], "state": outcome["state"], "reason": outcome["reason"], "coverage": f"{sum(c['met'] for c in cover)}/{len(cover)}",
           "coverage_met": sum(c["met"] for c in cover), "coverage_total": len(cover), "forbidden_hits": forbidden,
           "missing_reported": f"{len(missing_ok)}/{len(case['must_report_missing'])}", "missing_ok": len(missing_ok),
           "missing_total": len(case["must_report_missing"]), "claims_kept": kept, "claims_rejected": rejected,
           "rejection_reasons": sorted({r["reason"] for r in d.get("rejectedClaims", [])}),
           "calls": (outcome.get("cost") or {}).get("calls", 0), "cost_usd": (outcome.get("cost") or {}).get("estimatedUsd", 0)}
    cc = case.get("conflict_check")
    if cc:
        hit = [c for i in items if i["topic"] == cc["topic"] for c in i["claims"] if re.search(cc["pattern"], c["text"], re.I)]
        ok = bool(hit) and all(c["status"] in cc["allowed_statuses"] for c in hit) and \
            all(c["independentSources"] >= 2 for c in hit if c["status"] == "CONFLICT")
        # No claim about the contested point is also acceptable: the draft then asserts nothing it cannot support.
        row["conflict_handled"] = ok or not hit
        row["conflict_claims"] = [{"status": c["status"], "text": c["text"]} for c in hit]
    return row


def evaluate(cfg: BuilderConfig, root, cases_doc, spend_factory, provider_label=None):
    for cdef in cfg.raw["categories"].values():          # the evaluation measures the model path, not the extractive one
        for t in cdef["topics"].values():
            if t["kind"] == "model":
                t["heading_patterns"] = ["(?!)x"]
    rows = []
    for case in cases_doc["cases"]:
        net = FixtureNet(case.get("pages"))
        spend = spend_factory()
        b = Builder(cfg, root, None, http=net, provider=spend.gateway.provider, clock=lambda: EVAL_NOW, env={}, out=lambda *_: None,
                    workdir=None)
        data = b.load_data()
        country = next(c for c in data["countries"] if c["id"] == "gh")
        inputs = {"countryId": "gh", "country": country["name"], "category": case["category"],
                  "gap": {"type": "MISSING", "detail": "evaluation case"}, "existingRecordIds": []}
        outcome = b.process_task(inputs, empty_state(), data, EVAL_NOW, spend)
        rows.append(score_case(case, outcome) | {"measurement": measurement_of(spend)})
    return summarise(cases_doc["thresholds"], rows, measurement_of(spend))


def summarise(th, rows, measurement):
    cov_met, cov_tot = sum(r["coverage_met"] for r in rows), sum(r["coverage_total"] for r in rows)
    forb = sum(len(r["forbidden_hits"]) for r in rows)
    kept, rej = sum(r["claims_kept"] for r in rows), sum(r["claims_rejected"] for r in rows)
    ratio = rej / (kept + rej) if kept + rej else 0.0
    miss_ok, miss_tot = sum(r["missing_ok"] for r in rows), sum(r["missing_total"] for r in rows)
    conflicts = [r for r in rows if "conflict_handled" in r]
    live = measurement.startswith(LIVE)
    measured = bool(live and rows and all(r["calls"] > 0 for r in rows))
    meets = (measured and cov_tot and cov_met / cov_tot >= th["min_coverage"] and forb <= th["max_forbidden_hits"]
             and ratio <= th["max_rejected_claim_ratio"] and miss_ok == miss_tot and all(r["conflict_handled"] for r in conflicts)
             and all(r["calls"] <= th["max_calls_per_case"] for r in rows))
    return {"measurement": measurement, "model_quality_measured": measured,
            "note": ("Real model outputs scored against manual assessment." if live else
                     "Scripted (mock) outputs: this checks the scorer, grounding checks and controls only. Haiku's quality is NOT measured."),
            "totals": {"coverage": f"{cov_met}/{cov_tot}", "forbidden_hits": forb, "claims_kept": kept, "claims_rejected": rej,
                       "rejected_ratio": round(ratio, 3), "missing_reported": f"{miss_ok}/{miss_tot}",
                       "conflict_cases_handled": f"{sum(r['conflict_handled'] for r in conflicts)}/{len(conflicts)}",
                       "calls": sum(r["calls"] for r in rows), "cost_usd": round(sum(r["cost_usd"] for r in rows), 6)},
            "thresholds": th, "meets_thresholds": bool(meets) if measured else None,
            "verdict": (("SUITABLE on these cases" if meets else "NOT SUITABLE on these cases") if measured
                        else ("INCOMPLETE: not every case reached the model" if live else "NOT MEASURED (mock)")),
            "cases": rows}


def prepare_for_eval(cfg, cap_usd):
    cfg.dry_run, cfg.mode = False, "draft"
    cfg.limits.update(per_task_limit_usd=cap_usd, daily_limit_usd=cap_usd, monthly_limit_usd=cap_usd, total_limit_usd=cap_usd)


def make_spend(cfg: BuilderConfig, state, backend, provider, cap_usd=None, workdir=None):
    ecfg = cfg.gateway_config()
    ecfg.dry_run = False
    ecfg.per_task_limit_usd = ecfg.daily_limit_usd = ecfg.monthly_limit_usd = ecfg.total_limit_usd = cap_usd
    return BuilderSpend(ecfg, state, workdir=workdir, provider=provider, backend=backend)


def eval_fingerprint(cases_doc, model):
    return fingerprint([PROMPT_VERSION, SYSTEM_PROMPT, model, cases_doc["cases"], cases_doc["thresholds"]])


def make_eval_backend(env, root, cfg):
    choice = (env.get("TI_CB_STATE_BACKEND") or "git").strip().lower()
    if choice == "file":
        b = LocalBackend(env.get("TI_CB_STATE_DIR") or Path(root) / "var" / "country_builder")
        base = b.paths.base
        b.paths = _EvalPaths(base)
        return b
    from .production import BranchStateBackend
    paths = DynamicPaths()
    paths["state"] = EVAL_PATHS["state"]
    backend = BranchStateBackend(root, paths, branch=cfg.state_branch, mirror_worktree=False, create=True)
    backend.paths = paths
    return backend


class _EvalPaths(dict):
    def __init__(self, base):
        super().__init__()
        self.base = base

    def __missing__(self, key):
        if key == "state":
            return self.base / EVAL_PATHS["state"]
        if key.startswith("file:"):
            return self.base / key[5:]
        raise KeyError(key)


def run_approved(approval, cfg, root, backend, env, rate, today, provider=None, cases_doc=None, spend_workdir=None):
    """One bounded live evaluation. Refuses before any call unless every condition holds."""
    from datetime import date
    if approval.get("approvedBy") != "Marc" or approval.get("purpose") != PURPOSE:
        raise EvalRefused("approval file does not authorise this evaluation")
    if (date.fromisoformat(today) - date.fromisoformat(rate["reference_date"])).days > 5:
        raise EvalRefused(f"ECB rate dated {rate['reference_date']} is stale; no call made")
    cap = conservative_usd_cap(approval["capEur"], rate["usd_per_eur"], approval["conversion"]["margin"],
                               approval["conversion"]["absoluteUsdCeiling"])
    if cap <= 0:
        raise EvalRefused("computed USD cap is not positive")
    if not env.get("ANTHROPIC_API_KEY") and provider is None:
        raise EvalRefused("ANTHROPIC_API_KEY missing; no call made")
    model = approval["model"]
    cfg.raw["spending"]["sonnet_escalation_enabled"] = bool(approval.get("allowSonnetEscalation", False))
    cases_doc = cases_doc or load_cases()
    fp = eval_fingerprint(cases_doc, model)
    state = backend.snapshot().state()
    if (state.get("completed") or {}).get(fp):
        return {"skipped": "already evaluated with this prompt, model and case set", "result": state["completed"][fp]}
    if state.get("completed") and (state.get("approval") or {}).get("approvalId") == approval["approvalId"]:
        return {"skipped": "approval already used for a completed evaluation; a new approval is required"}
    if provider is None:
        provider = AnthropicProvider(env["ANTHROPIC_API_KEY"])
    prepare_for_eval(cfg, cap)

    def factory():       # a fresh view of the shared ledger for every case, so the all-time cap is always current
        s = make_spend(cfg, backend.snapshot().state(), backend, provider, cap, spend_workdir)
        s.cfg.models["haiku"] = model
        return s

    result = evaluate(cfg, root, cases_doc, factory)
    report = {"approval_id": approval["approvalId"], "model": model, "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "exchange_rate": rate, "cap": {"eur": approval["capEur"], "usd": cap},
              "publication": "none: the evaluation uses recorded pages and writes no draft and no data.json",
              "evaluation": result}

    def fn(snap):
        st = snap.state() or {}
        sp = spend_section(st)
        total = round(float(sp.get("total", 0) or 0) + sum(float(r["usd"]) for r in sp["open"]), 6)
        report["ledger"] = {"total_committed_usd": total, "cap_usd": cap, "remaining_usd": round(max(cap - total, 0), 6)}
        st["approval"] = {k: approval[k] for k in ("approvalId", "approvedBy", "capEur")}
        if result["measurement"].startswith(LIVE) and result["model_quality_measured"]:
            st.setdefault("completed", {})[fp] = {"run_at": report["run_at"], "verdict": result["verdict"], "totals": result["totals"]}
        return {"state": dump_state(st), RESULTS_KEY: json.dumps(report, indent=2) + "\n"}, None

    # Settlement of every reservation is written by the gateway wrapper during the calls (checkpoint); this final
    # write records the approval, the verdict and the results file.
    backend.transact(fn, message="Country builder evaluation ledger and results")
    return report


def render_summary(r):
    if "skipped" in r:
        return "## Country builder Haiku evaluation\n\nSkipped: " + r["skipped"] + ". No call made.\n"
    e = r["evaluation"]
    t = e["totals"]
    return "\n".join([
        "## Country builder Haiku evaluation", "",
        f"- Measurement: {e['measurement']} (model quality measured: {e['model_quality_measured']})",
        f"- Verdict: **{e['verdict']}**",
        f"- Coverage of manually required facts: {t['coverage']}; forbidden statements: {t['forbidden_hits']}; "
        f"rejected claims {t['claims_rejected']} of {t['claims_kept'] + t['claims_rejected']} ({t['rejected_ratio']}); "
        f"missing-evidence reported {t['missing_reported']}; conflicts handled {t['conflict_cases_handled']}",
        f"- Calls: {t['calls']}; cost (estimated): {t['cost_usd']} USD; cap EUR {r['cap']['eur']} = {r['cap']['usd']} USD; "
        f"ledger committed {r['ledger']['total_committed_usd']} USD",
        f"- Publication: {r['publication']}", ""])
