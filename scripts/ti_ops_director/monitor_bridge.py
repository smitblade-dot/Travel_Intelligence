"""Routes the existing Haiku monitor through the enforced spending gateway.

The authoritative spend record is ``state["spend"]`` in the monitor state file (see monitor_state.py for
how that file is shared between runs). Each run hydrates a throwaway SQLite ledger from it, so the gateway's
transactional per-task checks still apply, and additionally:

1. before every paid call the reservation is written to the shared state (compare-and-swap) and checked
   there against the daily and monthly limits, including other runs' open reservations;
2. straight after the call the settlement and the result-cache entry are written back (checkpoint);
3. a reservation whose run never settled it (job killed, push failed) is charged in full once it is older
   than RESERVATION_TTL_MINUTES. Spend is therefore never under-counted; at worst it is over-counted.
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from .budget import Budget, BudgetBlocked
from .config import load_config
from .models import AnthropicProvider, GatewayResult, ModelGateway
from .monitor_state import StateSyncError, dump_state
from .store import Store, fingerprint, utc_now

SPEND_STATUS = "estimated from API usage fields x configured list prices; not invoice-confirmed"
RESERVATION_TTL_MINUTES = 60       # the monitor job times out after 12 minutes
CACHE_TTL_HOURS = 48
CACHE_MAX_ENTRIES = 20


def spend_section(state):
    s = state.setdefault("spend", {})
    for key, empty in (("daily", {}), ("monthly", {}), ("open", [])):
        if not isinstance(s.get(key), type(empty)):
            s[key] = empty
    s.setdefault("currency", "USD")
    s["status"] = SPEND_STATUS
    return s


def _fold(spend, day, month, usd):
    spend["daily"][day] = round(float(spend["daily"].get(day, 0) or 0) + usd, 6)
    spend["monthly"][month] = round(float(spend["monthly"].get(month, 0) or 0) + usd, 6)
    spend["total"] = round(float(spend.get("total", 0) or 0) + usd, 6)
    spend["daily"] = dict(sorted(spend["daily"].items())[-7:])
    spend["monthly"] = dict(sorted(spend["monthly"].items())[-6:])


def expire_open(spend, now):
    """Charge abandoned reservations at their full reserved amount and close them."""
    keep, cutoff = [], now - timedelta(minutes=RESERVATION_TTL_MINUTES)
    for r in spend["open"]:
        try:
            stale = datetime.fromisoformat(r["at"]) < cutoff
        except (KeyError, TypeError, ValueError):
            stale = True
        if stale:
            _fold(spend, r.get("day", now.strftime("%Y-%m-%d")), r.get("month", now.strftime("%Y-%m")),
                  float(r.get("usd", 0) or 0))
            spend.setdefault("expired", []).append({"id": r.get("id"), "usd": r.get("usd"), "at": r.get("at"),
                                                    "charged": "in_full_unsettled"})
            spend["expired"] = spend["expired"][-20:]
        else:
            keep.append(r)
    spend["open"] = keep


def committed(spend, day, month):
    d = float(spend["daily"].get(day, 0) or 0) + sum(float(r["usd"]) for r in spend["open"] if r.get("day") == day)
    m = float(spend["monthly"].get(month, 0) or 0) + sum(float(r["usd"]) for r in spend["open"] if r.get("month") == month)
    return d, m


class SyncedBudget(Budget):
    """Local per-task checks (SQLite) plus a shared daily/monthly reservation before every paid call."""
    def __init__(self, store, cfg, owner):
        super().__init__(store, cfg)
        self.owner = owner

    def reserve(self, task_id, worker, model, tier, est_input_tokens, max_output_tokens):
        rid, amount = super().reserve(task_id, worker, model, tier, est_input_tokens, max_output_tokens)
        try:
            self.owner.reserve_shared(rid, amount, model)
        except BudgetBlocked:
            self.release(rid)
            raise
        except StateSyncError:
            self.release(rid)
            raise BudgetBlocked("state_sync_failed")
        return rid, amount


class MonitorSpend:
    def __init__(self, cfg, state, workdir=None, provider=None, clock=utc_now, backend=None):
        self.cfg, self.clock, self.backend = cfg, clock, backend
        self.run_id = os.environ.get("GITHUB_RUN_ID", "local") + "-" + uuid.uuid4().hex[:8]
        self.workdir = Path(workdir or os.environ.get("RUNNER_TEMP") or tempfile.mkdtemp(prefix="ti-monitor-"))
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.workdir / f"monitor-ledger-{os.getpid()}-{uuid.uuid4().hex[:8]}.sqlite3", clock=clock)
        self.budget = SyncedBudget(self.store, cfg, self)
        self.gateway = ModelGateway(self.store, cfg, provider)
        self.gateway.budget = self.budget
        self.batch_key = None
        self.shared = set()           # reservation ids written to the shared state
        self.checkpointed = set()     # reservation ids already settled in the shared state
        self._hydrate(state)

    @classmethod
    def open(cls, state, env=None, provider=None, workdir=None, backend=None):
        env = os.environ if env is None else env
        cfg = load_config(env=env)
        # Monitor-specific defaults that preserve the existing request: 2200 output tokens, 90 s timeout.
        cfg.max_output_tokens = int(env.get("TI_OPS_MAX_OUTPUT_TOKENS") or 2200)
        cfg.model_timeout_seconds = int(env.get("TI_OPS_MODEL_TIMEOUT_SECONDS") or 90)
        if provider is None and env.get("ANTHROPIC_API_KEY"):
            provider = AnthropicProvider(env["ANTHROPIC_API_KEY"])
        return cls(cfg, state, workdir=workdir, provider=provider, backend=backend)

    def _periods(self):
        now = self.store.clock()
        return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")

    def _hydrate(self, state):
        spend = spend_section(json.loads(json.dumps(state)))     # a copy: hydration never edits the caller's state
        expire_open(spend, self.store.clock())
        day, month = self._periods()
        daily, monthly = committed(spend, day, month)
        with self.store.tx() as db:
            # Distinct period columns keep the two carried totals from being counted twice.
            db.execute("INSERT INTO reservations VALUES('hydrated-day','hydrated',?,?,'hydrated',?,'settled',?,NULL,NULL)",
                       (self.store.now(), day, daily, daily))
            db.execute("INSERT INTO reservations VALUES('hydrated-month','hydrated',?,'hydrated',?,?,'settled',?,NULL,NULL)",
                       (self.store.now(), month, monthly, monthly))
            cutoff = self.store.clock() - timedelta(hours=CACHE_TTL_HOURS)
            for fp, entry in (state.get("triageCache") or {}).items():
                try:
                    if datetime.fromisoformat(entry["at"]) >= cutoff:
                        db.execute("INSERT OR REPLACE INTO results_cache VALUES(?,?,'hydrated')",
                                   (fp, json.dumps(entry["result"])))
                except (KeyError, TypeError, ValueError):
                    continue

    # ---- shared reservations ---------------------------------------------------------------------
    def reserve_shared(self, rid, amount, model):
        if self.backend is None:
            return                                   # local-only use (evaluation harness): SQLite checks apply
        day, month = self._periods()
        cfg, batch, run = self.cfg, self.batch_key, self.run_id

        def apply(snap):
            st = snap.state()
            spend = spend_section(st)
            expire_open(spend, self.store.clock())
            if batch and any(r.get("batch") == batch and r.get("run") != run for r in spend["open"]):
                return None, "concurrent_run_in_progress"
            d, m = committed(spend, day, month)
            if d + amount > cfg.daily_limit_usd + 1e-12:
                return None, "daily_allowance_insufficient"
            if m + amount > cfg.monthly_limit_usd + 1e-12:
                return None, "monthly_allowance_insufficient"
            total = float(spend.get("total", 0) or 0) + sum(float(r["usd"]) for r in spend["open"])
            if cfg.total_limit_usd is not None and total + amount > cfg.total_limit_usd + 1e-12:
                return None, "total_allowance_insufficient"
            spend["open"].append({"id": rid, "run": run, "at": self.store.now(), "day": day, "month": month,
                                  "usd": round(amount, 6), "model": model, "batch": batch})
            spend["updated"] = self.store.now()
            return {"state": dump_state(st)}, "reserved"

        _, verdict = self.backend.transact(apply, message="Monitor spend reservation (before model call)")
        if verdict != "reserved":
            raise BudgetBlocked(verdict)
        self.shared.add(rid)

    def _local_reservations(self):
        return self.store.query("SELECT id, day, month, amount_usd, actual_usd, state FROM reservations "
                                "WHERE task_id!='hydrated'")

    def settle_into(self, state):
        """Apply this run's settlements to ``state`` (normally the latest shared state). Idempotent."""
        spend = spend_section(state)
        expire_open(spend, self.store.clock())
        for r in self._local_reservations():
            if r["id"] in self.checkpointed:
                continue
            usd = {"settled": r["actual_usd"], "released": 0.0}.get(r["state"], r["amount_usd"])  # open: full
            was_open = any(o.get("id") == r["id"] for o in spend["open"])
            spend["open"] = [o for o in spend["open"] if o.get("id") != r["id"]]
            if r["id"] in self.shared and not was_open:
                continue        # already charged in full by expiry in the shared state
            _fold(spend, r["day"], r["month"], float(usd or 0))
        cache = state.get("triageCache") if isinstance(state.get("triageCache"), dict) else {}
        for row in self.store.query("SELECT fingerprint, result, ts FROM results_cache WHERE ts!='hydrated'"):
            cache[row["fingerprint"]] = {"at": row["ts"], "result": json.loads(row["result"])}
        state["triageCache"] = dict(sorted(cache.items(), key=lambda kv: kv[1].get("at", ""))[-CACHE_MAX_ENTRIES:])
        spend["mode"] = "dry_run" if self.cfg.dry_run else "live"
        spend["updated"] = self.store.now()
        return spend

    def checkpoint(self):
        """After model calls: write settlements and the cache entry at once, before publication work."""
        pending = [r["id"] for r in self._local_reservations() if r["id"] in self.shared and r["id"] not in self.checkpointed]
        if self.backend is None or not pending:
            return False

        def apply(snap):
            st = snap.state()
            self.settle_into(st)
            return {"state": dump_state(st)}, True

        self.backend.transact(apply, message="Monitor spend settlement (after model call)")
        self.checkpointed.update(pending)
        return True

    def committed_now(self, state):
        day, month = self._periods()
        spend = spend_section(json.loads(json.dumps(state)))
        return committed(spend, day, month)

    # ---- the call ------------------------------------------------------------------------------
    def triage(self, system, user, batch_key, **kw) -> GatewayResult:
        self.batch_key = batch_key
        tid, _ = self.store.create_task(
            worker="continuous_intelligence_monitor", task_type="triage", inputs={"batch": batch_key},
            dedupe_key="monitor|" + fingerprint([batch_key, self.store.now()]),
            business_application="Travel Intelligence", substream="03AA-04 Intelligence Automation Agents",
            mode="live", paid=True)
        return self.gateway.run(task_id=tid, worker="continuous_intelligence_monitor", task_type="triage",
                                system=system, user=user, validator=kw.pop("validator"), **kw)

    def export(self, state):
        """Local-only settlement into ``state`` (used when no shared backend is configured)."""
        return self.settle_into(state)
