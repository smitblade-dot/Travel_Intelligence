"""Spending controls enforced in code: reserve before a paid call, reconcile after.

Reservations are written inside a BEGIN IMMEDIATE transaction, so concurrent
reservations serialise and cannot jointly exceed a configured limit.
Amounts are in provider currency (USD). EUR is derived with a dated rate only.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from .store import Store


class BudgetBlocked(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def price_for(cfg, tier):
    """Rates for the model currently configured for this tier. Pricing is keyed by model ID."""
    model = cfg.models.get(tier)
    p = (cfg.pricing or {}).get(model)
    if not p or not p.get("input_per_mtok") or not p.get("output_per_mtok"):
        raise BudgetBlocked(f"pricing_unavailable:{model}")
    if not p.get("verified_at") or not p.get("source_url"):
        raise BudgetBlocked(f"pricing_unverified:{model}")
    return p


def cost_usd(cfg, tier, input_tokens, output_tokens, batch=False, conservative=False):
    """Cost of one request. A prompt over the model's long-context threshold is billed at the long rates.

    With conservative=True (reservations) the long rates apply from 80 percent of the threshold, so an
    underestimated prompt can never be reserved at the cheap rate.
    """
    p = price_for(cfg, tier)
    rin, rout = p["input_per_mtok"], p["output_per_mtok"]
    threshold = p.get("long_context_threshold_tokens")
    if threshold and input_tokens > (threshold * 0.8 if conservative else threshold):
        rin, rout = p.get("long_input_per_mtok", rin), p.get("long_output_per_mtok", rout)
    factor = (1 - p.get("batch_discount", 0.5)) if batch else 1.0
    return factor * (input_tokens * rin + output_tokens * rout) / 1_000_000


def conservative_input_tokens(text: str) -> int:
    # 1 token per 2.5 characters is deliberately pessimistic for English/JSON, plus 10% margin.
    return int(len(text) / 2.5 * 1.1) + 50


class Budget:
    def __init__(self, store: Store, cfg):
        self.store, self.cfg = store, cfg

    def _periods(self):
        now = self.store.clock()
        return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")

    def _committed(self, db, column, value, task_id=None):
        where = f"{column}=?" + (" AND task_id=?" if task_id else "")
        params = (value, task_id) if task_id else (value,)
        row = db.execute(
            "SELECT COALESCE(SUM(CASE state WHEN 'reserved' THEN amount_usd WHEN 'settled' THEN actual_usd "
            f"ELSE 0 END),0) AS s FROM reservations WHERE {where}", params).fetchone()
        return row["s"]

    def reserve(self, task_id, worker, model, tier, est_input_tokens, max_output_tokens):
        cfg = self.cfg
        if not cfg.paid_allowance_configured:
            raise BudgetBlocked("allowance_not_configured")
        amount = cost_usd(cfg, tier, est_input_tokens, max_output_tokens, conservative=True)
        day, month = self._periods()
        with self.store.tx() as db:
            checks = (("task", cfg.per_task_limit_usd, self._committed(db, "task_id", task_id)),
                      ("daily", cfg.daily_limit_usd, self._committed(db, "day", day)),
                      ("monthly", cfg.monthly_limit_usd, self._committed(db, "month", month)))
            for name, limit, used in checks:
                if used + amount > limit + 1e-12:
                    raise BudgetBlocked(f"{name}_allowance_insufficient")
            rid = "res-" + uuid.uuid4().hex[:16]
            db.execute("INSERT INTO reservations VALUES(?,?,?,?,?,?,'reserved',NULL,?,?)",
                       (rid, task_id, self.store.now(), day, month, amount, model, worker))
            self.store.event(db, task_id, "budget_reserved", {"usd": round(amount, 6), "model": model})
        return rid, amount

    def release(self, rid):
        with self.store.tx() as db:
            db.execute("UPDATE reservations SET state='released', actual_usd=0 WHERE id=? AND state='reserved'", (rid,))

    def settle_unknown(self, rid):
        """Call outcome unknown (e.g. timeout): keep the full reservation as the conservative charge."""
        with self.store.tx() as db:
            db.execute("UPDATE reservations SET state='settled', actual_usd=amount_usd WHERE id=? AND state='reserved'",
                       (rid,))

    def settle(self, rid, task_id, worker, model, tier, input_tokens, output_tokens):
        actual = cost_usd(self.cfg, tier, input_tokens, output_tokens)
        day, month = self._periods()
        with self.store.tx() as db:
            row = db.execute("SELECT amount_usd FROM reservations WHERE id=?", (rid,)).fetchone()
            db.execute("UPDATE reservations SET state='settled', actual_usd=? WHERE id=?", (actual, rid))
            db.execute("INSERT INTO usage(ts,day,month,task_id,worker,model,input_tokens,output_tokens,"
                       "gross_cost_usd,amount_status) VALUES(?,?,?,?,?,?,?,?,?,'estimated')",
                       (self.store.now(), day, month, task_id, worker, model, input_tokens, output_tokens, actual))
            if row and actual > row["amount_usd"] + 1e-12:
                self.store.event(db, task_id, "budget_overrun_vs_reservation", {"actual": actual})
        self.store.add_task_cost(task_id, estimated=actual,
                                 usage_record={"model": model, "in": input_tokens, "out": output_tokens,
                                               "usd": round(actual, 6), "status": "estimated"})
        return actual

    def totals(self):
        """(committed today, committed this month) in USD, counting open reservations at full value."""
        day, month = self._periods()
        with self.store.tx() as db:
            return self._committed(db, "day", day), self._committed(db, "month", month)

    # ---- reporting ---------------------------------------------------------
    def summary(self):
        day, month = self._periods()
        cfg = self.cfg
        db_rows = lambda sql, p=(): self.store.query(sql, p)
        def period(col, val):
            r = db_rows(f"SELECT COALESCE(SUM(gross_cost_usd),0) g, COUNT(*) n FROM usage WHERE {col}=?", (val,))[0]
            res = db_rows("SELECT COALESCE(SUM(CASE state WHEN 'reserved' THEN amount_usd WHEN 'settled' "
                          f"THEN actual_usd ELSE 0 END),0) c FROM reservations WHERE {col}=?", (val,))[0]
            return {"gross_estimated_usd": round(r["g"], 6), "calls": r["n"], "committed_usd": round(res["c"], 6)}
        d, m = period("day", day), period("month", month)
        def remaining(limit, p):
            return None if limit is None else round(max(0.0, limit - p["committed_usd"]), 6)
        by = lambda col: db_rows(f"SELECT {col} k, COUNT(*) calls, SUM(input_tokens) in_tok, SUM(output_tokens)"
                                 f" out_tok, ROUND(SUM(gross_cost_usd),6) usd FROM usage GROUP BY {col}")
        confirmations = self.store.get_meta("cost_confirmations", [])
        return {
            "currency": "USD", "cost_source": "anthropic_api (excludes Claude Code subscription usage)",
            "mode": "dry_run" if cfg.dry_run else "live",
            "allowance_configured": cfg.paid_allowance_configured,
            "limits_usd": {"per_task": cfg.per_task_limit_usd, "daily": cfg.daily_limit_usd, "monthly": cfg.monthly_limit_usd},
            "today_utc": {"date": day, **d, "remaining_usd": remaining(cfg.daily_limit_usd, d)},
            "month_utc": {"month": month, **m, "remaining_usd": remaining(cfg.monthly_limit_usd, m)},
            "by_model": by("model"), "by_worker": by("worker"), "by_task": by("task_id"),
            "amount_status": "estimated from API usage fields and configured pricing; not invoice-confirmed",
            "confirmed": confirmations, "credits_applied_usd": None if not confirmations else
                sum(c.get("credits_usd") or 0 for c in confirmations),
            "payable_usd": None if not confirmations else sum(c.get("payable_usd") or 0 for c in confirmations),
        }

    def record_confirmation(self, period, gross_usd, credits_usd, payable_usd, source):
        """Record a provider-confirmed figure (invoice or Console). Kept apart from estimates."""
        rows = self.store.get_meta("cost_confirmations", [])
        rows.append({"period": period, "gross_usd": gross_usd, "credits_usd": credits_usd,
                     "payable_usd": payable_usd, "source": source, "recorded_at": self.store.now()})
        self.store.set_meta("cost_confirmations", rows)


def eur_view(store: Store, usd_amount: float):
    """Convert with the latest stored dated rate; never invent one."""
    rows = store.query("SELECT * FROM fx_rates WHERE base='USD' AND quote='EUR' ORDER BY date DESC LIMIT 1")
    if not rows:
        return {"eur": None, "flag": "No verified USD->EUR rate stored; conversion not performed",
                "original_usd": usd_amount}
    r = rows[0]
    return {"eur": round(usd_amount * r["rate"], 6), "rate": r["rate"], "rate_date": r["date"],
            "rate_source": r["source"], "original_usd": usd_amount}
