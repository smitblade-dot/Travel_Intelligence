"""The real continuous_intelligence_monitor.main() run against a mocked Anthropic endpoint.

No network, no API key and no charge: urlopen inside the gateway is replaced, or the provider is faked.
"""
import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from email.utils import formatdate
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ops_support import ROOT   # noqa: E402

SCRIPT = ROOT / "scripts" / "continuous_intelligence_monitor.py"
spec = importlib.util.spec_from_file_location("monitor_under_test", SCRIPT)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)

LIMITS = {"TI_OPS_DRY_RUN": "false", "TI_OPS_PER_TASK_LIMIT_USD": "0.05", "TI_OPS_DAILY_LIMIT_USD": "0.20",
          "TI_OPS_MONTHLY_LIMIT_USD": "2"}
GOOD = json.dumps({"decisions": [{"index": 0, "countryId": "gb", "publish": True, "existingId": None,
                                  "category": "TRANSPORT_DISRUPTION", "incidentDate": None,
                                  "continuingImpact": True, "incidentKey": "heathrow-runway-closure"}]})


class FakeProvider:
    """Stands in for AnthropicProvider; checks that a reservation exists at call time."""
    def __init__(self, replies, ledger_dir=None):
        self.replies, self.calls, self.reserved_at_call = list(replies), [], []
        self.ledger_dir = ledger_dir

    def complete(self, *, model, system, user, max_tokens, timeout=30, options=None):
        self.calls.append({"model": model, "options": options, "max_tokens": max_tokens, "system": system, "user": user})
        for f in Path(self.ledger_dir).glob("monitor-ledger-*.sqlite3"):
            db = sqlite3.connect(f)
            self.reserved_at_call.append(db.execute("SELECT COUNT(*) FROM reservations WHERE state='reserved'").fetchone()[0])
            db.close()
        return {"text": self.replies.pop(0), "input_tokens": 2000, "output_tokens": 150, "stop_reason": "end_turn"}


class MonitorHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data_path, self.state_path = self.tmp / "data.json", self.tmp / "state.json"
        self.base = {"countries": [{"id": "gb", "name": "United Kingdom"}], "records": [], "sources": [], "locations": [],
                     "events": [], "sourceObservations": [], "meta": {"counts": {}, "currentIntelligence": {}}}
        self.data_path.write_text(json.dumps(self.base))
        self.items = [{"title": "Heathrow runway closed after incident", "url": "https://example.org/heathrow",
                       "date": formatdate(usegmt=True), "feed": "https://example.org/feed"}]
        self.patches = [mock.patch.object(monitor, "DATA", self.data_path), mock.patch.object(monitor, "STATE", self.state_path),
                        mock.patch.object(monitor, "discover", lambda: (list(self.items), []))]
        [p.start() for p in self.patches]
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def run_monitor(self, env=None, replies=(), provider=None, state=None):
        if state is not None:
            self.state_path.write_text(json.dumps(state))
        clean = {k: v for k, v in os.environ.items() if not k.startswith("TI_") and k != "ANTHROPIC_API_KEY"}
        clean.update({"RUNNER_TEMP": str(self.tmp), "ANTHROPIC_API_KEY": "test-key-not-real"}, **(env or {}))
        provider = provider or FakeProvider(replies, self.tmp)
        out = io.StringIO()
        with mock.patch.dict(os.environ, clean, clear=True), \
                mock.patch("ti_ops_director.monitor_bridge.AnthropicProvider", lambda key: provider), \
                mock.patch("ti_ops_director.models.urlopen", side_effect=AssertionError("direct network call")), \
                contextlib.redirect_stdout(out):
            monitor.main()
        self.last_out = out.getvalue()
        return provider, json.loads(self.state_path.read_text())

    def ledger(self, sql):
        rows = []
        for f in self.tmp.glob("monitor-ledger-*.sqlite3"):
            db = sqlite3.connect(f); db.row_factory = sqlite3.Row
            rows += [dict(r) for r in db.execute(sql).fetchall()]
            db.close()
        return rows


class BlockedPaths(MonitorHarness):
    def test_default_environment_is_dry_run_and_makes_no_call(self):
        prov, state = self.run_monitor(env={}, replies=[GOOD])
        self.assertEqual(prov.calls, [])
        self.assertEqual(state["lastTriageBlocked"]["reason"], "dry_run")
        self.assertEqual(state.get("seen", []), [])                  # cursor untouched: headlines kept
        self.assertIn("Haiku triage not run", self.last_out)

    def test_unset_limits_block_paid_execution(self):
        prov, state = self.run_monitor(env={"TI_OPS_DRY_RUN": "false"}, replies=[GOOD])
        self.assertEqual(prov.calls, [])
        self.assertEqual(state["lastTriageBlocked"]["reason"], "allowance_not_configured")
        self.assertEqual(state.get("seen", []), [])

    def test_partially_set_limits_still_block(self):
        env = dict(LIMITS); env.pop("TI_OPS_MONTHLY_LIMIT_USD")
        prov, state = self.run_monitor(env=env, replies=[GOOD])
        self.assertEqual((prov.calls, state["lastTriageBlocked"]["reason"]), ([], "allowance_not_configured"))

    def test_exhausted_limit_blocks_using_persisted_spend(self):
        today = monitor.now_utc().strftime("%Y-%m-%d")
        prior = {"seen": [], "spend": {"daily": {today: 0.20}, "monthly": {today[:7]: 0.20}}}
        prov, state = self.run_monitor(env=LIMITS, replies=[GOOD], state=prior)
        self.assertEqual(prov.calls, [])
        self.assertEqual(state["lastTriageBlocked"]["reason"], "daily_allowance_insufficient")

    def test_unverified_pricing_for_overridden_model_blocks(self):
        prov, state = self.run_monitor(env=dict(LIMITS, TI_HAIKU_MODEL="claude-haiku-9-9"), replies=[GOOD])
        self.assertEqual(prov.calls, [])
        self.assertEqual(state["lastTriageBlocked"]["reason"], "pricing_unavailable:claude-haiku-9-9")

    def test_blocked_run_publishes_nothing_even_if_publish_switch_is_on(self):
        before = self.data_path.read_text()
        self.run_monitor(env={"TI_MONITOR_PUBLISH": "true"}, replies=[GOOD])
        self.assertEqual(before, self.data_path.read_text())

    def test_missing_key_with_allowance_fails_explicitly(self):
        with self.assertRaises(RuntimeError):
            clean = dict(LIMITS, RUNNER_TEMP=str(self.tmp))
            with mock.patch.dict(os.environ, clean, clear=True):
                monitor.main()


class PaidPath(MonitorHarness):
    def test_defaults_to_haiku_with_compatible_request(self):
        prov, state = self.run_monitor(env=LIMITS, replies=[GOOD])
        self.assertEqual(len(prov.calls), 1)
        self.assertEqual(prov.calls[0]["model"], "claude-haiku-5-5")
        self.assertEqual(prov.calls[0]["options"]["thinking"], {"type": "disabled"})
        self.assertNotIn("temperature", prov.calls[0]["options"])
        self.assertEqual(prov.calls[0]["max_tokens"], 2200)           # existing output cap preserved
        self.assertEqual(len(state["seen"]), 1)                         # cursor advances only after triage
        self.assertNotIn("lastTriageBlocked", state)

    def test_actual_request_body_haiku_5_5_and_legacy_model(self):
        sent = []
        def fake_urlopen(req, timeout=0):
            sent.append((json.loads(req.data), timeout))
            body = json.dumps({"content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": GOOD}],
                               "usage": {"input_tokens": 1500, "output_tokens": 100}, "stop_reason": "end_turn"}).encode()
            return io.BytesIO(body) if False else _Resp(body)
        class _Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        clean = {"RUNNER_TEMP": str(self.tmp), "ANTHROPIC_API_KEY": "test-key-not-real", **LIMITS}
        with mock.patch.dict(os.environ, clean, clear=True), mock.patch("ti_ops_director.models.urlopen", fake_urlopen), \
                contextlib.redirect_stdout(io.StringIO()):
            monitor.main()
        body, timeout = sent[0]
        self.assertEqual(body["model"], "claude-haiku-5-5")
        self.assertNotIn("temperature", body)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["output_config"], {"effort": "low"})
        self.assertEqual(body["messages"][-1]["role"], "user")        # no prefill (400 on Haiku 5.5)
        self.assertEqual(timeout, 90)
        # rollback path: the older model keeps temperature 0 and its own, higher price
        self.state_path.unlink(); sent.clear()
        clean["TI_HAIKU_MODEL"] = "claude-haiku-4-5-20251001"
        with mock.patch.dict(os.environ, clean, clear=True), mock.patch("ti_ops_director.models.urlopen", fake_urlopen), \
                contextlib.redirect_stdout(io.StringIO()):
            monitor.main()
        self.assertEqual((sent[0][0]["model"], sent[0][0]["temperature"]), ("claude-haiku-4-5-20251001", 0))
        self.assertNotIn("thinking", sent[0][0])

    def test_unchanged_inputs_trigger_no_model_call(self):
        prov, state = self.run_monitor(env=LIMITS, replies=[GOOD, GOOD])
        spend_after_first = state["spend"]
        prov2, state2 = self.run_monitor(env=LIMITS, replies=[GOOD], provider=prov)
        self.assertEqual(len(prov.calls), 1)                            # second run: same headlines, zero calls
        self.assertEqual(state2["spend"]["daily"], spend_after_first["daily"])

    def test_no_bypass_reservation_exists_before_every_call(self):
        self.assertNotIn("api.anthropic.com", SCRIPT.read_text())       # monitor holds no direct endpoint
        self.assertNotIn("urlopen(request", SCRIPT.read_text())
        prov, state = self.run_monitor(env=LIMITS, replies=[GOOD])
        self.assertEqual(prov.reserved_at_call, [1])
        res = self.ledger("SELECT state, actual_usd FROM reservations WHERE task_id!='hydrated'")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["state"], "settled")

    def test_spend_is_persisted_and_next_run_is_limited_by_it(self):
        prov, state = self.run_monitor(env=LIMITS, replies=[GOOD])
        today = monitor.now_utc().strftime("%Y-%m-%d")
        spent = state["spend"]["daily"][today]
        self.assertGreater(spent, 0)
        self.assertIn("not invoice-confirmed", state["spend"]["status"])
        self.items = [{"title": "Second, different headline", "url": "https://example.org/other",
                       "date": formatdate(usegmt=True), "feed": "f"}]
        tight = dict(LIMITS, TI_OPS_DAILY_LIMIT_USD=str(spent + 0.0001))   # headroom smaller than one reservation
        prov2, state2 = self.run_monitor(env=tight, replies=[GOOD])
        self.assertEqual(prov2.calls, [])
        self.assertEqual(state2["lastTriageBlocked"]["reason"], "daily_allowance_insufficient")

    def test_failed_provider_call_keeps_cursor_and_charges_reservation(self):
        class Boom(FakeProvider):
            def complete(self, **kw):
                raise TimeoutError()
        prov, state = self.run_monitor(env=LIMITS, provider=Boom([], self.tmp))
        self.assertEqual(state["lastTriageBlocked"]["reason"], "provider_error")
        self.assertEqual(state.get("seen", []), [])
        self.assertGreater(list(state["spend"]["daily"].values())[0], 0)

    def test_publication_default_is_off_and_data_untouched(self):
        before = self.data_path.read_text()
        self.run_monitor(env=LIMITS, replies=[GOOD])
        self.assertEqual(before, self.data_path.read_text())
        self.assertIn("DRY_RUN_DECISIONS", self.last_out)
        self.run_monitor(env=dict(LIMITS, TI_MONITOR_PUBLISH="true"), replies=[GOOD],
                         state={"seen": []})                              # explicit switch is the only way
        self.assertEqual(len(json.loads(self.data_path.read_text())["events"]), 1)


class Escalation(MonitorHarness):
    def test_sonnet_escalation_is_justified_and_logged_before_the_call(self):
        env = dict(LIMITS, TI_OPS_MAX_MODEL_CALLS_PER_TASK="3")
        prov, state = self.run_monitor(env=env, replies=["not json", "still not json", GOOD])
        self.assertEqual([c["model"] for c in prov.calls], ["claude-haiku-5-5"] * 2 + ["claude-sonnet-5-5"])
        ev = self.ledger("SELECT detail FROM events WHERE kind='model_escalation'")
        d = json.loads(ev[0]["detail"])
        self.assertEqual((d["reason"], d["model"]), ("failed_corrective_haiku", "claude-sonnet-5-5"))
        self.assertGreater(d["estimated_incremental_usd"], 0)
        self.assertEqual(len(state["seen"]), 1)

    def test_default_call_cap_prevents_escalation(self):
        prov, state = self.run_monitor(env=LIMITS, replies=["bad", "bad", GOOD])
        self.assertEqual([c["model"] for c in prov.calls], ["claude-haiku-5-5"] * 2)
        self.assertEqual(state["lastTriageBlocked"]["reason"], "max_model_calls_per_task")
        self.assertEqual(state.get("seen", []), [])

    def test_opus_cannot_be_enabled_from_the_monitor_environment(self):
        from ti_ops_director.config import load_config
        from ti_ops_director.models import ModelNotPermitted, route
        env = dict(LIMITS, TI_OPS_OPUS_APPROVAL="Marc", TI_OPS_MODEL_OPUS="claude-opus-5-5")
        cfg = load_config(env=env)
        self.assertIsNone(cfg.opus_approval)
        with self.assertRaises(ModelNotPermitted):
            route(cfg, task_type="triage", want_tier="opus", escalation_reason="complex_investigation")
        prov, _ = self.run_monitor(env=dict(LIMITS, TI_OPS_MAX_MODEL_CALLS_PER_TASK="4"), replies=["x", "x", "x", "x"])
        self.assertNotIn("claude-opus-5-5", [c["model"] for c in prov.calls])


if __name__ == "__main__":
    unittest.main()


class EvaluationHarness(unittest.TestCase):
    """Synthetic cases (see the fixture's label): they test the harness and code guards, not model quality."""
    def test_cases_replay_through_gateway_and_guards(self):
        from ti_ops_director.config import load_config
        from ti_ops_director.monitor_bridge import MonitorSpend
        from ti_ops_director.monitor_eval import evaluate
        doc = json.loads((ROOT / "tests/fixtures/ops_director/monitor_eval_cases.json").read_text())
        outputs = [json.dumps({"decisions": [c["model_output"]]}) for c in doc["cases"]]
        tmp = tempfile.mkdtemp()
        cfg = load_config(env=dict(LIMITS, TI_OPS_MAX_MODEL_CALLS_PER_TASK="2"))
        cfg.max_output_tokens = 2200
        spend = MonitorSpend(cfg, {}, workdir=tmp, provider=FakeProvider(outputs, tmp))
        result = evaluate(doc, spend, monitor)
        self.assertEqual((result["evaluated"], result["not_run"]), (7, 0))
        self.assertEqual(result["correct"], 7)
        self.assertTrue(all(r["model"] == "claude-haiku-5-5" for r in result["cases"]))

    def test_eval_without_allowance_runs_nothing(self):
        from ti_ops_director.config import load_config
        from ti_ops_director.monitor_bridge import MonitorSpend
        from ti_ops_director.monitor_eval import evaluate
        doc = json.loads((ROOT / "tests/fixtures/ops_director/monitor_eval_cases.json").read_text())
        spend = MonitorSpend(load_config(env={"TI_OPS_DRY_RUN": "false"}), {}, workdir=tempfile.mkdtemp(),
                             provider=FakeProvider([], tempfile.mkdtemp()))
        result = evaluate(doc, spend, monitor)
        self.assertEqual((result["evaluated"], result["not_run"]), (0, 7))
