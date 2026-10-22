"""03AA-04_Country_Intelligence_Builder_TI: detect a country/category gap, collect permitted evidence, build claims
(ordinary code first, Haiku only for what code cannot cover), run the TI publication rules, and publish what passes.

Operating model (Marc, 22 Oct 2026): TI is an automated, rules-and-limits service. Content that passes the rules is
published automatically through the established compare-and-swap path to main and verified on the live site.
Only exceptions are held; each is recorded with its reason and routed through the Master Agent. A model call
needs changed evidence, mode draft or publish, and the spending gateway's reservation. Failed replacements never
disturb valid existing content.
"""
from __future__ import annotations

import json
import os
import socket
import time
import uuid
from datetime import timedelta
from pathlib import Path

from .country_config import AGENT_NAME, BuilderConfig, load_builder_config
from .country_draft import (PROMPT_VERSION, SYSTEM_PROMPT, build_candidate_batch, build_user, ground_claims, item_content,
                            structural_valid, validate_batch)
from .country_evidence import collect_category, evidence_fingerprint, http_get, select_passages
from .country_gaps import TASK_TYPE, coverage_summary, find_gaps, task_for
from .country_pipeline import assess_all, build_items, source_exceptions, topic_exceptions, unique_exceptions, verify_model_claims
from .country_publish import (PublishRefused, make_main_backend, publish_items, rollback_publication, release_is_ours, served_matches, served_without,
                              site_url_for, verify_live)
from .models import AnthropicProvider
from .monitor_bridge import MonitorSpend, spend_section
from .monitor_state import FileStateBackend, StateSyncError, dump_state
from .production import http_request
from .sanitise import clean_text
from .store import fingerprint, iso, utc_now

EXIT_OK, EXIT_CONFIG, EXIT_STATE, EXIT_PUBLICATION = 0, 3, 4, 5
LEASE_MINUTES = 20
SCHEMA = 1
REPO_URL = "https://github.com/smitblade-dot/Travel_Intelligence"
SPEND_NOTE = "estimated from API usage fields x configured list prices; not invoice-confirmed"
RETRY_BLOCKS = ("awaiting_paid_authorisation", "budget:", "insufficient_evidence_temporary", "gateway:provider_error")


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
            "tasks": {}, "drafts": {}, "runs": [], "exceptions": {}, "publications": {}, "pending": {}}


def _sections(state):
    state.setdefault("controls", {"paused": False, "stopped": False})
    for k, empty in (("tasks", {}), ("drafts", {}), ("runs", []), ("exceptions", {}), ("publications", {}), ("pending", {})):
        if not isinstance(state.get(k), type(empty)):
            state[k] = empty
    return state


class Outcome(dict):
    """What one task produced. Keys: state (completed|blocked|failed|pending), reason, draft, cost, evidence, ..."""


# ---- the builder ---------------------------------------------------------------------------------------------
class Builder:
    def __init__(self, cfg: BuilderConfig, root, backend, *, http=http_get, provider=None, clock=utc_now, env=None, out=print,
                 workdir=None, data_path=None, main_backend=None, site_http=http_request, sleep=time.sleep):
        self.cfg, self.root, self.backend, self.http = cfg, Path(root), backend, http
        self.clock, self.env, self.out, self.workdir = clock, os.environ if env is None else env, out, workdir
        self.owner = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.data_path = Path(data_path or self.root / "data.json")
        self.provider = provider
        self.main_backend, self.site_http, self.sleep = main_backend, site_http, sleep
        if provider is None and self.env.get("ANTHROPIC_API_KEY"):
            self.provider = AnthropicProvider(self.env["ANTHROPIC_API_KEY"])

    # -- planning --
    def load_data(self):
        return json.loads(self.data_path.read_text(encoding="utf-8"))

    def active_countries(self, state=None):
        """Validation scope until the proof gate is met AND expansion is switched on. -> (countries, gate)."""
        scope = self.cfg.raw["scope"]
        gate = scope_gate(state or empty_state(), scope.get("expansion", {}))
        return list(self.cfg.countries), gate

    def plan(self, data, today, state=None):
        scope = self.cfg.raw["scope"]
        countries, _ = self.active_countries(state)
        gaps = find_gaps(data, countries, self.cfg.categories, today, scope["stale_after_days_since_checked"], tuple(scope["gap_types"]))
        return gaps, [task_for(g, today, self.cfg.raw["schedule"]["recheck_after_days"]) | {"gap": g} for g in gaps]

    # -- model and publication preconditions --
    def model_allowed(self):
        """(allowed, reason). Dry-run and missing configuration never reach the provider."""
        if self.cfg.mode not in ("draft", "publish"):
            return False, "mode_is_" + self.cfg.mode
        if self.cfg.dry_run or not self.cfg.paid_allowance_configured:
            return False, "awaiting_paid_authorisation"
        if self.provider is None:
            return False, "provider_not_configured"
        return True, None

    def publication_allowed(self):
        """(allowed, why). Publication only from the production workflow on main; anything else fails closed."""
        pub = self.cfg.publication_cfg
        if not self.cfg.publication_enabled:
            return False, f"publication_off (mode {self.cfg.mode}, TI_CB_PUBLISH {self.cfg.publication_setting})"
        if self.env.get("GITHUB_EVENT_NAME") not in pub["allowed_events"] or self.env.get("GITHUB_REF") != pub["required_ref"]:
            return False, "publication_only_from_scheduled_or_manual_run_on_main"
        return True, None

    # -- one task --
    def process_task(self, inputs, state, data, now, spend=None) -> Outcome:
        cfg, today = self.cfg, now.date()
        cc, category = inputs["countryId"], inputs["category"]
        topics = cfg.category_defs[category]["topics"]
        country = next(c for c in data["countries"] if str(c["id"]).lower() == cc)
        evidence = collect_category(cfg, cc, category, self.http, now, data["sources"])
        passages, per_topic = select_passages(evidence, topics, cfg.limits_cfg)
        by_code = {e["code"]: e["publisher"] for e in evidence}
        reference = {k: country.get(k) for k in ("plugTypes", "voltage", "frequency")} if category == "EQUIPMENT" else None
        rules_fp = self.rules_fingerprint(category)
        fp = evidence_fingerprint(passages, evidence, list(topics), {"reference": reference, "rules": rules_fp,
                                                                      "all": [[e["sourceKey"], e["contentSha256"]] for e in evidence if e.get("contentSha256")]})
        base = {"evidence": evidence, "fingerprint": fp, "passages": passages}
        settled = [d for d in state["drafts"].values() if d["countryId"] == cc and d["category"] == category
                   and not d.get("supersededBy") and d.get("settled") and d["evidenceFingerprint"] == fp]
        if settled:
            return Outcome(state="completed", reason="unchanged_evidence", draft=None, existing=settled[0]["draftId"], exceptions=[],
                           cost=_no_cost("no model call: evidence and rules unchanged"), assessments=[], publish_items=[], pending=[], **base)
        pub_id = "pub-" + now.strftime("%Y%m%dT%H%M%SZ") + "-" + fp[:6]
        items, missing = build_items(self, inputs, country, evidence, passages, per_topic, topics, today, data)
        extractive = {it["topic"] for it in items if it.get("method") == "EXTRACTIVE"}
        model_topics = [t for t, d in topics.items() if d["kind"] == "model" and per_topic.get(t) and t not in extractive]
        for tname, tdef in topics.items():
            if tdef["kind"] == "model" and tname not in extractive and tname not in model_topics:
                missing.append({"topic": tname, "origin": "collector",
                                "reason": "no usable passage was retrieved for this topic (" + _source_statuses(evidence, tdef) + ")"})
        if not [e for e in evidence if e["status"] == "RETRIEVED"] and not items:
            return Outcome(state="blocked", reason="insufficient_evidence_temporary", draft=None, exceptions=source_exceptions(inputs, evidence),
                           cost=_no_cost("no model call: no usable evidence"), missing=missing, assessments=[], publish_items=[], pending=[], **base)
        cost, model_info, model_status, rejected, current, pending, verify_info = _no_cost("no model call"), None, "NOT_NEEDED", [], [], [], None
        if model_topics:
            allowed, why = self.model_allowed()
            user = build_user(country["name"], category, {t: topics[t]["subcategory"] for t in model_topics}, passages, by_code)
            if allowed:
                res = spend.draft(SYSTEM_PROMPT, user, f"{cc}:{category}", validator=structural_valid,
                                  max_chars=cfg.limits_cfg["max_prompt_chars"] + len(SYSTEM_PROMPT),
                                  max_output_tokens=int(cfg.raw["spending"]["max_output_tokens"]))
                spend.checkpoint()
                cost = self._cost(spend, res)
                if res.status in ("ok", "cached"):
                    g_items, g_missing, rejected, current = ground_claims(res.output, passages, evidence, set(model_topics), cfg.limits_cfg, today)
                    for gi in g_items:
                        for c in gi["claims"]:
                            c["method"] = "MODEL"
                        items.append(gi | {"method": "MODEL", "excluded": [], "candidateRecord": True})
                    missing += g_missing
                    model_status, model_info = "DRAFTED", {"model": res.model, "promptVersion": PROMPT_VERSION, "cached": res.status == "cached"}
                    if cfg.publication_enabled or cfg.raw["spending"].get("verifier_enabled") and cfg.mode == "publish":
                        verify_info = verify_model_claims(self, spend, items, passages, f"{cc}:{category}")
                        cost = self._cost(spend, res)
                else:
                    model_status = "NOT_DRAFTED"
                    reason = "invalid_model_output" if str(res.reason) == "max_model_calls_per_task" else str(res.reason)
                    pending = [{"topic": t, "reason": "model drafting blocked: " + reason} for t in model_topics]
                    missing += [{"topic": t, "reason": "model drafting blocked: " + reason, "origin": "gateway"} for t in model_topics]
                    if not items:
                        return Outcome(state="blocked", reason=("budget:" if "allowance" in reason else "gateway:") + reason, draft=None, cost=cost,
                                       missing=missing, exceptions=source_exceptions(inputs, evidence), assessments=[], publish_items=[],
                                       pending=pending, **base)
            else:
                model_status = "NOT_RUN"
                cost = _no_cost("no model call: " + why, _estimate_prompt(self, user))
                pending = [{"topic": t, "reason": why} for t in model_topics]
                missing += [{"topic": t, "reason": "not drafted: " + why.replace("_", " "), "origin": "gateway"} for t in model_topics]
        assessments, pub_items = assess_all(self, items, evidence, data, inputs, today, pub_id)
        exceptions = source_exceptions(inputs, evidence)
        for a in assessments:
            exceptions += topic_exceptions(inputs, a["topic"], a)
        exceptions = unique_exceptions(exceptions)
        draft = self._assemble(data, inputs, country, evidence, passages, items, missing, rejected, current, model_status, model_info, cost,
                               fp, now, assessments, pub_items, verify_info)
        gate_reason = "awaiting_paid_authorisation" if model_status == "NOT_RUN" and not pub_items else None
        return Outcome(state="blocked" if gate_reason else "completed", reason=gate_reason or "assessed", draft=draft, cost=cost, missing=missing,
                       assessments=assessments, publish_items=pub_items, exceptions=exceptions, pending=pending, pub_id=pub_id, **base)

    def rules_fingerprint(self, category):
        return fingerprint([self.cfg.publication_cfg, self.cfg.raw["categories"][category],
                            {k: self.cfg.sources[k].get("registry_id") for k in self.cfg.sources}, self.registry_fingerprint()])

    def registry_fingerprint(self):
        from .country_rules import repo_file
        p = repo_file(self.root, "config", "source_registry.json")
        return fingerprint(p.read_text(encoding="utf-8")) if p.exists() else None

    def _cost(self, spend, res):
        row = spend.store.query("SELECT COALESCE(SUM(gross_cost_usd),0) c, COUNT(*) n FROM usage")[0]
        return {"model": res.model, "calls": row["n"] or res.calls, "inputTokens": res.input_tokens, "outputTokens": res.output_tokens,
                "estimatedUsd": round(row["c"], 6), "reservedMaxUsd": round(res.estimated_usd, 6),
                "status": SPEND_NOTE if res.calls else ("cached result: no new call" if res.status == "cached" else res.status),
                "gatewayStatus": res.status}

    def _assemble(self, data, inputs, country, evidence, passages, items, missing, rejected, current, model_status, model_info, cost, fp, now,
                  assessments, pub_items, verify_info):
        cfg, today = self.cfg, now.date()
        cc, category = inputs["countryId"], inputs["category"]
        tdefs = cfg.category_defs[category]["topics"]
        batch = build_candidate_batch(data, cc, category, tdefs, items, evidence, today, cfg.sources)
        ok, detail = validate_batch(self.data_path, batch)
        used = {x["passageId"] for it in items for c in it["claims"] for x in c["evidence"]} | {x["passageId"] for c in current for x in c["evidence"]}
        draft_id = f"cib-{cc}-{category.lower()}-{fp[:10]}"
        decisions = {a["topic"]: a for a in assessments}
        out_items = []
        for it in items:
            a = decisions.get(it["topic"], {})
            kept = {c["text"] for c in a.get("claims", [])}
            why = {e.get("text", "")[:200]: (e.get("reasons") or [e.get("reason")]) for e in a.get("excluded", [])}
            claims = [{**c, "publishable": c["text"] in kept, "heldBecause": [] if c["text"] in kept else why.get(c["text"][:200], ["item_held"])}
                      for c in it["claims"]]
            out_items.append({**it, "claims": claims, "content": item_content(it), "layer": "BASELINE", "decision": a.get("decision"),
                              "heldReasons": a.get("reasons", [])})
        return {
            "draftId": draft_id, "schemaVersion": "2.0", "agent": AGENT_NAME, "promptVersion": PROMPT_VERSION,
            "countryId": cc, "country": country["name"], "category": category, "gap": inputs["gap"],
            "generatedAt": iso(now), "evidenceFingerprint": fp,
            "reviewStatus": "AWAITING_PUBLICATION_DECISION", "publicationStatus": "NOT_PUBLISHED",
            "modelStatus": model_status, "model": model_info, "verification": verify_info,
            "items": out_items, "missingEvidence": missing, "rejectedClaims": rejected, "currentContext": current,
            "assessment": [{"topic": a["topic"], "decision": a["decision"], "confidence": a.get("confidence"), "reasons": a["reasons"],
                            "claimsPublishable": len(a["claims"]), "excluded": a["excluded"][:12]} for a in assessments],
            "evidence": [{k: v for k, v in e.items() if k not in ("passages", "structured")} | {"passagesUsed": [p for p in e["passages"] if p["id"] in used]}
                         for e in evidence],
            "candidateBatch": batch, "publicationItems": [{"recordId": p["recordId"], "contentHash": p["contentHash"], "record": p["record"]} for p in pub_items],
            "validation": {"candidateBatchMergesCleanly": ok, "detail": detail, "claimsKept": sum(len(i["claims"]) for i in items),
                           "claimsRejected": len(rejected)},
            "cost": cost}

    # -- the run --
    def run(self, now=None) -> int:
        cfg, out = self.cfg, self.out
        now = now or self.clock()
        if cfg.errors:
            out("ERROR: " + "; ".join(cfg.errors))
            return EXIT_CONFIG
        if cfg.mode == "off":
            out(f"{AGENT_NAME}: OFF ({cfg.mode_source}). Nothing runs. Set TI_CB_MODE to observe, draft or publish.")
            return EXIT_OK
        try:
            state = _sections(self.backend.snapshot().state()) if self.backend else empty_state()
        except StateSyncError as exc:
            if cfg.mode in ("draft", "publish"):
                out(f"::error title=Country builder state::{exc}")
                return EXIT_STATE
            state = empty_state()
        data = self.load_data()
        gaps, planned = self.plan(data, now.date(), state)
        summary = {"coverage": coverage_summary(data), "gaps": gaps}
        if cfg.mode == "observe":
            report = self._observe(state, data, planned, now, summary)
            out(report)
            self._step_summary(report)
            return EXIT_OK
        return self._draft_run(state, data, planned, now, summary)

    def _observe(self, state, data, planned, now, summary):
        """Read-only: shows what a run would do and checks the evidence is retrievable. No model, no writes."""
        rows = []
        for t in planned[: self.cfg.tasks_per_run]:
            existing = state["tasks"].get(t["dedupe_key"])
            if existing and existing["state"] in ("completed",):
                rows.append(f"- {t['inputs']['country']} / {t['inputs']['category']}: already {existing['state']} (draft {existing.get('draftId')}); nothing to do")
                continue
            o = self.process_task(t["inputs"], state, data, now)
            rows += [f"- {t['inputs']['country']} / {t['inputs']['category']}: evidence " + ", ".join(f"{e['sourceKey']}={e['status']}" for e in o["evidence"])
                     + f"; {sum(1 for a in o['assessments'] if a['decision'] == 'PUBLISH')} topic(s) would pass the rules; "
                       f"{len(o['exceptions'])} exception(s); cost this run $0 (observe mode)"]
        return "\n".join([f"# {AGENT_NAME}: OBSERVE", "",
                          f"Coverage: {summary['coverage']['covered']}/{summary['coverage']['combinations']} combinations covered "
                          f"({summary['coverage']['missing']} missing).",
                          f"Gaps in the configured scope ({', '.join(self.cfg.countries)} / {', '.join(self.cfg.categories)}): {len(summary['gaps'])}.",
                          "Observe mode reads and plans only: no queue entry, no draft, no publication, no model call, nothing written.", "", *rows])

    def _draft_run(self, state, data, planned, now, summary):
        cfg, out = self.cfg, self.out
        batch_ids = []
        stats = {"created": 0, "reopened": 0, "duplicates": 0, "recovered": []}

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
                old = st["tasks"].get(key)
                if old and old["state"] == "completed" and self._needs_another_look(st, old):
                    # The gap is still open and the last result cannot be final: its draft never settled (for example a
                    # draft-mode trial that could not publish) or the rules/register have changed since. Settled work waits
                    # for its normal recheck bucket.
                    old.update(state="pending", attempts=0, updated_at=iso(now))
                    stats["reopened"] += 1
                    continue
                if old and old["state"] != "cancelled":
                    stats["duplicates"] += 1
                    continue
                st["tasks"][key] = {"id": "cbt-" + uuid.uuid4().hex[:12], "dedupe_key": key, "task_type": TASK_TYPE,
                                    "inputs": t["inputs"], "state": "pending", "attempts": 0, "created_at": iso(now),
                                    "updated_at": iso(now), "lease_owner": None, "lease_expires": None}
                stats["created"] += 1

            def retryable(t):
                r = t.get("blockingReason") or ""
                if t["state"] != "blocked" or not any(r.startswith(p) for p in RETRY_BLOCKS):
                    return False
                if r.startswith(("awaiting", "budget")):
                    return self.model_allowed()[0]                      # only once paid drafting is actually possible
                if r.startswith("gateway:provider_error"):
                    return t["attempts"] < cfg.raw.get("max_attempts", 3)   # transient provider failure: bounded retries
                return True
            eligible = [t for t in st["tasks"].values() if t["state"] == "pending" or retryable(t)]
            eligible = [t for t in eligible if t["dedupe_key"] in {p["dedupe_key"] for p in planned}]
            order = {c: i for i, c in enumerate(cfg.categories)}
            eligible.sort(key=lambda t: (order.get(t["inputs"]["category"], 99), t["inputs"]["countryId"], t["created_at"], t["id"]))
            for t in eligible[: cfg.tasks_per_run]:
                t.update(state="running", attempts=t["attempts"] + 1, lease_owner=self.owner,
                         lease_expires=iso(now + timedelta(minutes=LEASE_MINUTES)), updated_at=iso(now))
                batch_ids.append(t["dedupe_key"])
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
                outcome = _failed("pending", "state_sync:" + clean_text(exc, 120))
            except Exception as exc:   # bounded attempts; an unexpected error never loops
                retry = task["attempts"] < cfg.raw.get("max_attempts", 3)
                outcome = _failed("pending" if retry else "failed", f"{type(exc).__name__}: {clean_text(exc, 160)}")
            results.append((key, outcome, spend))
        # Pending verifications are read-only checks, so they run in draft mode too; only rollback needs permission to publish.
        rechecks = self.recheck_pending(now, can_rollback=self.publication_allowed()[0]) if cfg.mode in ("draft", "publish") else []
        publication = self._publish_phase(results, now)
        publication["rechecks"] = rechecks
        publication["recheck_failures"] = [dict(f, publicationId=r["publicationId"]) for r in rechecks for f in r.get("failures", [])]
        bad = sorted({f["reason"] for f in publication.get("failures", []) + publication["recheck_failures"] if f["reason"] in ALARM_REASONS})
        try:
            report = self._persist(results, now, summary, stats, publication)
        except StateSyncError as exc:
            out(f"::error title=Country builder state::{exc}")
            if bad:
                out(f"::error title=Country builder publication::{', '.join(bad)}")
            return EXIT_PUBLICATION if bad else EXIT_STATE
        out(report)
        self._step_summary(report)
        if bad:
            out(f"::error title=Country builder publication::{', '.join(bad)}: see the report; main or the live site may need attention")
            return EXIT_PUBLICATION
        return EXIT_OK

    # -- publication --
    def _needs_another_look(self, st, task):
        inp = task["inputs"]
        drafts = [d for d in st["drafts"].values() if d["countryId"] == inp["countryId"] and d["category"] == inp["category"]
                  and not d.get("supersededBy")]
        if not drafts:
            return True
        d = max(drafts, key=lambda d: d["createdAt"])
        pub = st["publications"].get(d.get("publicationId") or "", {})
        if pub.get("state") in ("VERIFICATION_PENDING", "ROLLBACK_PENDING"):
            return False                      # on main with verification pending: rechecked by the ledger, never republished
        return (not d.get("settled")) or d.get("rules") != self.rules_fingerprint(inp["category"])

    def _publish_phase(self, results, now):
        """Publish every item that passed the rules, verify the live site, roll back if it does not serve them."""
        items = [p for _, o, _ in results for p in (o.get("publish_items") or [])]
        res = {"attempted": False, "items": [i["recordId"] for i in items], "preview": [], "failures": []}
        if not items:
            return res
        allowed, why = self.publication_allowed()
        if not allowed:
            res.update(skipped=why, preview=[i["recordId"] for i in items])
            return res
        pub_cfg = self.cfg.publication_cfg
        ver = pub_cfg["verification"]
        pub_id = next(o["pub_id"] for _, o, _ in results if o.get("publish_items"))
        for p in items:                              # all items of one run share one publication id
            p["record"]["provenance"]["publicationId"] = pub_id
            for ob in p["observations"]:
                ob["publicationId"] = pub_id
        backend = self.main_backend or make_main_backend(self.root, pub_cfg["target_branch"])
        res.update(attempted=True, publicationId=pub_id)
        self._ledger(lambda st: st["publications"].__setitem__(pub_id, {"id": pub_id, "at": iso(now), "state": "PENDING", "records": res["items"]}))
        try:
            summary = publish_items(backend, items, pub_id, now, pub_cfg["max_records_per_run"], pub_cfg.get("refresh_days", 7))
        except PublishRefused as exc:
            res["failures"].append({"reason": "publication_refused", "detail": str(exc)[:300]})
            self._ledger(lambda st: st["publications"][pub_id].update(state="REFUSED", detail=str(exc)[:300]))
            return res
        except StateSyncError as exc:
            res["failures"].append({"reason": "publication_push_failed", "detail": str(exc)[:300]})
            self._ledger(lambda st: st["publications"][pub_id].update(state="PUSH_FAILED", detail=str(exc)[:300]))
            return res
        res["summary"] = summary
        changed = [i for i in items if i["recordId"] in summary["added"] + summary["updated"]]
        res["skipped_items"] = summary["skipped"]
        if not changed and not summary["refreshed"]:
            self._ledger(lambda st: st["publications"][pub_id].update(state="NO_CHANGE", summary=summary))
            return res
        url = site_url_for(self.root, pub_cfg)
        v = verify_live(url, self.site_http, lambda served: served_matches(served, changed) if changed else (True, []),
                        ver["timeout_seconds"], ver["interval_seconds"], sleep=self.sleep)
        res["verification"] = v
        if v["ok"]:
            res["published"] = [i["recordId"] for i in changed]
            self._ledger(lambda st: st["publications"][pub_id].update(state="VERIFIED", commit=summary.get("commit"), verifiedAt=iso(self.clock()),
                                                                     summary=summary, served=v))
            return res
        # The site did not serve the content inside the window. A late deployment is not bad content: only a site that is
        # demonstrably serving a release at or after ours, yet not our content, is treated as incorrect and rolled back.
        verify_items = [{"recordId": i["recordId"], "record": {"content": i["record"]["content"]}, "contentHash": i["contentHash"],
                         "needsSources": i["needsSources"], "observations": [{"id": o["id"]} for o in i["observations"]]} for i in changed]
        base = {"commit": summary.get("commit"), "generated": summary.get("generated"), "verifyItems": verify_items,
                "firstCheckedAt": iso(self.clock()), "rechecks": 0, "lastDetail": v.get("detail"), "servedGenerated": v.get("served_generated")}
        if release_is_ours(v, summary.get("generated")) and ver.get("rollback_on_incorrect_content"):
            res["failures"].append({"reason": "published_content_incorrect", "detail": v.get("detail")})
            self._rollback(res, backend, pub_id, url, ver, base)
            return res
        res["pending"] = True
        res["published"] = [i["recordId"] for i in changed]          # on main; the site has not served it yet
        res["verification_pending"] = {"records": [i["recordId"] for i in changed], "detail": v.get("detail")}
        try:
            self._ledger(lambda st: st["publications"][pub_id].update(state="VERIFICATION_PENDING", pending=base, summary=summary))
        except StateSyncError as exc:      # content is on main but the ledger could not record it: surface, do not lose it
            res["failures"].append({"reason": "verification_state_unrecorded", "detail": f"{summary.get('commit')}: {str(exc)[:200]}"})
        return res

    def _rollback(self, res, backend, pub_id, url, ver, base):
        try:
            rb = rollback_publication(backend, pub_id, self.clock())
            res["rollback"] = rb
            v2 = verify_live(url, self.site_http, lambda served: served_without(served, rb["removed"]) if rb["removed"] else (True, []),
                             ver.get("recheck_timeout_seconds", 120), ver["interval_seconds"], sleep=self.sleep)
            res["rollback"]["verification"] = v2
            base["rollbackRemoved"] = rb["removed"]
            if not v2["ok"]:
                res["failures"].append({"reason": "rollback_not_yet_served", "detail": v2.get("detail")})
        except (PublishRefused, StateSyncError) as exc:
            res["failures"].append({"reason": "rollback_failed", "detail": str(exc)[:300]})
        state = ("ROLLBACK_PENDING" if "rollback" in res and not res["rollback"]["verification"]["ok"]
                 else "ROLLED_BACK" if "rollback" in res else "VERIFICATION_FAILED")
        self._ledger(lambda st: st["publications"][pub_id].update(state=state, pending=base, detail=base.get("lastDetail"),
                                                                 commit=base.get("commit")))

    def recheck_pending(self, now, can_rollback=True):
        """Bounded rechecks of publications whose verification (or rollback) is pending. Read-only unless an incorrect
        release is demonstrated and publishing is allowed. Never deletes content for a slow deployment."""
        ver = self.cfg.publication_cfg["verification"]
        if "rollback_on_incorrect_content" not in ver:
            self.out("note: verification.rollback_on_incorrect_content is not set; incorrect releases will be reported but not rolled back")
        limit = ver.get("max_rechecks", 6)
        report = []
        try:
            st = _sections(self.backend.snapshot().state() or empty_state())
        except StateSyncError:
            return report
        backend = self.main_backend or make_main_backend(self.root, self.cfg.publication_cfg["target_branch"])
        for pub_id, p in list(st["publications"].items()):
            if p.get("state") not in ("VERIFICATION_PENDING", "ROLLBACK_PENDING"):
                continue
            pend, url = p["pending"], site_url_for(self.root, self.cfg.publication_cfg)
            if p["state"] == "ROLLBACK_PENDING":
                removed = pend.get("rollbackRemoved") or []
                v = verify_live(url, self.site_http, lambda served: served_without(served, removed) if removed else (True, []),
                                ver.get("recheck_timeout_seconds", 120), ver["interval_seconds"], sleep=self.sleep)
                outcome = "rollback_verified" if v["ok"] else None
                if v["ok"]:
                    self._ledger(lambda s2: s2["publications"][pub_id].update(state="ROLLED_BACK", pending=None, served=v))
                    report.append({"publicationId": pub_id, "result": outcome})
                    continue
                items = None
            else:
                try:
                    main = backend.snapshot().data() or {}
                except StateSyncError:
                    main = {}
                recs = {r.get("id"): r for r in main.get("records", [])}
                items = [i for i in pend["verifyItems"]
                         if (recs.get(i["recordId"], {}).get("provenance") or {}).get("publicationId") == pub_id
                         and (recs[i["recordId"]].get("provenance") or {}).get("contentHash") == i["contentHash"]]
                if not items:                          # replaced by a later publication or an edit: nothing of ours left to verify
                    self._ledger(lambda s2: s2["publications"][pub_id].update(state="SUPERSEDED", pending=None))
                    report.append({"publicationId": pub_id, "result": "superseded"})
                    continue
                v = verify_live(url, self.site_http, lambda served: served_matches(served, items), ver.get("recheck_timeout_seconds", 120),
                                ver["interval_seconds"], sleep=self.sleep)
                if v["ok"]:
                    self._ledger(lambda s2: s2["publications"][pub_id].update(state="VERIFIED", verifiedAt=iso(self.clock()), served=v, pending=None))
                    report.append({"publicationId": pub_id, "result": "verified", "records": p["records"], "verification": v})
                    continue
            n = pend["rechecks"] + 1
            pend.update(rechecks=n, lastDetail=v.get("detail"), servedGenerated=v.get("served_generated"), lastCheckedAt=iso(self.clock()))
            res = {"failures": []}
            if items is not None and release_is_ours(v, pend.get("generated")) and ver.get("rollback_on_incorrect_content") and can_rollback:
                res["failures"].append({"reason": "published_content_incorrect", "detail": v.get("detail")})
                self._rollback(res, backend, pub_id, url, ver, pend)
                report.append({"publicationId": pub_id, "result": "rolled_back_incorrect_content", "failures": res["failures"]})
            elif n >= limit:
                reason = "verification_unresolved" if items is not None else "rollback_not_yet_served"
                self._ledger(lambda s2: s2["publications"][pub_id].update(state="VERIFICATION_FAILED" if items is not None else "ROLLBACK_PENDING", pending=pend))
                report.append({"publicationId": pub_id, "result": "unresolved_after_rechecks", "detail": v.get("detail"),
                               "failures": [{"reason": reason, "detail": v.get("detail")}]})
            else:
                self._ledger(lambda s2: s2["publications"][pub_id].update(pending=pend))
                report.append({"publicationId": pub_id, "result": "still_pending", "rechecks": n, "detail": v.get("detail")})
        return report

    def _ledger(self, mutate):
        def fn(snap):
            st = _sections(snap.state() or empty_state())
            mutate(st)
            return {"state": dump_state(st)}, None
        self.backend.transact(fn, message=f"{AGENT_NAME}: publication ledger")

    def _persist(self, results, now, summary, stats, publication):
        cfg = self.cfg

        def fn(snap):
            st = _sections(snap.state() or empty_state())
            writes = {}
            published = set(publication.get("published", []))
            failed_pub = bool(publication.get("failures"))
            for key, o, spend in results:
                t = st["tasks"].get(key)
                if t is None:
                    continue
                t.update(updated_at=iso(now), lease_owner=None, lease_expires=None, outcome=o["reason"], cost_usd=(o["cost"] or {}).get("estimatedUsd", 0))
                if o["state"] == "pending":
                    t.update(state="pending", error=o["reason"])
                    continue
                d = o.get("draft")
                eligible = [p["recordId"] for p in (o.get("publish_items") or [])]
                unpublished = [r for r in eligible if r not in published and r not in (publication.get("summary") or {}).get("unchanged", [])]
                held = [a for a in (o.get("assessments") or []) if a["decision"] == "HOLD"]
                t["state"] = o["state"]
                t["blockingReason"] = o["reason"] if o["state"] == "blocked" else None
                t["error"] = o["reason"] if o["state"] == "failed" else None
                t["published"] = sorted(published & set(eligible))
                t["held"] = [a["topic"] for a in held]
                if o.get("existing"):
                    t["draftId"] = o["existing"]
                if d:
                    d = json.loads(json.dumps(d))
                    d["publicationStatus"] = "PUBLISHED" if published & set(eligible) else ("NOT_PUBLISHED" if unpublished else "NOTHING_TO_PUBLISH")
                    d["reviewStatus"] = ("AUTO_PUBLISHED_VERIFICATION_PENDING" if published & set(eligible) and publication.get("pending") and not failed_pub else
                                         "AUTO_PUBLISHED_AND_VERIFIED" if published & set(eligible) and not failed_pub else
                                         "PUBLICATION_FAILED_ROLLED_BACK" if failed_pub else
                                         "HELD_EXCEPTIONS_ROUTED" if held else
                                         "ELIGIBLE_AWAITING_PUBLICATION_RUN" if unpublished else "NO_PUBLISHABLE_CHANGE")
                    d["publication"] = {"publicationId": publication.get("publicationId"), "recordIds": sorted(published & set(eligible)),
                                        "verification": publication.get("verification"), "skipped": publication.get("skipped")}
                    for old in st["drafts"].values():
                        if old["countryId"] == d["countryId"] and old["category"] == d["category"] and not old.get("supersededBy") \
                                and old["draftId"] != d["draftId"]:
                            old["supersededBy"] = d["draftId"]
                    path = f"drafts/{d['countryId']}/{d['category']}/{d['draftId']}.json"
                    writes["file:" + path] = json.dumps(d, indent=2, ensure_ascii=False) + "\n"
                    st["drafts"][d["draftId"]] = {
                        "draftId": d["draftId"], "path": path, "countryId": d["countryId"], "category": d["category"],
                        "evidenceFingerprint": d["evidenceFingerprint"], "modelStatus": d["modelStatus"], "reviewStatus": d["reviewStatus"],
                        "createdAt": d["generatedAt"], "costUsd": d["cost"].get("estimatedUsd", 0), "claims": d["validation"]["claimsKept"],
                        "rules": self.rules_fingerprint(d["category"]), "publicationId": publication.get("publicationId") if published & set(eligible) else None,
                        "settled": bool(not unpublished and not failed_pub and not o.get("pending") and not publication.get("pending")),
                        "published": sorted(published & set(eligible))}
                    t["draftId"] = d["draftId"]
                self._record_exceptions(st, o, key, now, unpublished_failures=failed_pub and bool(eligible), publication=publication)
                for rec in (publication.get("summary") or {}).get("skipped", []):
                    self._upsert_exception(st, {"key": f"pub:{rec['recordId']}:{rec['reason']}", "scope": "publication", "countryId": t["inputs"]["countryId"],
                                                "category": t["inputs"]["category"], "topic": None, "reasons": [rec["reason"].split(":")[0]],
                                                "detail": rec["recordId"], "action": ACTION_FOR.get(rec["reason"].split(":")[0], rec["reason"]),
                                                "requiresHuman": rec["reason"].startswith("existing")}, now)
                if spend is not None:
                    spend.settle_into(st)
            for f in publication.get("recheck_failures", []):
                self._upsert_exception(st, {"key": f"publication:{f['publicationId']}:{f['reason']}", "scope": "publication", "countryId": None,
                                            "category": None, "topic": None, "reasons": [f["reason"]], "detail": f.get("detail"),
                                            "action": ACTION_FOR.get(f["reason"], f["reason"]), "requiresHuman": True}, now)
            self._update_runs(st, results, now, stats, publication)
            self._prune(st, writes)
            report = self._render(st, summary, results, now, stats, publication)
            writes["state"] = dump_state(st)
            writes["report"] = report
            return writes, report

        _, report = self.backend.transact(fn, message=f"{AGENT_NAME}: {iso(now)}")
        return report

    def _update_runs(self, st, results, now, stats, publication):
        keep = self.cfg.raw["retention"]["run_history_keep"]
        st["runs"] = (st["runs"] + [{"at": iso(now), "mode": self.cfg.mode, "tasksRun": len(results), "created": stats["created"],
                                     "reopened": stats["reopened"], "duplicatesSuppressed": stats["duplicates"], "recovered": stats["recovered"],
                                     "published": publication.get("published", []), "publicationFailures": publication.get("failures", []),
                                     "outcomes": {k: o["reason"] for k, o, _ in results}}])[-keep:]

    # -- exceptions: held with a reason, routed through the Master Agent --
    def _record_exceptions(self, st, o, key, now, unpublished_failures, publication):
        inp = st["tasks"][key]["inputs"]
        scope_prefix = f"{inp['countryId']}:{inp['category']}:"
        current = {e["key"] for e in (o.get("exceptions") or [])}
        for e in o.get("exceptions") or []:
            self._upsert_exception(st, e, now)
        for f in publication.get("failures", []) if unpublished_failures else []:
            self._upsert_exception(st, {"key": scope_prefix + "publication:" + f["reason"], "scope": "publication", "countryId": inp["countryId"],
                                        "category": inp["category"], "topic": None, "reasons": [f["reason"]], "detail": f.get("detail"),
                                        "action": ACTION_FOR.get(f["reason"], f["reason"]), "requiresHuman": True}, now)
        if o["reason"] not in ("unchanged_evidence",):
            for k, ex in st["exceptions"].items():     # an exception not seen again for this task is resolved
                if k.startswith(scope_prefix) and ex["state"] == "HELD" and k not in current and ex["scope"] != "publication":
                    ex.update(state="RESOLVED", resolvedAt=iso(now))
        for p in o.get("pending") or []:
            st["pending"][scope_prefix + p["topic"]] = {"countryId": inp["countryId"], "category": inp["category"], "topic": p["topic"],
                                                         "reason": p["reason"], "since": st["pending"].get(scope_prefix + p["topic"], {}).get("since", iso(now))}
        for topic in list(st["pending"]):
            if topic.startswith(scope_prefix) and not any(scope_prefix + p["topic"] == topic for p in (o.get("pending") or [])) \
                    and o["reason"] != "unchanged_evidence":
                st["pending"].pop(topic)

    def _upsert_exception(self, st, e, now):
        cur = st["exceptions"].get(e["key"])
        if cur and cur["state"] == "HELD":
            cur.update(lastSeen=iso(now), occurrences=cur["occurrences"] + 1, detail=e.get("detail"))
        else:
            st["exceptions"][e["key"]] = {**e, "id": "cbx-" + fingerprint(e["key"])[:10], "state": "HELD", "firstSeen": iso(now),
                                          "lastSeen": iso(now), "occurrences": 1}

    def _prune(self, st, writes):
        days = self.cfg.raw["retention"]["closed_task_days"]
        cutoff = iso(self.clock() - timedelta(days=days))
        for key in [k for k, t in st["tasks"].items() if t["state"] in ("completed", "failed", "cancelled") and t["updated_at"] < cutoff]:
            del st["tasks"][key]
        for key in [k for k, e in st["exceptions"].items() if e["state"] == "RESOLVED" and e.get("resolvedAt", "") < cutoff]:
            del st["exceptions"][key]
        keep = self.cfg.raw["retention"]["superseded_drafts_keep"]
        superseded = sorted((d for d in st["drafts"].values() if d.get("supersededBy")), key=lambda d: d["createdAt"])
        for d in superseded[:-keep] if keep else superseded:
            st["drafts"].pop(d["draftId"], None)

    def _render(self, st, summary, results, now, stats, publication):
        branch = self.cfg.state_branch or "ti-country-drafts"
        pa = self.publication_allowed()
        lines = [f"# {AGENT_NAME}", "", f"Run: {iso(now)} | mode: {self.cfg.mode} | automatic publication: "
                 f"{'ON' if pa[0] else 'OFF (' + pa[1] + ')'} | paid model use: {'ON' if self.model_allowed()[0] else 'OFF (' + (self.model_allowed()[1] or '') + ')'}", "",
                 f"Coverage (whole product): {summary['coverage']['covered']}/{summary['coverage']['combinations']} combinations; "
                 f"gaps in scope: {len(summary['gaps'])}. Tasks this run: {len(results)}; new {stats['created']}; reopened {stats['reopened']}; "
                 f"duplicates suppressed {stats['duplicates']}; interrupted tasks recovered {len(stats['recovered'])}.", ""]
        c = st["controls"]
        if c.get("paused") or c.get("stopped"):
            lines += [f"**Paused/stopped by operator** (paused={c.get('paused')}, stopped={c.get('stopped')}): nothing was started.", ""]
        spend = spend_section(json.loads(json.dumps(st)))
        lines += [f"Spend recorded (all time, USD): {spend.get('total', 0)} ({SPEND_NOTE}).", ""]
        lines += self._render_publication(publication, branch)
        for key, o, _ in results:
            t = st["tasks"][key]
            inp = t["inputs"]
            lines += [f"## {inp['country']} / {inp['category']}: {t['state'].upper()} ({o['reason']})", "",
                      f"- Gap addressed: {inp['gap']['type']}: {inp['gap']['detail']}",
                      "- Evidence: " + ", ".join(f"{e['sourceKey']}={e['status']}" + (f" ({e['error']})" if e["status"] != "RETRIEVED" and e.get("error") else "")
                                                for e in o.get("evidence", [])),
                      f"- Cost this task: {_cost_line(o['cost'])}"]
            for a in o.get("assessments") or []:
                lines.append(f"- {a['topic']}: **{a['decision']}**" + (f" ({', '.join(a['reasons'])})" if a["reasons"] else "")
                             + (f", confidence {a['confidence']}, {len(a['claims'])} claim(s)" if a["decision"] == "PUBLISH" else ""))
            if o.get("draft"):
                lines += _preview(o["draft"], branch)
            elif o.get("existing"):
                lines += [f"- No new work: evidence and rules unchanged since draft {o['existing']}; no model call."]
            lines.append("")
        open_ex = [e for e in st["exceptions"].values() if e["state"] == "HELD"]
        lines += ["## Exceptions held (routed to the Master Agent)", ""]
        lines += [f"- [{'NEEDS DECISION' if e['requiresHuman'] else 'automatic retry'}] {e['countryId']}/{e['category']}"
                  f"{'/' + e['topic'] if e.get('topic') else ''}: {', '.join(e['reasons'])}. {e['action']} (seen {e['occurrences']}x since {e['firstSeen'][:10]})"
                  for e in open_ex] or ["- none"]
        lines += ["", "## Awaiting budget authorisation (model-written topics; not exceptions)", ""]
        lines += [f"- {p['countryId']}/{p['category']}/{p['topic']}: {p['reason']} (since {p['since'][:10]})" for p in st["pending"].values()] or ["- none"]
        _, gate = self.active_countries(st)
        lines += ["", "## Scope", "", f"- Validation scope: {', '.join(self.cfg.countries)} / {', '.join(self.cfg.categories)}. Expansion: "
                  f"{'ON' if self.cfg.raw['scope'].get('expansion', {}).get('enabled') else 'OFF (needs confirmation of coverage and budget)'}; proof gate: "
                  f"{'MET' if gate['open'] else 'not met'} ({gate['detail']})", "", "## Records published by this agent", ""]
        pubs = [p for p in st["publications"].values() if p["state"] == "VERIFIED"]
        lines += [f"- {p['id']}: {', '.join(p['records'])} | commit {p.get('commit')} | verified live {p.get('verifiedAt')}" for p in pubs[-10:]] or ["- none yet"]
        lines += ["", "## Drafts and assessments held on this branch", ""]
        for d in sorted(st["drafts"].values(), key=lambda d: d["createdAt"], reverse=True)[:10]:
            lines.append(f"- {d['countryId']}/{d['category']}: {d['draftId']} | {d['reviewStatus']} | model: {d['modelStatus']} | "
                         f"claims {d['claims']} | {REPO_URL}/blob/{branch}/{d['path']}"
                         + (f" | superseded by {d['supersededBy']}" if d.get("supersededBy") else ""))
        return "\n".join(lines) + "\n"

    def _render_publication(self, pub, branch):
        if not pub["items"]:
            return ["## Publication", "", "- Nothing passed the rules in this run (see the exceptions below).", ""]
        if pub.get("skipped"):
            return ["## Publication", "", f"- NOT published: {pub['skipped']}. Would publish: {', '.join(pub['preview'])}.", ""]
        out = ["## Publication", ""]
        if pub.get("published") and pub["verification"]["ok"]:
            v = pub["verification"]
            out += [f"- PUBLISHED and verified on the live site: {', '.join(pub['published'])}",
                    f"- Commit: {REPO_URL}/commit/{pub['summary'].get('commit')} | live check: {v['url']}data.json served the new content after {v['attempts']} check(s); "
                    f"served data generated {v.get('served_generated')}"]
        if pub.get("verification_pending"):
            vp = pub["verification_pending"]
            out.append(f"- PUBLISHED to main, live verification PENDING (deployment not yet visible; content not judged wrong, nothing removed): "
                       f"{', '.join(vp['records'])} | {vp['detail']}")
        for r in pub.get("rechecks", []):
            out.append(f"- Recheck of {r['publicationId']}: {r['result']}" + (f" ({r.get('detail')})" if r.get("detail") else ""))
        for f in pub.get("failures", []):
            out.append(f"- FAILED: {f['reason']}: {f.get('detail')}")
        if pub.get("rollback"):
            out.append(f"- Rolled back: {pub['rollback']}")
        s = pub.get("summary") or {}
        if s.get("unchanged"):
            out.append(f"- Already published and unchanged (duplicate prevented): {', '.join(s['unchanged'])}")
        if s.get("refreshed"):
            out.append(f"- Re-verified, check date refreshed: {', '.join(s['refreshed'])}")
        for sk in s.get("skipped", []):
            out.append(f"- Not written: {sk['recordId']} ({sk['reason']})")
        out.append("")
        return out

    def _step_summary(self, text):
        if self.env.get("GITHUB_STEP_SUMMARY"):
            with open(self.env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
                fh.write(text + "\n")


ALARM_REASONS = {"published_content_incorrect", "rollback_failed", "rollback_not_yet_served", "verification_unresolved",
                 "publication_push_failed", "publication_refused", "verification_state_unrecorded"}
ACTION_FOR = {
    "verification_state_unrecorded": "The publication is on main but the ledger could not record its pending verification; check the commit named in the detail.",
    "published_content_incorrect": "The site is serving a release at least as new as the publication but not its content; a rollback was attempted (see the report).",
    "rollback_not_yet_served": "The rollback was committed but the site has not served it yet; recheck the site.",
    "verification_unresolved": "The site still does not serve the published content after the bounded rechecks. Nothing was deleted; check the GitHub Pages deployment.",
    "publication_refused": "The candidate failed canonical validation or a guard; nothing was pushed. Investigate the detail.",
    "publication_push_failed": "Pushing to main failed; retried next run.",
    "rollback_failed": "Rollback failed. Investigate main immediately.",
    "existing_record_not_owned": "A hand-written or earlier record already uses this id; it was not touched.",
    "max_records_per_run": "Per-run publication limit reached; the rest publish next run.",
}


def scope_gate(state, expansion):
    """Has the validation scope proved the path? Published and live-verified records, no rollbacks, clean runs."""
    gate = expansion.get("gate", {})
    pubs = [p for p in state.get("publications", {}).values()]
    verified = sum(len(p["records"]) for p in pubs if p["state"] == "VERIFIED")
    rolled = sum(1 for p in pubs if p["state"] in ("ROLLED_BACK", "VERIFICATION_FAILED"))
    clean = sum(1 for p in pubs if p["state"] == "VERIFIED")
    ok = (verified >= gate.get("min_verified_on_live_site", 3) and rolled <= gate.get("max_rollbacks", 0)
          and clean >= gate.get("min_clean_publication_runs", 2))
    return {"open": ok, "detail": f"{verified} record(s) verified live (need {gate.get('min_verified_on_live_site', 3)}), {rolled} rollback(s) "
                                  f"(max {gate.get('max_rollbacks', 0)}), {clean} clean publication run(s) (need {gate.get('min_clean_publication_runs', 2)})"}


# ---- helpers -------------------------------------------------------------------------------------------------
def _failed(state, reason):
    return Outcome(state=state, reason=reason, draft=None, cost=_no_cost("error"), evidence=[], passages=[], fingerprint=None,
                   assessments=[], publish_items=[], exceptions=[], pending=[])


def _no_cost(why, estimate=None):
    return {"model": None, "calls": 0, "inputTokens": 0, "outputTokens": 0, "estimatedUsd": 0.0, "status": why,
            "wouldCostUsdIfRun": estimate}


def _estimate_prompt(builder, user):
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
    lines = [f"- Draft: {d['draftId']} | status **{d['reviewStatus']}** | publication {d['publicationStatus']} | model: {d['modelStatus']}",
             f"- Link: {REPO_URL}/blob/{branch}/drafts/{d['countryId']}/{d['category']}/{d['draftId']}.json", ""]
    for it in d["items"]:
        lines.append(f"### {it['title']}  [{it['topic']}] {it.get('decision') or ''}")
        for c in it["claims"][:8]:
            src = "; ".join(sorted({f"{x['publisher'].split(' (')[0]} ({x['passageId']})" for x in c["evidence"]})) or "no external source"
            tag = "PUBLISHABLE" if c.get("publishable") else "HELD (" + ", ".join(c.get("heldBecause") or ["n/a"]) + ")" if "publishable" in c else c["status"]
            lines.append(f"- {tag}: {c['text']}  _[{src}]_")
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
    create = cfg.mode in ("draft", "publish") and not cfg.errors
    backend = kw.pop("backend", None) or (make_backend(env, root, cfg, create) if cfg.mode != "off" else None)
    if cfg.mode in ("draft", "publish") and backend is None and not cfg.errors:
        cfg.errors.append("draft and publish modes need a state branch (TI_CB_STATE_BRANCH) or TI_CB_STATE_BACKEND=file")
    return Builder(cfg, root, backend, env=env, out=out, **kw).run()
