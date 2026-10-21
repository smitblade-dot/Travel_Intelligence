"""Routes the existing Haiku monitor through the enforced spending gateway.

On GitHub Actions the disk is ephemeral, so the committed monitor state file is the authoritative
spend record. Each run hydrates a throwaway SQLite ledger from that file, makes every paid call
through ModelGateway (reservation, limits, routing), and writes the totals back. Only one monitor
job runs at a time (workflow concurrency group), so the state file has a single writer.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

from .budget import Budget
from .config import load_config
from .models import AnthropicProvider, GatewayResult, ModelGateway
from .store import Store, fingerprint, utc_now

SPEND_STATUS = "estimated from API usage fields x configured list prices; not invoice-confirmed"


class MonitorSpend:
    def __init__(self, cfg, state, workdir=None, provider=None, clock=utc_now):
        self.cfg, self.clock = cfg, clock
        self.workdir = Path(workdir or os.environ.get("RUNNER_TEMP") or tempfile.mkdtemp(prefix="ti-monitor-"))
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.workdir / f"monitor-ledger-{os.getpid()}-{uuid.uuid4().hex[:8]}.sqlite3", clock=clock)
        self.budget = Budget(self.store, cfg)
        self.gateway = ModelGateway(self.store, cfg, provider)
        self._hydrate(state.get("spend") or {})

    @classmethod
    def open(cls, state, env=None, provider=None, workdir=None):
        env = os.environ if env is None else env
        cfg = load_config(env=env)
        # Monitor-specific defaults that preserve the existing request: 2200 output tokens, 90 s timeout.
        cfg.max_output_tokens = int(env.get("TI_OPS_MAX_OUTPUT_TOKENS") or 2200)
        cfg.model_timeout_seconds = int(env.get("TI_OPS_MODEL_TIMEOUT_SECONDS") or 90)
        if provider is None and env.get("ANTHROPIC_API_KEY"):
            provider = AnthropicProvider(env["ANTHROPIC_API_KEY"])
        return cls(cfg, state, workdir=workdir, provider=provider)

    def _hydrate(self, spend):
        day, month = self.store.clock().strftime("%Y-%m-%d"), self.store.clock().strftime("%Y-%m")
        daily = float((spend.get("daily") or {}).get(day, 0) or 0)
        monthly = float((spend.get("monthly") or {}).get(month, 0) or 0)
        self.prior_months = {k: v for k, v in (spend.get("monthly") or {}).items() if k != month}
        with self.store.tx() as db:
            # Distinct period columns keep the two carried totals from being counted twice.
            db.execute("INSERT INTO reservations VALUES('hydrated-day','hydrated',?,?,'hydrated',?,'settled',?,NULL,NULL)",
                       (self.store.now(), day, daily, daily))
            db.execute("INSERT INTO reservations VALUES('hydrated-month','hydrated',?,'hydrated',?,?,'settled',?,NULL,NULL)",
                       (self.store.now(), month, monthly, monthly))

    def triage(self, system, user, batch_key, **kw) -> GatewayResult:
        tid, _ = self.store.create_task(
            worker="continuous_intelligence_monitor", task_type="triage", inputs={"batch": batch_key},
            dedupe_key="monitor|" + fingerprint([batch_key, self.store.now()]),
            business_application="Travel Intelligence", substream="03AA-04 Intelligence Automation Agents",
            mode="live", paid=True)
        return self.gateway.run(task_id=tid, worker="continuous_intelligence_monitor", task_type="triage",
                                system=system, user=user, validator=kw.pop("validator"), **kw)

    def export(self, state):
        with self.store.tx() as db:      # an interrupted call keeps its full reservation
            db.execute("UPDATE reservations SET state='settled', actual_usd=amount_usd WHERE state='reserved'")
        day_total, month_total = self.budget.totals()
        day, month = self.store.clock().strftime("%Y-%m-%d"), self.store.clock().strftime("%Y-%m")
        monthly = dict(sorted({**self.prior_months, month: round(month_total, 6)}.items())[-6:])
        state["spend"] = {"currency": "USD", "status": SPEND_STATUS, "daily": {day: round(day_total, 6)},
                          "monthly": monthly, "updated": self.store.now(),
                          "mode": "dry_run" if self.cfg.dry_run else "live"}
        return state["spend"]
