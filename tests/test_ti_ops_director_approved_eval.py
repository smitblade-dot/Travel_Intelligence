"""The separately approved, bounded live evaluation. MOCKED provider and fixed rate: no network, no charge."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ops_support import ROOT   # noqa: E402
from test_ti_ops_director_monitor_eval import DOC, replies  # noqa: E402
from test_ti_ops_director_monitor_integration import FakeProvider, monitor  # noqa: E402
from test_ti_ops_director_monitor_persistence import Remote, git  # noqa: E402

from ti_ops_director import monitor_eval  # noqa: E402
from ti_ops_director.monitor_state import GitStateBackend  # noqa: E402

PATHS = {"state": "data/ops_director_eval_ledger.json", "results": "docs/evidence/ops_director_haiku_eval.json"}
APPROVAL = json.loads((ROOT / "config" / "ops_director_eval_approval.json").read_text())
RATE = {"source": "test", "url": monitor_eval.ECB_URL, "reference_date": "2026-10-20", "usd_per_eur": 1.1612}
ENV = {"ANTHROPIC_API_KEY": "test-key-not-real"}


def ledger(remote):
    return json.loads(git(remote.bare, "show", "main:" + PATHS["state"]))


class Conversion(unittest.TestCase):
    def test_conservative_cap(self):
        self.assertEqual(monitor_eval.conservative_usd_cap(1.0, 1.1612, 0.10, 1.00), 1.00)   # ceiling applies
        self.assertEqual(monitor_eval.conservative_usd_cap(1.0, 1.0499, 0.10, 1.00), 0.94)   # rounded down
        self.assertEqual(monitor_eval.conservative_usd_cap(1.0, 0.98, 0.10, 1.00), 0.88)

    def test_parse_ecb_xml(self):
        xml = ("<gesmes:Envelope><Cube><Cube time='2026-10-20'><Cube currency='USD' rate='1.1612'/>"
               "<Cube currency='JPY' rate='170.10'/></Cube></Cube></gesmes:Envelope>")
        self.assertEqual(monitor_eval.parse_ecb(xml), ("2026-10-20", 1.1612))
        with self.assertRaises(monitor_eval.EvalRefused):
            monitor_eval.parse_ecb("<html>maintenance</html>")

    def test_unreachable_ecb_refuses(self):
        def boom(url):
            raise OSError("no route")
        with self.assertRaises(monitor_eval.EvalRefused):
            monitor_eval.fetch_ecb_rate(boom)

    def test_approval_file_is_bounded_to_the_evaluation(self):
        self.assertEqual((APPROVAL["approvedBy"], APPROVAL["purpose"], APPROVAL["capEur"]),
                         ("Marc", "haiku_quality_evaluation", 1.0))
        self.assertIn("not ongoing paid operation", APPROVAL["scope"])


class Refusals(unittest.TestCase):
    def run_eval(self, approval=APPROVAL, rate=RATE, env=ENV, provider=None):
        remote = Remote()
        prov = provider or FakeProvider(replies(), tempfile.mkdtemp())
        backend = GitStateBackend(remote.runner(), PATHS)
        return monitor_eval.run_approved_eval(approval, backend, env, monitor, DOC, rate, "2026-10-21", provider=prov), prov

    def test_stale_rate_unapproved_purpose_and_missing_key_make_no_call(self):
        prov = FakeProvider(replies(), tempfile.mkdtemp())
        for kwargs in ({"rate": dict(RATE, reference_date="2026-10-01")},
                       {"approval": dict(APPROVAL, purpose="ongoing_operation")},
                       {"approval": dict(APPROVAL, approvedBy="someone else")}):
            with self.assertRaises(monitor_eval.EvalRefused):
                self.run_eval(provider=prov, **kwargs)
        remote = Remote()
        with self.assertRaises(monitor_eval.EvalRefused):
            monitor_eval.run_approved_eval(APPROVAL, GitStateBackend(remote.runner(), PATHS), {}, monitor, DOC, RATE,
                                           "2026-10-21")
        self.assertEqual(prov.calls, [])


class BoundedRun(unittest.TestCase):
    def test_run_records_usage_ledger_and_is_not_repeated(self):
        remote = Remote()
        backend = GitStateBackend(remote.runner(), PATHS)
        prov = FakeProvider(replies(), tempfile.mkdtemp())
        # The mocked provider is not the real one, so the result is labelled MOCKED and never marked complete.
        r = monitor_eval.run_approved_eval(APPROVAL, backend, ENV, monitor, DOC, RATE, "2026-10-21", provider=prov)
        self.assertEqual(r["labelled"]["measurement"], monitor_eval.MOCKED)
        self.assertEqual(r["usage"]["calls_billed"], len(prov.calls))
        self.assertEqual(r["cap"]["usd"], 1.00)
        self.assertLessEqual(r["ledger"]["total_committed_usd"], r["cap"]["usd"])
        self.assertAlmostEqual(r["ledger"]["total_committed_usd"], r["usage"]["actual_usd"], places=6)
        st = ledger(remote)
        self.assertEqual(st["spend"]["open"], [])
        self.assertIn("approval", st)
        pushed = json.loads(git(remote.bare, "show", "main:" + PATHS["results"]))
        self.assertEqual(pushed["usage"]["calls_billed"], len(prov.calls))

    def test_completed_live_result_is_not_rerun(self):
        remote = Remote()
        backend = GitStateBackend(remote.runner(), PATHS)
        fp = monitor_eval.eval_fingerprint(DOC, monitor, APPROVAL["model"])
        backend.transact(lambda snap: ({"state": json.dumps({"completed": {fp: {"run_at": "x"}}})}, None))
        prov = FakeProvider(replies(), tempfile.mkdtemp())
        r = monitor_eval.run_approved_eval(APPROVAL, backend, ENV, monitor, DOC, RATE, "2026-10-21", provider=prov)
        self.assertIn("skipped", r)
        self.assertEqual(prov.calls, [])

    def test_total_cap_holds_across_runs_and_counts_retries(self):
        remote = Remote()
        backend = GitStateBackend(remote.runner(), PATHS)
        # An earlier run already committed all but 0.002 USD of the 1.00 USD cap.
        backend.transact(lambda snap: ({"state": json.dumps({"spend": {"total": 0.998, "daily": {}, "monthly": {},
                                                                        "open": []}})}, None))
        bad_then_good = ["not json"] + replies()        # the first case needs its corrective retry
        prov = FakeProvider(bad_then_good, tempfile.mkdtemp())
        r = monitor_eval.run_approved_eval(APPROVAL, backend, ENV, monitor, DOC, RATE, "2026-10-21", provider=prov)
        st = ledger(remote)
        self.assertLessEqual(st["spend"]["total"], 1.00 + 1e-9)
        blocked = [c for c in r["labelled"]["cases"] if c["reason"] == "total_allowance_insufficient"]
        self.assertGreater(len(blocked), 0)                       # the cap stopped further calls
        self.assertGreaterEqual(len(prov.calls), 1)
        self.assertLess(len(prov.calls), len(bad_then_good))


if __name__ == "__main__":
    unittest.main()
