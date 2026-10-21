"""Model routing and the single gateway through which every paid call must pass."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.request import Request, urlopen

from .budget import Budget, BudgetBlocked, conservative_input_tokens, cost_usd
from .sanitise import clean_text
from .store import Store, fingerprint

SONNET_REASONS = {
    "conflicting_evidence", "complex_investigation", "failed_corrective_haiku",
    "representative_testing_exceeds_haiku",
}


class ModelNotPermitted(Exception):
    pass


@dataclass
class RouteDecision:
    tier: str
    model: str
    escalated: bool
    reason: Optional[str]


def route(cfg, *, task_type, escalation_reason=None, want_tier="haiku"):
    """Cheapest option first. Haiku default; Sonnet needs a listed reason; Opus needs explicit approval."""
    if want_tier == "haiku":
        return RouteDecision("haiku", cfg.models["haiku"], False, None)
    if want_tier == "sonnet":
        if escalation_reason not in SONNET_REASONS:
            raise ModelNotPermitted("sonnet_requires_logged_escalation_reason")
        return RouteDecision("sonnet", cfg.models["sonnet"], True, escalation_reason)
    if want_tier == "opus":
        a = cfg.opus_approval or {}
        if not (a.get("approved_by") == "Marc" and a.get("task_type") == task_type
                and (a.get("allowance_usd") or 0) > 0):
            raise ModelNotPermitted("opus_disabled_without_explicit_approval")
        return RouteDecision("opus", cfg.models["opus"], True, escalation_reason or "approved_by_marc")
    raise ModelNotPermitted("unknown_tier")


class Provider:
    """Interface: complete(model, system, user, max_tokens, timeout) -> dict(text,input_tokens,output_tokens)."""
    def complete(self, **kw):  # pragma: no cover
        raise NotImplementedError


class AnthropicProvider(Provider):
    """Real provider. Never used by tests; needs ANTHROPIC_API_KEY from the environment."""
    def __init__(self, api_key):
        self.api_key = api_key

    def complete(self, *, model, system, user, max_tokens, timeout=30, options=None):
        body = {"model": model, "max_tokens": max_tokens, "system": system,
                "messages": [{"role": "user", "content": user}]}
        body.update(options or {})        # per-model compatibility settings from configuration
        req = Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(), method="POST",
                      headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                               "content-type": "application/json"})
        with urlopen(req, timeout=timeout) as r:
            ans = json.loads(r.read(1_000_000))
        text = "".join(b.get("text", "") for b in ans.get("content", []) if b.get("type") == "text")
        u = ans.get("usage", {})
        return {"text": text, "input_tokens": int(u.get("input_tokens", 0)),
                "output_tokens": int(u.get("output_tokens", 0)), "stop_reason": ans.get("stop_reason")}


@dataclass
class GatewayResult:
    status: str                      # ok | cached | dry_run | blocked
    output: Optional[dict] = None
    reason: Optional[str] = None
    model: Optional[str] = None
    estimated_usd: float = 0.0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class ModelGateway:
    def __init__(self, store: Store, cfg, provider: Optional[Provider] = None):
        self.store, self.cfg, self.provider = store, cfg, provider
        self.budget = Budget(store, cfg)

    def _calls_used(self, task_id):
        return self.store.query("SELECT COUNT(*) n FROM reservations WHERE task_id=? AND state!='released'", (task_id,))[0]["n"]

    def _estimate(self, tier, text, max_out):
        return cost_usd(self.cfg, tier, conservative_input_tokens(text), max_out, conservative=True)

    def run(self, *, task_id, worker, task_type, system, user, validator: Callable[[dict], bool],
            escalation_reason=None, want_tier="haiku", max_chars=20000, max_output_tokens=None) -> GatewayResult:
        cfg, store = self.cfg, self.store
        if len(user) > max_chars:      # refuse rather than silently truncating structured input
            return GatewayResult("blocked", reason="input_too_large")
        user = clean_text(user, max_chars)
        max_out = min(max_output_tokens or cfg.max_output_tokens, cfg.max_output_tokens)
        if conservative_input_tokens(system + user) > cfg.max_input_tokens_per_call:
            return GatewayResult("blocked", reason="input_too_large")
        # Reuse valid previous result for identical content: no new work, no model call.
        fp = fingerprint([task_type, system, user])
        cached = store.query("SELECT result FROM results_cache WHERE fingerprint=?", (fp,))
        if cached:
            return GatewayResult("cached", json.loads(cached[0]["result"]))
        try:
            decision = route(cfg, task_type=task_type, escalation_reason=escalation_reason, want_tier=want_tier)
            est = self._estimate(decision.tier, system + user, max_out)
        except ModelNotPermitted as exc:
            return GatewayResult("blocked", reason=str(exc))
        except BudgetBlocked as exc:
            return GatewayResult("blocked", reason=exc.reason)
        if cfg.dry_run:
            with store.tx() as db:
                store.event(db, task_id, "dry_run_model_call_skipped",
                            {"model": decision.model, "estimated_usd": round(est, 6)})
            return GatewayResult("dry_run", reason="dry_run", model=decision.model, estimated_usd=est)
        if self.provider is None:
            return GatewayResult("blocked", reason="provider_not_configured")
        calls = tok_in = tok_out = 0
        tier_now, escalation_now, corrective_used = decision, escalation_reason, False
        while True:
            if self._calls_used(task_id) >= cfg.max_model_calls_per_task:
                return GatewayResult("blocked", reason="max_model_calls_per_task", calls=calls)
            try:
                est = self._estimate(tier_now.tier, system + user, max_out)
                if tier_now.escalated:
                    with store.tx() as db:   # logged BEFORE the call
                        store.event(db, task_id, "model_escalation", {
                            "task_type": task_type, "reason": tier_now.reason, "model": tier_now.model,
                            "estimated_incremental_usd": round(est, 6)})
                rid, _ = self.budget.reserve(task_id, worker, tier_now.model, tier_now.tier,
                                             conservative_input_tokens(system + user), max_out)
            except BudgetBlocked as exc:
                return GatewayResult("blocked", reason=exc.reason, calls=calls)
            try:
                raw = self.provider.complete(model=tier_now.model, system=system, user=user,
                                             max_tokens=max_out, timeout=cfg.model_timeout_seconds,
                                             options=cfg.model_options.get(tier_now.model, {}))
            except Exception:
                self.budget.settle_unknown(rid)   # outcome unknown: charge the reservation conservatively
                return GatewayResult("blocked", reason="provider_error", calls=calls + 1)
            calls += 1
            self.budget.settle(rid, task_id, worker, tier_now.model, tier_now.tier,
                               raw["input_tokens"], raw["output_tokens"])
            tok_in, tok_out = tok_in + raw["input_tokens"], tok_out + raw["output_tokens"]
            parsed = _parse_json(raw["text"]) if raw.get("stop_reason") not in ("refusal", "max_tokens") else None
            if parsed is not None and validator(parsed):
                with store.tx() as db:
                    db.execute("INSERT OR REPLACE INTO results_cache VALUES(?,?,?)",
                               (fp, json.dumps(parsed), store.now()))
                return GatewayResult("ok", parsed, model=tier_now.model, estimated_usd=est, calls=calls,
                                     input_tokens=tok_in, output_tokens=tok_out)
            # invalid output: one corrective retry on the same tier, then escalate (if allowed) or block
            if not corrective_used:
                corrective_used = True
                continue
            if tier_now.tier == "haiku":
                try:
                    tier_now = route(cfg, task_type=task_type, want_tier="sonnet",
                                     escalation_reason="failed_corrective_haiku")
                    continue
                except ModelNotPermitted as exc:
                    return GatewayResult("blocked", reason=str(exc), calls=calls)
            return GatewayResult("blocked", reason="invalid_model_output", calls=calls)


FENCE = re.compile(r"^`{3}(?:json)?\s*(.*?)\s*`{3}$", re.S)


def _parse_json(text):
    """Exactly one JSON object, optionally inside one code fence. Leading or trailing content is rejected.

    Same rule as the strict parser proposed for the pre-gateway monitor (draft PR #26): the failed
    production run of 21 October 2026 returned a second object after the first ("Extra data").
    """
    text = (text or "").strip()
    fenced = FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("{"):
        return None
    try:
        value, end = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return None
    if text[end:].strip() or not isinstance(value, dict):
        return None
    return value
