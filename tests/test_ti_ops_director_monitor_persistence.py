"""Shared monitor state on GitHub Actions: real git repositories, no network, no API key, no charge.

A bare repository stands in for GitHub; each "runner" is a separate shallow clone, as on Actions.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from email.utils import formatdate
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ops_support import ROOT   # noqa: E402
from test_ti_ops_director_monitor_integration import GOOD, LIMITS, FakeProvider, monitor  # noqa: E402

from ti_ops_director.budget import BudgetBlocked          # noqa: E402
from ti_ops_director.config import load_config            # noqa: E402
from ti_ops_director.monitor_bridge import RESERVATION_TTL_MINUTES, MonitorSpend  # noqa: E402
from ti_ops_director.monitor_state import GitStateBackend, StateSyncError, make_backend  # noqa: E402

PATHS = {"state": "data/continuous_monitor_state.json", "data": "data.json"}
BASE = {"countries": [{"id": "gb", "name": "United Kingdom"}], "records": [], "sources": [], "locations": [],
        "events": [], "sourceObservations": [], "change_log": [], "oilGasSummary": {},
        "meta": {"schemaVersion": "2.0", "counts": {}, "currentIntelligence": {}}}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


class Remote:
    """A bare 'origin' with main holding data.json and the monitor state."""
    def __init__(self, state=None):
        self.tmp = Path(tempfile.mkdtemp())
        self.bare = self.tmp / "origin.git"
        git(self.tmp, "init", "--quiet", "--bare", "-b", "main", str(self.bare))
        seed = self.tmp / "seed"
        git(self.tmp, "clone", "--quiet", str(self.bare), str(seed))
        (seed / "data").mkdir()
        (seed / "data.json").write_text(json.dumps(BASE))
        (seed / PATHS["state"]).write_text(json.dumps(state or {"seen": []}))
        git(seed, "add", ".")
        git(seed, "-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "--quiet", "-m", "seed")
        git(seed, "push", "--quiet", "origin", "HEAD:main")
        self.n = 0

    def runner(self):
        """A fresh shallow checkout, like actions/checkout with fetch-depth 1."""
        self.n += 1
        path = self.tmp / f"runner{self.n}"
        git(self.tmp, "clone", "--quiet", "--depth=1", "file://" + str(self.bare), str(path))
        return path

    def state(self):
        return json.loads(git(self.bare, "show", "main:" + PATHS["state"]))

    def data(self):
        return json.loads(git(self.bare, "show", "main:data.json"))

    def commits(self):
        return int(git(self.bare, "rev-list", "--count", "main"))


def spend_for(backend, workdir, provider=None, env=None):
    cfg = load_config(env=dict(LIMITS, **(env or {})))
    cfg.max_output_tokens = 2200
    return MonitorSpend(cfg, backend.snapshot().state(), workdir=workdir, provider=provider, backend=backend)


class GitCompareAndSwap(unittest.TestCase):
    def test_stale_writer_is_rejected_and_reapplies_on_the_new_tip(self):
        remote = Remote()
        a = GitStateBackend(remote.runner(), PATHS)
        b = GitStateBackend(remote.runner(), PATHS)
        attempts = []

        def add(name):
            def apply(snap):
                st = snap.state()
                st.setdefault("writers", []).append(name)
                if name == "b":
                    attempts.append(1)
                    if len(attempts) == 1:
                        a.transact(add("a"))      # a commits between b's read and b's push
                return {"state": json.dumps(st)}, None
            return apply

        b.transact(add("b"))
        self.assertEqual(remote.state()["writers"], ["a", "b"])   # nothing lost; b re-read after rejection
        self.assertEqual(len(attempts), 2)

    def test_unreachable_remote_fails_closed(self):
        remote = Remote()
        runner = remote.runner()
        git(runner, "remote", "set-url", "origin", str(remote.tmp / "missing.git"))
        with self.assertRaises(StateSyncError):
            GitStateBackend(runner, PATHS).snapshot()

    def test_unknown_backend_name_fails_closed(self):
        with self.assertRaises(StateSyncError):
            make_backend({"TI_MONITOR_STATE_BACKEND": "s3"}, ROOT, PATHS)


class SharedLimits(unittest.TestCase):
    """Daily and monthly limits hold across separate runs, retries and concurrent runs."""
    def reserve(self, spend, batch="batch-1", est_tokens=20000):
        spend.batch_key = batch
        tid, _ = spend.store.create_task(worker="m", task_type="triage", inputs={}, dedupe_key=batch + os.urandom(4).hex(),
                                         business_application="TI", substream="03AA-04", mode="live", paid=True)
        return spend.budget.reserve(tid, "m", "claude-haiku-5-5", "haiku", est_tokens, 2200)

    def test_concurrent_runs_cannot_jointly_exceed_the_daily_limit(self):
        remote = Remote()
        one_call = 0.0031          # ~20k input + 2.2k output tokens at Haiku 5.5 rates, conservatively priced
        env = {"TI_OPS_DAILY_LIMIT_USD": str(one_call * 1.5), "TI_OPS_PER_TASK_LIMIT_USD": "1"}
        run_a = spend_for(GitStateBackend(remote.runner(), PATHS), tempfile.mkdtemp(), env=env)
        run_b = spend_for(GitStateBackend(remote.runner(), PATHS), tempfile.mkdtemp(), env=env)
        # Both hydrated from the same empty state: each alone would fit. Only one reservation may land.
        self.reserve(run_a, "batch-a")
        with self.assertRaises(BudgetBlocked) as ctx:
            self.reserve(run_b, "batch-b")
        self.assertEqual(ctx.exception.reason, "daily_allowance_insufficient")
        self.assertEqual(len(remote.state()["spend"]["open"]), 1)

    def test_same_batch_is_not_triaged_twice_by_concurrent_runs(self):
        remote = Remote()
        run_a = spend_for(GitStateBackend(remote.runner(), PATHS), tempfile.mkdtemp())
        run_b = spend_for(GitStateBackend(remote.runner(), PATHS), tempfile.mkdtemp())
        self.reserve(run_a, "same-headlines")
        with self.assertRaises(BudgetBlocked) as ctx:
            self.reserve(run_b, "same-headlines")
        self.assertEqual(ctx.exception.reason, "concurrent_run_in_progress")

    def test_killed_run_keeps_its_reservation_and_a_rerun_is_limited_by_it(self):
        remote = Remote()
        env = {"TI_OPS_DAILY_LIMIT_USD": "0.005", "TI_OPS_PER_TASK_LIMIT_USD": "1"}
        killed = spend_for(GitStateBackend(remote.runner(), PATHS), tempfile.mkdtemp(), env=env)
        self.reserve(killed)                       # pushed; then the job dies before settling
        self.assertEqual(len(remote.state()["spend"]["open"]), 1)
        rerun = spend_for(GitStateBackend(remote.runner(), PATHS), tempfile.mkdtemp(), env=env)
        with self.assertRaises(BudgetBlocked):     # "Re-run failed jobs" cannot spend the same money again
            self.reserve(rerun, "batch-2")

    def test_abandoned_reservation_is_charged_in_full_after_the_ttl(self):
        remote = Remote()
        backend = GitStateBackend(remote.runner(), PATHS)
        killed = spend_for(backend, tempfile.mkdtemp())
        _, amount = self.reserve(killed)
        later = killed.store.clock() + timedelta(minutes=RESERVATION_TTL_MINUTES + 1)
        nxt = MonitorSpend(killed.cfg, remote.state(), workdir=tempfile.mkdtemp(), backend=backend, clock=lambda: later)
        self.reserve(nxt, "batch-2")
        st = remote.state()["spend"]
        self.assertEqual(st["expired"][0]["charged"], "in_full_unsettled")
        self.assertAlmostEqual(st["daily"][later.strftime("%Y-%m-%d")], round(amount, 6), places=6)

    def test_settlement_replaces_the_reservation_with_actual_cost(self):
        remote = Remote()
        prov_dir = tempfile.mkdtemp()
        spend = spend_for(GitStateBackend(remote.runner(), PATHS), prov_dir, provider=FakeProvider([GOOD], prov_dir))
        res = spend.triage("system", "user text", "batch-1", validator=lambda a: True)
        self.assertEqual(res.status, "ok")
        self.assertTrue(spend.checkpoint())
        st = remote.state()["spend"]
        self.assertEqual(st["open"], [])
        day = spend.store.clock().strftime("%Y-%m-%d")
        self.assertAlmostEqual(st["daily"][day], (2000 * 0.10 + 150 * 0.50) / 1e6, places=7)
        self.assertEqual(len(remote.state()["triageCache"]), 1)


class MonitorOnActions(unittest.TestCase):
    """monitor.main() with the git backend, as the workflow runs it."""
    def setUp(self):
        self.remote = Remote()
        self.items = [{"title": "Heathrow runway closed after incident", "url": "https://example.org/heathrow",
                       "date": formatdate(usegmt=True), "feed": "f"}]

    def main(self, env, provider=None, runner=None):
        runner = runner or self.remote.runner()
        summary = Path(tempfile.mkdtemp()) / "summary.md"
        full = {"TI_MONITOR_STATE_BACKEND": "git", "RUNNER_TEMP": tempfile.mkdtemp(),
                "ANTHROPIC_API_KEY": "test-key-not-real", "GITHUB_STEP_SUMMARY": str(summary), **env}
        provider = provider or FakeProvider([GOOD], full["RUNNER_TEMP"])
        out = io.StringIO()
        with mock.patch.object(monitor, "ROOT", runner), mock.patch.object(monitor, "discover", lambda: (list(self.items), [])), \
                mock.patch.dict(os.environ, full, clear=True), \
                mock.patch("ti_ops_director.monitor_bridge.AnthropicProvider", lambda key: provider), \
                contextlib.redirect_stdout(out):
            code = monitor.main(env=dict(os.environ))
        return code, provider, summary.read_text(), out.getvalue()

    def test_paid_run_persists_reservation_settlement_and_cursor(self):
        before = self.remote.commits()
        code, prov, summary, _ = self.main(LIMITS)
        st = self.remote.state()
        self.assertEqual((code, len(prov.calls)), (0, 1))
        self.assertEqual(st["lastOutcome"]["triage"], "COMPLETED")
        self.assertEqual(len(st["seen"]), 1)
        self.assertEqual(st["spend"]["open"], [])
        self.assertGreater(sum(st["spend"]["daily"].values()), 0)
        self.assertEqual(self.remote.commits() - before, 3)    # reservation, settlement, final state
        self.assertIn("Triage completed", summary)

    def test_next_run_on_a_new_runner_sees_the_previous_spend(self):
        self.main(LIMITS)
        spent = sum(self.remote.state()["spend"]["daily"].values())
        self.items = [{"title": "Different headline", "url": "https://example.org/b", "date": formatdate(usegmt=True), "feed": "f"}]
        code, prov, summary, _ = self.main(dict(LIMITS, TI_OPS_DAILY_LIMIT_USD=str(spent + 0.0001)))
        self.assertEqual(prov.calls, [])
        self.assertEqual(self.remote.state()["lastOutcome"]["triage"], "NOT_RUN_BUDGET_EXHAUSTED")
        self.assertIn("Triage did NOT complete", summary)
        self.assertEqual(code, 0)                               # an expected control, shown, not an error

    def test_unchanged_input_after_a_lost_final_write_makes_no_second_call(self):
        runner = self.remote.runner()
        real = GitStateBackend.transact

        def lose_final(self_, fn, message=""):
            if message.startswith("Continuous intelligence monitor:"):
                raise StateSyncError("simulated push failure")
            return real(self_, fn, message)
        with mock.patch.object(GitStateBackend, "transact", lose_final):
            code, prov, summary, _ = self.main(LIMITS, runner=runner)
        self.assertEqual((code, len(prov.calls)), (monitor.EXIT_FAILED, 1))   # visible failure
        self.assertNotIn("seen", [k for k, v in self.remote.state().items() if v])   # cursor not saved
        self.assertIn("State persisted | False", summary)
        code2, prov2, _, _ = self.main(LIMITS)                  # same headlines, same prompt, new runner
        self.assertEqual((code2, prov2.calls), (0, []))         # served from the persisted result cache
        self.assertEqual(self.remote.state()["lastOutcome"]["triage"], "COMPLETED")

    def test_publication_on_actions_writes_validated_data_in_the_same_commit(self):
        code, _, summary, _ = self.main(dict(LIMITS, TI_MONITOR_PUBLISH="true"))
        self.assertEqual(code, 0)
        self.assertEqual(len(self.remote.data()["events"]), 1)
        self.assertIn("events published 1", summary)


if __name__ == "__main__":
    unittest.main()
