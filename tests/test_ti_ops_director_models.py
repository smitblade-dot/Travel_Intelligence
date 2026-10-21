"""Model routing, spending controls and cost-saving behaviour. Providers are mocked: no API charges."""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ops_support import *   # noqa: F401,F403
from ti_ops_director.budget import Budget, BudgetBlocked, cost_usd
from ti_ops_director.models import ModelGateway, ModelNotPermitted, Provider, route
from ti_ops_director.workers import Worker, WorkResult


class FakeProvider(Provider):
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def complete(self, *, model, system, user, max_tokens, timeout=30, options=None):
        self.calls.append(model)
        text = self.replies.pop(0)
        return {"text": text, "input_tokens": 1000, "output_tokens": 100}


GOOD = json.dumps({"relevant": True})
valid = lambda d: d.get("relevant") in (True, False)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def gw(self, replies=(), **cfgkw):
        cfg = paid_cfg(self.tmp, **cfgkw)
        clock = Clock()
        store = Store(cfg.db_path, clock=clock)
        tid, _ = store.create_task(worker="w", task_type="t", inputs={}, dedupe_key="k" + str(id(replies)),
                                   business_application="Travel Intelligence", substream="03AA-04")
        prov = FakeProvider(replies)
        return ModelGateway(store, cfg, prov), prov, tid, store, cfg

    def run_gw(self, gw, tid, text="classify this", **kw):
        return gw.run(task_id=tid, worker="w", task_type="classification", system="Return JSON.",
                      user=text, validator=valid, **kw)


class RoutingTests(Base):
    def test_haiku_default_and_cached_second_call_is_free(self):
        gw, prov, tid, store, cfg = self.gw([GOOD])
        r1 = self.run_gw(gw, tid)
        r2 = self.run_gw(gw, tid)                               # identical content: reuse, no call
        self.assertEqual((r1.status, r2.status), ("ok", "cached"))
        self.assertEqual(prov.calls, [cfg.models["haiku"]])
        self.assertEqual(r1.model, cfg.models["haiku"])

    def test_changed_content_is_analysed(self):
        gw, prov, tid, *_ = self.gw([GOOD, GOOD])
        self.run_gw(gw, tid, "one"); self.run_gw(gw, tid, "two")
        self.assertEqual(len(prov.calls), 2)

    def test_sonnet_needs_listed_reason_and_logs_estimate_before_call(self):
        gw, prov, tid, store, cfg = self.gw([GOOD])
        self.assertEqual(self.run_gw(gw, tid, want_tier="sonnet").reason, "sonnet_requires_logged_escalation_reason")
        self.assertEqual(self.run_gw(gw, tid, want_tier="sonnet", escalation_reason="because I like it").status, "blocked")
        self.assertEqual(prov.calls, [])
        r = self.run_gw(gw, tid, want_tier="sonnet", escalation_reason="conflicting_evidence")
        self.assertEqual(r.status, "ok")
        ev = store.query("SELECT kind, detail FROM events WHERE kind='model_escalation'")
        d = json.loads(ev[0]["detail"])
        self.assertEqual((d["reason"], d["model"]), ("conflicting_evidence", cfg.models["sonnet"]))
        self.assertGreater(d["estimated_incremental_usd"], 0)

    def test_opus_disabled_by_default_and_needs_matching_approval(self):
        cfg = make_cfg(self.tmp)
        with self.assertRaises(ModelNotPermitted):
            route(cfg, task_type="classification", want_tier="opus", escalation_reason="x")
        cfg.opus_approval = {"approved_by": "Marc", "task_type": "other", "allowance_usd": 1}
        with self.assertRaises(ModelNotPermitted):
            route(cfg, task_type="classification", want_tier="opus")
        cfg.opus_approval = {"approved_by": "Marc", "task_type": "classification", "allowance_usd": 1}
        self.assertEqual(route(cfg, task_type="classification", want_tier="opus").tier, "opus")
        cfg.opus_approval = {"approved_by": "Somebody", "task_type": "classification", "allowance_usd": 1}
        with self.assertRaises(ModelNotPermitted):
            route(cfg, task_type="classification", want_tier="opus")

    def test_one_corrective_retry_then_escalation_with_reason(self):
        gw, prov, tid, store, cfg = self.gw(["not json", "still not", GOOD], max_model_calls_per_task=3)
        r = self.run_gw(gw, tid)
        self.assertEqual(r.status, "ok")
        self.assertEqual(prov.calls, [cfg.models["haiku"]] * 2 + [cfg.models["sonnet"]])
        d = json.loads(store.query("SELECT detail FROM events WHERE kind='model_escalation'")[0]["detail"])
        self.assertEqual(d["reason"], "failed_corrective_haiku")

    def test_max_calls_per_task_blocks_further_work(self):
        gw, prov, tid, *_ = self.gw(["bad", "bad", GOOD], max_model_calls_per_task=2)
        r = self.run_gw(gw, tid)
        self.assertEqual((r.status, r.reason), ("blocked", "max_model_calls_per_task"))
        self.assertEqual(len(prov.calls), 2)

    def test_dry_run_makes_no_call(self):
        gw, prov, tid, *_ = self.gw([GOOD], dry_run=True)
        r = self.run_gw(gw, tid)
        self.assertEqual((r.status, prov.calls), ("dry_run", []))
        self.assertGreater(r.estimated_usd, 0)


class PricingTierTests(Base):
    def test_long_context_tier_and_conservative_reservation(self):
        cfg = paid_cfg(self.tmp, pricing={"claude-haiku-5-5": dict(PRICING["claude-haiku-5-5"], long_context_threshold_tokens=100000,
                                                                 long_input_per_mtok=0.50, long_output_per_mtok=2.50)})
        self.assertAlmostEqual(cost_usd(cfg, "haiku", 50_000, 1000), 0.0055)          # standard tier
        self.assertAlmostEqual(cost_usd(cfg, "haiku", 150_000, 1000), 0.0775)         # whole request at long rates
        self.assertAlmostEqual(cost_usd(cfg, "haiku", 85_000, 1000, conservative=True), 0.0450)   # reserved at long rates
        self.assertAlmostEqual(cost_usd(cfg, "haiku", 85_000, 1000), 0.0090)

    def test_oversized_prompt_is_refused_not_priced_up(self):
        gw, prov, tid, *_ = self.gw([GOOD])
        r = gw.run(task_id=tid, worker="w", task_type="classification", system="s", user="x" * 200_000,
                   validator=valid, max_chars=300_000)
        self.assertEqual((r.status, r.reason, prov.calls), ("blocked", "input_too_large", []))

    def test_default_pricing_file_is_verified_and_model_keyed(self):
        from ti_ops_director.config import Config
        c = Config()
        for model in (c.models["haiku"], c.models["sonnet"], c.models["opus"]):
            self.assertIn(model, c.pricing)
            self.assertTrue(c.pricing[model]["verified_at"] and c.pricing[model]["source_url"])
        self.assertEqual(c.pricing["claude-haiku-5-5"]["long_input_per_mtok"], 0.50)


class SpendTests(Base):
    def test_unconfigured_allowance_blocks_paid_calls(self):
        gw, prov, tid, *_ = self.gw([GOOD], daily_limit_usd=None)
        r = self.run_gw(gw, tid)
        self.assertEqual((r.status, r.reason, prov.calls), ("blocked", "allowance_not_configured", []))

    def test_missing_or_unverified_pricing_blocks(self):
        gw, prov, tid, *_ = self.gw([GOOD], pricing={})
        self.assertEqual(self.run_gw(gw, tid).reason, "pricing_unavailable:claude-haiku-5-5")
        bad = {"claude-haiku-5-5": {"input_per_mtok": 1, "output_per_mtok": 1}}
        gw, prov, tid, *_ = self.gw([GOOD], pricing=bad)
        self.assertEqual(self.run_gw(gw, tid).reason, "pricing_unverified:claude-haiku-5-5")
        self.assertEqual(prov.calls, [])

    def test_exhausted_allowance_blocks(self):
        gw, prov, tid, *_ = self.gw([GOOD], per_task_limit_usd=0.0000001)
        r = self.run_gw(gw, tid)
        self.assertEqual((r.status, r.reason), ("blocked", "task_allowance_insufficient"))
        self.assertEqual(prov.calls, [])

    def test_concurrent_reservations_cannot_overspend(self):
        cfg = paid_cfg(self.tmp, per_task_limit_usd=100, daily_limit_usd=0.0100, monthly_limit_usd=100)
        store = Store(cfg.db_path, clock=Clock())
        budget = Budget(store, cfg)
        one = cost_usd(cfg, "haiku", 10000, 800)
        ok, blocked = [], []
        def go(i):
            tid = f"t{i}"
            try:
                budget.reserve(tid, "w", "m", "haiku", 10000, 800); ok.append(i)
            except BudgetBlocked:
                blocked.append(i)
        ths = [threading.Thread(target=go, args=(i,)) for i in range(40)]
        [t.start() for t in ths]; [t.join() for t in ths]
        self.assertEqual(len(ok), int(0.0100 // one))
        self.assertLessEqual(len(ok) * one, 0.0100 + 1e-12)
        self.assertEqual(len(ok) + len(blocked), 40)

    def test_settle_records_estimated_usage_by_task_worker_model(self):
        gw, prov, tid, store, cfg = self.gw([GOOD])
        self.run_gw(gw, tid)
        s = Budget(store, cfg).summary()
        self.assertEqual(s["by_model"][0]["k"], cfg.models["haiku"])
        self.assertEqual(s["today_utc"]["calls"], 1)
        self.assertIn("not invoice-confirmed", s["amount_status"])
        self.assertIsNone(s["payable_usd"])                      # not verified: not invented
        Budget(store, cfg).record_confirmation("2026-10", 1.0, 0.25, 0.75, "Console invoice (test fixture)")
        self.assertEqual(Budget(store, cfg).summary()["payable_usd"], 0.75)

    def test_provider_error_charges_reservation_conservatively(self):
        class Boom(Provider):
            def complete(self, **kw):
                raise TimeoutError()
        gw, _, tid, store, cfg = self.gw([])
        gw.provider = Boom()
        r = self.run_gw(gw, tid)
        self.assertEqual(r.reason, "provider_error")
        self.assertGreater(Budget(store, cfg).summary()["today_utc"]["committed_usd"], 0)

    def test_deterministic_work_continues_when_paid_work_blocked(self):
        class PaidWorker(Worker):
            name, task_types, implemented = "paid_test", ("paid_test",), True
            def run(self, task, ctx):
                return WorkResult("completed", {})
        reg = default_registry(); reg.register(PaidWorker()); reg.register(RecordingWorker())
        cfg = make_cfg(self.tmp, dry_run=False)                  # live mode, NO allowance configured
        ctl, _ = make_controller(self.tmp, cfg=cfg, registry=reg)
        paid, _ = ctl.enqueue(task_type="paid_test", inputs={}, dedupe_subject="p", paid=True)
        det, _ = ctl.enqueue(task_type="recording", inputs={}, dedupe_subject="d")
        ctl.tick()
        self.assertEqual(ctl.store.get(paid)["state"], "blocked")
        self.assertEqual(ctl.store.get(paid)["blocking_reason"], "budget:allowance_not_configured")
        self.assertTrue(ctl.store.get(paid)["requires_human"])
        self.assertEqual(ctl.store.get(det)["state"], "completed")
        ctl.cfg.per_task_limit_usd = ctl.cfg.daily_limit_usd = ctl.cfg.monthly_limit_usd = 1.0
        ctl.cfg.pricing = PRICING
        ctl.tick()
        self.assertEqual(ctl.store.get(paid)["state"], "completed")


if __name__ == "__main__":
    unittest.main()
