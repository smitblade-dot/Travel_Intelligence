"""03AA-04_Country_Intelligence_Builder_TI: detect a country/category gap, collect permitted evidence, draft with
Haiku through the spending gateway, check every claim, and save a draft that is kept apart from data.json.

Ordinary code does everything it can: coverage and staleness checks, deduplication, retrieval, parsing,
freshness, grounding checks, storage and reporting. A model call happens only when there is changed evidence to
draft from, the mode is ``draft``, paid execution is configured and the gateway reserves the money first.
Nothing here writes data.json, publishes, or dispatches another workflow.
"""
from __future__ import annotations

import json
import os
import socket
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .country_config import AGENT_NAME, BuilderConfig, load_builder_config
from .country_draft import (PROMPT_VERSION, SYSTEM_PROMPT, build_candidate_batch, build_user, climate_item, ground_claims,
                            item_content, reference_item, structural_valid, validate_batch)
from .country_evidence import collect_category, evidence_fingerprint, http_get, select_passages
from .country_gaps import TASK_TYPE, coverage_summary, find_gaps, task_for
from .models import AnthropicProvider
from .monitor_bridge import MonitorSpend, spend_section
from .monitor_state import FileStateBackend, StateSyncError, dump_state
from .sanitise import clean_text
from .store import fingerprint, iso, utc_now

EXIT_OK, EXIT_CONFIG, EXIT_STATE = 0, 3, 4
LEASE_MINUTES = 20
SCHEMA = 1
REPO_URL = "https://github.com/smitblade-dot/Travel_Intelligence"
SPEND_NOTE = "estimated from API usage fields x configured list prices; not invoice-confirmed"
RETRY_BLOCKS = ("awaiting_paid_authorisation", "budget:", "insufficient_evidence_temporary")


# ---- storage -------------------------------------------------------------------------------------------------
class DynamicPaths(dict):
    """Fixed keys for the ledger and report, plus ``file:<repo path>`` keys for drafts."""
    def __init__(self, base=None):
        super().__init__({"state": "country_builder_state.json", "report": "country_builder_report.md"})
        self.base = base

    def __missing__(self, key):
        if key.startswith("file:"):
            return (self.base / key[5:]) if self.base else key[5:]
        raise KeyError(key)


class LocalBackend(FileStateBackend):
    """File backend for one host (tests, the Master Agent queue). Same contract as the git backend."""
    def __init__(self, base):
        self.base = Path(base)
        super().__init__({})
        self.paths = _LocalPaths(self.base)


class _LocalPaths(dict):
    def __init__(self, base):
        super().__init__()
        self.base = base

    def __missing__(self, key):
        names = {"state": "country_builder_state.json", "report": "country_builder_report.md"}
        rel = names.get(key) or (key[5:] if key.startswith("file:") else None)
        if rel is None:
            raise KeyError(key)
        return self.base / rel


def make_backend(env, root, cfg: BuilderConfig, create):
    choice = (env.get("TI_CB_STATE_BACKEND") or "git").strip().lower()
    if choice == "file":
        return LocalBackend(env.get("TI_CB_STATE_DIR") or Path(root) / "var" / "country_builder")
    if choice != "git":
        cfg.errors.append("TI_CB_STATE_BACKEND must be 'git' or 'file'")
        return None
    if not cfg.state_branch:
        return None
    from .production import BranchStateBackend
    backend = BranchStateBackend(root, DynamicPaths(), branch=cfg.state_branch, mirror_worktree=False, create=create)
    backend.paths = DynamicPaths()          # GitStateBackend copies paths into a plain dict; drafts need the dynamic keys
    return backend


class BuilderSpend(MonitorSpend):
    """The monitor's reserve-before-call wrapper around the existing ModelGateway, with this agent's worker name."""
    WORKER = "country_intelligence_builder"

    def draft(self, system, user, batch_key, **kw):
        self.batch_key = batch_key
        tid, _ = self.store.create_task(
            worker=self.WORKER, task_type=TASK_TYPE, inputs={"batch": batch_key},
            dedupe_key="builder|" + fingerprint([batch_key, self.store.now()]),
            business_application="Travel Intelligence", substream="03AA-04 Intelligence Automation Agents",
            mode="live", paid=True)
        return self.gateway.run(task_id=tid, worker=self.WORKER, task_type=TASK_TYPE, system=system, user=user,
                                validator=kw.pop("validator"), **kw)


def empty_state():
    return {"schemaVersion": SCHEMA, "agent": AGENT_NAME, "controls": {"paused": False, "stopped": False},
            "tasks": {}, "drafts": {}, "runs": []}


def _sections(state):
    state.setdefault("controls", {"paused": False, "stopped": False})
    for k, empty in (("tasks", {}), ("drafts", {}), ("runs", [])):
        if not isinstance(state.get(k), type(empty)):
            state[k] = empty
    return state


class Outcome(dict):
    """What one task produced. Keys: state (completed|blocked|failed), reason, draft (dict or None), cost, notes."""


# ---- the builder ---------------------------------------------------------------------------------------------
class Builder:
    def __init__(self, cfg: BuilderConfig, root, backend, *, http=http_get, provider=None, clock=utc_now, env=None, out=print,
                 workdir=None, data_path=None):
        self.cfg, self.root, self.backend, self.http = cfg, Path(root), backend, http
        self.clock, self.env, self.out, self.workdir = clock, os.environ if env is None else env, out, workdir
        self.owner = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.data_path = Path(data_path or self.root / "data.json")
        self.provider = provider
        if provider is None and self.env.get("ANTHROPIC_API_KEY"):
            self.provider = AnthropicProvider(self.env["ANTHROPIC_API_KEY"])

    # -- planning --
    def load_data(self):
        return json.loads(self.data_path.read_text(encoding="utf-8"))

    def plan(self, data, today):
        scope = self.cfg.raw["scope"]
        gaps = find_gaps(data, self.cfg.countries, self.cfg.categories, today, scope["stale_after_days_since_checked"],
                         tuple(scope["gap_types"]))
        return gaps, [task_for(g, today, self.cfg.raw["schedule"]["recheck_after_days"]) | {"gap": g} for g in gaps]

    # -- model preconditions --
    def model_allowed(self):
        """(allowed, reason). Dry-run and missing configuration never reach the provider."""
        if self.cfg.mode != "draft":
            return False, "mode_is_" + self.cfg.mode
        if self.cfg.dry_run:
            return False, "awaiting_paid_authorisation"
        if not self.cfg.paid_allowance_configured:
            return False, "awaiting_paid_authorisation"
        if self.provider is None:
            return False, "provider_not_configured"
        return True, None

    # -- one task --
    def process_task(self, inputs, state, data, now, spend=None) -> Outcome:
        cfg, today = self.cfg, now.date()
        cc, category = inputs["countryId"], inputs["category"]
        cdef = cfg.category_defs[category]
        topics = cdef["topics"]
        country = next(c for c in data["countries"] if str(c["id"]).lower() == cc)
        evidence = collect_category(cfg, cc, category, self.http, now, data["sources"])
        passages, per_topic = select_passages(evidence, topics, cfg.limits_cfg)
        model_topics = [t for t, d in topics.items() if d["kind"] == "model" and per_topic.get(t)]
        by_code = {e["code"]: e["publisher"] for e in evidence}
        reference = {k: country.get(k) for k in ("plugTypes", "voltage", "frequency")} if category == "EQUIPMENT" else None
        fp = evidence_fingerprint(passages, evidence, list(topics), reference)
        base = {"evidence": evidence, "fingerprint": fp, "passages": passages}
        prior = [d for d in state["drafts"].values() if d["countryId"] == cc and d["category"] == category and not d.get("supersededBy")]
        complete_prior = [d for d in prior if d.get("modelStatus") in ("DRAFTED", "NOT_NEEDED")]
        if any(d["evidenceFingerprint"] == fp for d in complete_prior):
            return Outcome(state="completed", reason="unchanged_evidence", draft=None, cost=_no_cost("no model call: evidence unchanged"),
                           existing=[d["draftId"] for d in complete_prior if d["evidenceFingerprint"] == fp][0], **base)
        items, missing, rejected, current = [], [], [], []
        for tname, tdef in topics.items():
            if tdef["kind"] == "deterministic":
                it = climate_item(evidence, tdef, today)
                if it:
                    items.append(it)
                else:
                    missing.append({"topic": tname, "reason": "the climatology source was not retrieved", "origin": "collector"})
            elif tdef["kind"] == "reference":
                it = reference_item(country, tdef)
                if it:
                    items.append(it)
            elif tdef["kind"] == "outlook":
                missing.append({"topic": tname, "origin": "collector",
                                "reason": "No approved current seasonal-outlook source is configured for the next 4-6 weeks. "
                                          "Model knowledge is not used as evidence of current conditions."})
            elif tname not in model_topics:
                missing.append({"topic": tname, "origin": "collector",
                                "reason": "no usable passage was retrieved for this topic (" + _source_statuses(evidence, tdef) + ")"})
        usable = [e for e in evidence if e["status"] == "RETRIEVED"]
        if not usable and not items:
            return Outcome(state="blocked", reason="insufficient_evidence_temporary", draft=None,
                           cost=_no_cost("no model call: no usable evidence"), missing=missing, **base)
        cost, model_info, model_status = _no_cost("no model call"), None, "NOT_NEEDED"
        if model_topics:
            allowed, why = self.model_allowed()
            user = build_user(country["name"], category, {t: topics[t]["subcategory"] for t in model_topics}, passages, by_code)
            if allowed:
                res = spend.draft(SYSTEM_PROMPT, user, f"{cc}:{category}", validator=structural_valid,
                                  max_chars=cfg.limits_cfg["max_prompt_chars"] + len(SYSTEM_PROMPT),
                                  max_output_tokens=int(cfg.raw["spending"]["max_output_tokens"]))
                spend.checkpoint()
                row = spend.store.query("SELECT COALESCE(SUM(gross_cost_usd),0) c FROM usage")[0]
                cost = {"model": res.model, "calls": res.calls, "inputTokens": res.input_tokens, "outputTokens": res.output_tokens,
                        "estimatedUsd": round(row["c"], 6), "reservedMaxUsd": round(res.estimated_usd, 6),
                        "status": SPEND_NOTE if res.calls else ("cached result: no new call" if res.status == "cached" else res.status),
                        "gatewayStatus": res.status}
                if res.status in ("ok", "cached"):
                    g_items, g_missing, rejected, current = ground_claims(res.output, passages, evidence, set(model_topics), cfg.limits_cfg, today)
                    items += g_items
                    missing += g_missing
                    model_status, model_info = "DRAFTED", {"model": res.model, "promptVersion": PROMPT_VERSION,
                                                          "cached": res.status == "cached"}
                else:
                    model_status = "NOT_DRAFTED"
                    missing += [{"topic": t, "reason": "model drafting blocked: " + str(res.reason), "origin": "gateway"} for t in model_topics]
                    if not items:
                        reason = str(res.reason)
                        if reason == "max_model_calls_per_task":      # first answer and its corrective retry both failed validation
                            reason = "invalid_model_output"
                        return Outcome(state="blocked", reason=("budget:" if "allowance" in reason else "gateway:") + reason,
                                       draft=None, cost=cost, missing=missing, **base)
            else:
                est = _estimate_prompt(self, spend, user)
                model_status = "NOT_RUN"
                cost = _no_cost("no model call: " + why, est)
                missing += [{"topic": t, "reason": "not drafted: " + why.replace("_", " "), "origin": "gateway"} for t in model_topics]
                if not items:
                    return Outcome(state="blocked", reason=why, draft=None, cost=cost, missing=missing, **base)
        draft = self._assemble(data, inputs, country, evidence, passages, items, missing, rejected, current, model_status, model_info,
                               cost, fp, now)
        gate_reason = None
        if model_status in ("NOT_RUN", "NOT_DRAFTED"):
            gate_reason = "awaiting_paid_authorisation" if model_status == "NOT_RUN" else "gateway_blocked"
        return Outcome(state="blocked" if gate_reason else "completed", reason=gate_reason or "drafted", draft=draft, cost=cost,
                       missing=missing, **base)

    def _assemble(self, data, inputs, country, evidence, passages, items, missing, rejected, current, model_status, model_info, cost, fp, now):
        cfg, today = self.cfg, now.date()
        cc, category = inputs["countryId"], inputs["category"]
        tdefs = cfg.category_defs[category]["topics"]
        batch = build_candidate_batch(data, cc, category, tdefs, items, evidence, today, cfg.sources)
        ok, detail = validate_batch(self.data_path, batch)
        used = {x["passageId"] for it in items for c in it["claims"] for x in c["evidence"]} | \
               {x["passageId"] for c in current for x in c["evidence"]}
        draft_id = f"cib-{cc}-{category.lower()}-{fp[:10]}"
        out_items = []
        for it in items:
            out_items.append({**it, "content": item_content(it), "layer": "BASELINE",
                              "contentFreshness": "CURRENT" if any(c["temporal"] == "CURRENT" for c in it["claims"]) else "STABLE_REFERENCE"})
        draft = {
            "draftId": draft_id, "schemaVersion": "1.0", "agent": AGENT_NAME, "promptVersion": PROMPT_VERSION,
            "countryId": cc, "country": country["name"], "category": category, "gap": inputs["gap"],
            "generatedAt": iso(now), "evidenceFingerprint": fp,
            "reviewStatus": cfg.raw["review"]["initial_review_status"], "publicationStatus": "DRAFT", "publishable": False,
            "reviewNotice": "Draft only. Not independently verified, not reviewed and not published. data.json is unchanged.",
            "modelStatus": model_status, "model": model_info,
            "items": out_items, "missingEvidence": missing, "rejectedClaims": rejected,
            "currentContext": current,
            "evidence": [{k: v for k, v in e.items() if k not in ("passages", "structured")} |
                         {"passagesUsed": [p for p in e["passages"] if p["id"] in used]} for e in evidence],
            "candidateBatch": batch,
            "validation": {"candidateBatchMergesCleanly": ok, "detail": detail, "claimsKept": sum(len(i["claims"]) for i in items),
                           "claimsRejected": len(rejected), "everyQuoteVerbatim": True, "mergedIntoDataJson": False},
            "cost": cost,
        }
        if not ok:
            draft["reviewStatus"] = "VALIDATION_FAILED"
        return draft

    # -- the run --
    def run(self, now=None) -> int:
        cfg, out = self.cfg, self.out
        now = now or self.clock()
        if cfg.errors:
            out("ERROR: " + "; ".join(cfg.errors))
            return EXIT_CONFIG
        if cfg.mode == "off":
            out(f"{AGENT_NAME}: OFF ({cfg.mode_source}). Nothing runs. Set TI_CB_MODE to observe or draft.")
            return EXIT_OK
        try:
            state = _sections(self.backend.snapshot().state()) if self.backend else empty_state()
        except StateSyncError as exc:
            if cfg.mode == "draft":
                out(f"::error title=Country builder state::{exc}")
                return EXIT_STATE
            state = empty_state()
        data = self.load_data()
        gaps, planned = self.plan(data, now.date())
        summary = {"coverage": coverage_summary(data), "gaps": gaps}
        if cfg.mode == "observe":
            report = self._observe(state, data, planned, now, summary)
            out(report)
            self._step_summary(report)
            return EXIT_OK
        return self._draft_run(state, data, planned, now, summary)

    def _observe(self, state, data, planned, now, summary):
        """Read-only: shows what a draft run would do and checks the evidence is retrievable. No model, no writes."""
        rows = []
        for t in planned[: self.cfg.tasks_per_run]:
            existing = state["tasks"].get(t["dedupe_key"])
            if existing and existing["state"] in ("completed",):
                rows.append(f"- {t['inputs']['country']} / {t['inputs']['category']}: already {existing['state']} (draft {existing.get('draftId')}); nothing to do")
                continue
            o = self.process_task(t["inputs"], state, data, now)
            sources = ", ".join(f"{e['sourceKey']}={e['status']}" for e in o["evidence"])
            rows.append(f"- {t['inputs']['country']} / {t['inputs']['category']}: would draft; evidence {sources}; "
                        f"{len(o['passages'])} passages selected; model call {'would be' if self.model_topics_hint(o) else 'not'} needed; "
                        f"cost this run $0 (observe mode)")
        return "\n".join([f"# {AGENT_NAME}: OBSERVE", "",
                          f"Coverage: {summary['coverage']['covered']}/{summary['coverage']['combinations']} combinations covered "
                          f"({summary['coverage']['missing']} missing).",
                          f"Gaps in the configured scope ({', '.join(self.cfg.countries)} / {', '.join(self.cfg.categories)}): {len(summary['gaps'])}.",
                          "Observe mode reads and plans only: no queue entry, no draft, no model call, nothing written.", "", *rows])

    def model_topics_hint(self, outcome):
        return bool(outcome.get("passages"))

    def _draft_run(self, state, data, planned, now, summary):
        cfg, out = self.cfg, self.out
        batch_ids = []
        stats = {"created": 0, "duplicates": 0, "recovered": []}

        def plan_tx(snap):
            st = _sections(snap.state() or empty_state())
            for key, t in list(st["tasks"].items()):
                if t["state"] == "running" and t["lease_expires"] < iso(now):
                    t["state"] = "pending" if t["attempts"] < cfg.raw.get("max_attempts", 3) else "failed"
                    t["error"] = "interrupted: lease expired"
                    stats["recovered"].append(t["id"])
            if st["controls"].get("paused") or st["controls"].get("stopped"):
                return {"state": dump_state(st)}, st
            for t in planned:
                key = t["dedupe_key"]
                if key in st["tasks"] and st["tasks"][key]["state"] != "cancelled":
                    stats["duplicates"] += 1
                    continue
                st["tasks"][key] = {"id": "cbt-" + uuid.uuid4().hex[:12], "dedupe_key": key, "task_type": TASK_TYPE,
                                    "inputs": t["inputs"], "state": "pending", "attempts": 0, "created_at": iso(now),
                                    "updated_at": iso(now), "lease_owner": None, "lease_expires": None}
                stats["created"] += 1
            eligible = [t for t in st["tasks"].values() if t["state"] == "pending" or
                        (t["state"] == "blocked" and any((t.get("blockingReason") or "").startswith(p) for p in RETRY_BLOCKS)
                         and (self.model_allowed()[0] or not (t.get("blockingReason") or "").startswith(("awaiting", "budget"))))]
            eligible = [t for t in eligible if t["dedupe_key"] in {p["dedupe_key"] for p in planned}]
            order = {c: i for i, c in enumerate(cfg.categories)}
            eligible.sort(key=lambda t: (order.get(t["inputs"]["category"], 99), t["inputs"]["countryId"], t["created_at"], t["id"]))
            for t in eligible[: cfg.tasks_per_run]:
                t.update(state="running", attempts=t["attempts"] + 1, lease_owner=self.owner,
                         lease_expires=iso(now + timedelta(minutes=LEASE_MINUTES)), updated_at=iso(now))
                batch_ids.append(t["dedupe_key"])
            st["runs_pending"] = None
            return {"state": dump_state(st)}, st

        try:
            _, st = self.backend.transact(plan_tx, message=f"{AGENT_NAME}: plan {iso(now)}")
        except StateSyncError as exc:
            out(f"::error title=Country builder state::{exc}")
            return EXIT_STATE
        results = []
        for key in batch_ids:
            task = st["tasks"][key]
            spend = None
            try:
                spend = BuilderSpend(cfg.gateway_config(), st, workdir=self.workdir, provider=self.provider, backend=self.backend)
                outcome = self.process_task(task["inputs"], st, data, now, spend)
            except StateSyncError as exc:
                outcome = Outcome(state="pending", reason="state_sync:" + clean_text(exc, 120), draft=None, cost=_no_cost("state sync failed"),
                                  evidence=[], passages=[], fingerprint=None)
            except Exception as exc:   # bounded attempts; an unexpected error never loops
                retry = task["attempts"] < cfg.raw.get("max_attempts", 3)
                outcome = Outcome(state="pending" if retry else "failed", reason=f"{type(exc).__name__}: {clean_text(exc, 160)}",
                                  draft=None, cost=_no_cost("error"), evidence=[], passages=[], fingerprint=None)
            results.append((key, outcome, spend))
        try:
            report = self._persist(results, now, summary, stats)
        except StateSyncError as exc:
            out(f"::error title=Country builder state::{exc}")
            return EXIT_STATE
        out(report)
        self._step_summary(report)
        return EXIT_OK

    def _persist(self, results, now, summary, stats):
        cfg = self.cfg
        written = {}

        def fn(snap):
            st = _sections(snap.state() or empty_state())
            writes = {}
            for key, o, spend in results:
                t = st["tasks"].get(key)
                if t is None:
                    continue
                t.update(updated_at=iso(now), lease_owner=None, lease_expires=None, outcome=o["reason"],
                         cost_usd=(o["cost"] or {}).get("estimatedUsd", 0))
                if o["state"] == "pending":
                    t.update(state="pending", error=o["reason"])
                    continue
                t["state"] = o["state"]
                t["blockingReason"] = o["reason"] if o["state"] == "blocked" else None
                t["error"] = o["reason"] if o["state"] == "failed" else None
                d = o.get("draft")
                if o.get("existing"):
                    t["draftId"] = o["existing"]
                if d:
                    d = json.loads(json.dumps(d))
                    # Supersede earlier drafts for this country/category; keep them for audit.
                    for old in st["drafts"].values():
                        if old["countryId"] == d["countryId"] and old["category"] == d["category"] and not old.get("supersededBy") \
                                and old["draftId"] != d["draftId"]:
                            old["supersededBy"] = d["draftId"]
                    path = f"drafts/{d['countryId']}/{d['category']}/{d['draftId']}.json"
                    writes["file:" + path] = json.dumps(d, indent=2, ensure_ascii=False) + "\n"
                    st["drafts"][d["draftId"]] = {
                        "draftId": d["draftId"], "path": path, "countryId": d["countryId"], "category": d["category"],
                        "evidenceFingerprint": d["evidenceFingerprint"], "modelStatus": d["modelStatus"],
                        "reviewStatus": d["reviewStatus"], "createdAt": d["generatedAt"], "costUsd": d["cost"].get("estimatedUsd", 0),
                        "claims": d["validation"]["claimsKept"]}
                    t["draftId"] = d["draftId"]
                    written[d["draftId"]] = (d, path)
                if spend is not None:
                    spend.settle_into(st)
            keep = cfg.raw["retention"]["run_history_keep"]
            st["runs"] = (st["runs"] + [{"at": iso(now), "mode": cfg.mode, "tasksRun": len(results), "created": stats["created"],
                                         "duplicatesSuppressed": stats["duplicates"], "recovered": stats["recovered"],
                                         "outcomes": {k: o["reason"] for k, o, _ in results}}])[-keep:]
            self._prune(st, writes)
            report = self._render(st, summary, results, now, stats)
            writes["state"] = dump_state(st)
            writes["report"] = report
            return writes, report

        _, report = self.backend.transact(fn, message=f"{AGENT_NAME}: {iso(now)}")
        return report

    def _prune(self, st, writes):
        keep = self.cfg.raw["retention"]["superseded_drafts_keep"]
        superseded = sorted((d for d in st["drafts"].values() if d.get("supersededBy")), key=lambda d: d["createdAt"])
        for d in superseded[:-keep] if keep else superseded:
            st["drafts"].pop(d["draftId"], None)

    def _render(self, st, summary, results, now, stats):
        branch = self.cfg.state_branch or "ti-country-drafts"
        lines = [f"# {AGENT_NAME}", "", f"Run: {iso(now)} | mode: {self.cfg.mode} | paid execution: "
                 f"{'ON' if self.model_allowed()[0] else 'OFF (' + (self.model_allowed()[1] or '') + ')'}", "",
                 f"Coverage (whole product): {summary['coverage']['covered']}/{summary['coverage']['combinations']} combinations; "
                 f"gaps in scope: {len(summary['gaps'])}. Tasks this run: {len(results)}; new {stats['created']}; "
                 f"duplicates suppressed {stats['duplicates']}; interrupted tasks recovered {len(stats['recovered'])}.", ""]
        c = st["controls"]
        if c.get("paused") or c.get("stopped"):
            lines += [f"**Paused/stopped by operator** (paused={c.get('paused')}, stopped={c.get('stopped')}): nothing was started.", ""]
        spend = spend_section(json.loads(json.dumps(st)))
        lines += [f"Spend recorded (all time, USD): {spend.get('total', 0)} ({SPEND_NOTE}).", ""]
        for key, o, _ in results:
            t = st["tasks"][key]
            inp = t["inputs"]
            lines += [f"## {inp['country']} / {inp['category']}: {t['state'].upper()} ({o['reason']})", "",
                      f"- Gap addressed: {inp['gap']['type']}: {inp['gap']['detail']}",
                      f"- Evidence: " + ", ".join(f"{e['sourceKey']}={e['status']}" for e in o.get("evidence", [])) ,
                      f"- Cost this task: {_cost_line(o['cost'])}"]
            if o.get("draft"):
                lines += _preview(o["draft"], branch)
            elif o.get("existing"):
                lines += [f"- No new work: evidence unchanged since draft {o['existing']}; no model call."]
            lines.append("")
        lines += ["## Drafts held (not published)", ""]
        for d in sorted(st["drafts"].values(), key=lambda d: d["createdAt"], reverse=True)[:10]:
            lines.append(f"- {d['countryId']}/{d['category']}: {d['draftId']} | {d['reviewStatus']} | model: {d['modelStatus']} | "
                         f"claims {d['claims']} | {REPO_URL}/blob/{branch}/{d['path']}"
                         + (f" | superseded by {d['supersededBy']}" if d.get("supersededBy") else ""))
        return "\n".join(lines) + "\n"

    def _step_summary(self, text):
        if self.env.get("GITHUB_STEP_SUMMARY"):
            with open(self.env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
                fh.write(text + "\n")


# ---- helpers -------------------------------------------------------------------------------------------------
def _no_cost(why, estimate=None):
    return {"model": None, "calls": 0, "inputTokens": 0, "outputTokens": 0, "estimatedUsd": 0.0, "status": why,
            "wouldCostUsdIfRun": estimate}


def _estimate_prompt(builder, spend, user):
    try:
        from .budget import conservative_input_tokens, cost_usd
        g = builder.cfg.gateway_config()
        return round(cost_usd(g, "haiku", conservative_input_tokens(SYSTEM_PROMPT + user), g.max_output_tokens, conservative=True), 6)
    except Exception:
        return None


def _source_statuses(evidence, tdef):
    return ", ".join(f"{e['sourceKey']}: {e['status']}" for e in evidence if e["sourceKey"] in tdef["sources"]) or "no source configured"


def _cost_line(c):
    if not c.get("calls"):
        extra = f"; would cost about ${c['wouldCostUsdIfRun']} if run" if c.get("wouldCostUsdIfRun") is not None else ""
        return f"$0.00 ({c['status']}{extra})"
    return f"${c['estimatedUsd']} ({c['calls']} call(s), {c['inputTokens']} in / {c['outputTokens']} out tokens, {c['model']}; {c['status']})"


def _preview(d, branch):
    lines = [f"- Draft: {d['draftId']} | review status **{d['reviewStatus']}** | publication {d['publicationStatus']} | model: {d['modelStatus']}",
             f"- Link: {REPO_URL}/blob/{branch}/drafts/{d['countryId']}/{d['category']}/{d['draftId']}.json", ""]
    for it in d["items"]:
        lines.append(f"### {it['title']}  [{it['topic']}]")
        for c in it["claims"]:
            src = "; ".join(sorted({f"{x['publisher'].split(' (')[0]} ({x['passageId']})" for x in c["evidence"]})) or "no external source"
            lines.append(f"- {c['status']}: {c['text']}  _[{src}]_")
        lines.append(f"- Uncertainty: {it['uncertainty']}")
        lines.append("")
    if d["missingEvidence"]:
        lines.append("Missing evidence:")
        lines += [f"- {m['topic']}: {m['reason']}" for m in d["missingEvidence"]]
    if d["rejectedClaims"]:
        lines.append(f"Rejected claims: {len(d['rejectedClaims'])} ({', '.join(sorted({r['reason'] for r in d['rejectedClaims']}))})")
    return lines


def run_cli(env=None, root=None, out=print, **kw):
    env = os.environ if env is None else env
    root = Path(root or Path(__file__).resolve().parents[2])
    cfg = load_builder_config(env, root)
    create = cfg.mode == "draft" and not cfg.errors
    backend = kw.pop("backend", None) or (make_backend(env, root, cfg, create) if cfg.mode != "off" else None)
    if cfg.mode == "draft" and backend is None and not cfg.errors:
        cfg.errors.append("draft mode needs a state branch (TI_CB_STATE_BRANCH) or TI_CB_STATE_BACKEND=file")
    return Builder(cfg, root, backend, env=env, out=out, **kw).run()
