"""Workflow success vs triage completion, publication fail-closed, configuration errors. Offline, no cost."""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ops_support import ROOT   # noqa: E402
from test_ti_ops_director_monitor_integration import GOOD, LIMITS, FakeProvider, MonitorHarness, monitor  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "continuous-intelligence-monitor.yml"


class PublicationFailsClosed(unittest.TestCase):
    def test_only_true_enables_publication(self):
        cases = {None: (False, "unset"), "": (False, "unset"), "false": (False, "false"), "FALSE": (False, "false"),
                 "true": (True, "true"), "True": (True, "true"), "yes": (False, "invalid"), "1": (False, "invalid"),
                 " true": (False, "invalid"), "true ": (False, "invalid"), "on": (False, "invalid")}
        for raw, expected in cases.items():
            env = {} if raw is None else {"TI_MONITOR_PUBLISH": raw}
            self.assertEqual(monitor.publication_setting(env), expected, raw)

    def test_trace_workflow_to_application(self):
        """The repository variable reaches the monitor through exactly one env line; an unset variable is ''."""
        text = WORKFLOW.read_text()
        self.assertEqual(re.findall(r"TI_MONITOR_PUBLISH:.*", text), ["TI_MONITOR_PUBLISH: ${{ vars.TI_MONITOR_PUBLISH }}"])
        for other in WORKFLOW.parent.glob("*.yml"):           # elsewhere it may only be pinned off
            if other != WORKFLOW:
                for line in re.findall(r"TI_MONITOR_PUBLISH:.*", other.read_text()):
                    self.assertEqual(line, 'TI_MONITOR_PUBLISH: "false"', other.name)
        readers = [p.name for p in (ROOT / "scripts").rglob("*.py") if "TI_MONITOR_PUBLISH" in p.read_text()]
        self.assertEqual(readers, ["continuous_intelligence_monitor.py"])
        self.assertIn("publication_setting(env)", (ROOT / "scripts" / "continuous_intelligence_monitor.py").read_text())


class PublicationEndToEnd(MonitorHarness):
    def test_unset_empty_and_invalid_values_publish_nothing(self):
        before = self.data_path.read_text()
        for value in (None, "", "yes", "1", "TRUE ", "enabled"):
            env = dict(LIMITS) if value is None else dict(LIMITS, TI_MONITOR_PUBLISH=value)
            _, state = self.run_monitor(env=env, replies=[GOOD], state={"seen": []})
            self.assertEqual(self.data_path.read_text(), before, value)
            self.assertFalse(state["lastOutcome"]["publication"]["enabled"])
        self.assertIn("Publication disabled", self.last_out)          # invalid value is called out

    def test_true_publishes_one_attributed_early_report(self):
        _, state = self.run_monitor(env=dict(LIMITS, TI_MONITOR_PUBLISH="true"), replies=[GOOD])
        self.assertEqual(state["lastOutcome"]["publication"]["published"], 1)


class OutcomeVisibility(MonitorHarness):
    def summary(self, env, replies=(GOOD,), expect_code=0, provider=None, state=None):
        path = self.tmp / "summary.md"
        path.write_text("")
        _, st = self.run_monitor(env=dict(env, GITHUB_STEP_SUMMARY=str(path)), replies=list(replies),
                                 expect_code=expect_code, provider=provider, state=state)
        return path.read_text(), st

    def test_dry_run_is_not_reported_as_triage(self):
        text, st = self.summary({})
        self.assertIn("| Workflow run | executed (this is not a statement about triage) |", text)
        self.assertIn("NOT COMPLETED: `NOT_RUN_DRY_RUN`", text)
        self.assertIn("Triage did NOT complete", text)
        self.assertEqual((st["lastOutcome"]["triage"], st["lastOutcome"]["triageCompleted"]), ("NOT_RUN_DRY_RUN", False))

    def test_budget_exhausted_is_visible(self):
        today = monitor.now_utc().strftime("%Y-%m-%d")
        text, st = self.summary(LIMITS, state={"seen": [], "spend": {"daily": {today: 0.2}, "monthly": {today[:7]: 0.2}}})
        self.assertIn("NOT_RUN_BUDGET_EXHAUSTED", text)
        self.assertIn("daily_allowance_insufficient", text)

    def test_token_cap_counts_as_budget_exhausted(self):
        today = monitor.now_utc().strftime("%Y-%m-%d")
        text, _ = self.summary(LIMITS, state={"seen": [], "budget": {"date": today, "input": 10**9, "output": 0}})
        self.assertIn("NOT_RUN_BUDGET_EXHAUSTED", text)

    def test_misconfiguration_fails_the_job(self):
        text, st = self.summary({"TI_OPS_DRY_RUN": "false"}, expect_code=monitor.EXIT_NOT_CONFIGURED)
        self.assertIn("NOT_RUN_NOT_CONFIGURED", text)
        self.assertIn("::error title=Haiku triage not completed", self.last_out)

    def test_completed_triage(self):
        text, st = self.summary(LIMITS)
        self.assertIn("Triage completed for this run's new headlines.", text)
        self.assertEqual(st["consecutiveUntriaged"], 0)
        self.assertIn("::notice title=Haiku triage completed", self.last_out)

    def test_no_new_headlines_is_not_a_failure(self):
        self.items = []
        text, st = self.summary(LIMITS, replies=())
        self.assertIn("NO_NEW_HEADLINES", text)

    def test_repeated_provider_failures_turn_the_job_red(self):
        class Boom(FakeProvider):
            def complete(self, **kw):
                raise TimeoutError()
        state = None
        for run in range(1, monitor.MAX_CONSECUTIVE_FAILED + 1):
            code = 0 if run < monitor.MAX_CONSECUTIVE_FAILED else monitor.EXIT_FAILED
            _, state = self.summary(dict(LIMITS, TI_OPS_DAILY_LIMIT_USD="5"), provider=Boom([], self.tmp),
                                    expect_code=code)
        self.assertEqual(state["consecutiveFailed"], monitor.MAX_CONSECUTIVE_FAILED)
        self.assertIn("Triage stalled", self.last_out)

    def test_long_budget_exhaustion_stays_green_but_visible(self):
        today = monitor.now_utc().strftime("%Y-%m-%d")
        for _ in range(monitor.MAX_CONSECUTIVE_FAILED + 1):
            text, state = self.summary(LIMITS, state=dict(json.loads(self.state_path.read_text()) if self.state_path.exists()
                                                          else {"seen": []},
                                                          budget={"date": today, "input": 10**9, "output": 0}))
        self.assertEqual(state["consecutiveUntriaged"], monitor.MAX_CONSECUTIVE_FAILED + 1)
        self.assertEqual(state["consecutiveFailed"], 0)
        self.assertIn("NOT_RUN_BUDGET_EXHAUSTED", text)


class OperationalReport(MonitorHarness):
    def test_report_shows_untriaged_state_from_the_monitor(self):
        import tempfile
        from ti_ops_director.config import load_config
        from ti_ops_director.controller import Controller
        from ti_ops_director.report import build_report, render_markdown
        from ti_ops_director.store import Store
        self.run_monitor(env={}, replies=[GOOD])                      # dry-run: headlines kept, triage not run
        repo = Path(tempfile.mkdtemp())
        (repo / "data").mkdir()
        (repo / "data" / "continuous_monitor_state.json").write_text(self.state_path.read_text())
        cfg = load_config(env={"TI_OPS_REPO_ROOT": str(repo), "TI_OPS_DB_PATH": str(repo / "ops.sqlite3")})
        text = render_markdown(build_report(Controller(cfg, Store(cfg.db_path))))
        self.assertIn("Last triage: NOT COMPLETED (NOT_RUN_DRY_RUN); reason: dry_run", text)
        self.assertIn("New headlines left untriaged at last run: 1; consecutive runs untriaged: 1", text)


class ConfigurationErrors(MonitorHarness):
    def test_invalid_or_infinite_limits_block_paid_execution(self):
        for bad in ("abc", "inf", "nan", "1e999"):
            prov, state = self.run_monitor(env=dict(LIMITS, TI_OPS_DAILY_LIMIT_USD=bad), replies=[GOOD],
                                           expect_code=monitor.EXIT_NOT_CONFIGURED, state={"seen": []})
            self.assertEqual(prov.calls, [], bad)
            self.assertTrue(state["lastTriageBlocked"]["reason"].startswith("config_error"), bad)

    def test_zero_or_negative_limits_block(self):
        for bad in ("0", "-1"):
            prov, state = self.run_monitor(env=dict(LIMITS, TI_OPS_MONTHLY_LIMIT_USD=bad), replies=[GOOD],
                                           expect_code=monitor.EXIT_NOT_CONFIGURED, state={"seen": []})
            self.assertEqual((prov.calls, state["lastTriageBlocked"]["reason"]), ([], "allowance_not_configured"))

    def test_unrecognised_dry_run_value_stays_dry_run(self):
        prov, state = self.run_monitor(env=dict(LIMITS, TI_OPS_DRY_RUN="maybe"), replies=[GOOD])
        self.assertEqual(prov.calls, [])
        self.assertEqual(state["lastOutcome"]["triage"], "NOT_RUN_DRY_RUN")
        self.assertIn("dry-run kept on", self.last_out)


if __name__ == "__main__":
    unittest.main()
