"""Optional ACC reporting (docs/ACC_REPORTING.md): inert by default, never changes an agent's behaviour,
maps each agent's own outcome to ACC schema 1.1, and signs requests the ACC can verify. Offline."""
import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ti_ops_director import agent_outcome  # noqa: E402
from ti_ops_director import production as pc  # noqa: E402
from ti_ops_director import country_engine  # noqa: E402
import acc_report  # noqa: E402
from test_ti_ops_director_production import (NOW, FakeAPI, healthy_runs, make_root, ok_validator, run,  # noqa: E402
                                              site_for)
from country_support import make_builder  # noqa: E402
from datetime import timedelta  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MASTER, BUILDER, PC = acc_report.MASTER, acc_report.BUILDER, acc_report.CONTROLLER
ENV_RUN = {"GITHUB_RUN_ID": "123456", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_EVENT_NAME": "schedule"}


class Inert(unittest.TestCase):
    def test_write_is_a_no_op_without_the_variable(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(agent_outcome.write({}, MASTER, {"x": 1}))
            self.assertFalse(agent_outcome.write({"TI_AGENT_OUTCOME_FILE": ""}, MASTER, {"x": 1}))
            self.assertEqual(os.listdir(tmp), [])

    def test_write_never_raises(self):
        self.assertFalse(agent_outcome.write({"TI_AGENT_OUTCOME_FILE": "/nonexistent/dir/x.json"}, MASTER, {"x": 1}))
        self.assertFalse(agent_outcome.write(None, MASTER, {"x": object()}))

    def test_write_records_agent_and_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "o.json")
            self.assertTrue(agent_outcome.write({"TI_AGENT_OUTCOME_FILE": p}, BUILDER, {"exit_code": 0}))
            doc = json.load(open(p))
            self.assertEqual((doc["agent"], doc["exit_code"], doc["schema"]), (BUILDER, 0, "ti-agent-outcome/1"))
            self.assertTrue(doc["finished_at_utc"].endswith("Z"))


class ProductionControllerUnchanged(unittest.TestCase):
    """Same inputs, with and without the outcome file: identical exit code, output, GITHUB_OUTPUT, summary, state."""

    def pass_(self, mode, with_file):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(tmp)
            r = healthy_runs()
            r["continuous-intelligence-monitor.yml"] = [run(1, NOW - timedelta(minutes=300))]
            env = {"TI_PC_MODE": mode, "TI_PC_STATE_BACKEND": "file", "TI_PC_STATE_DIR": str(Path(tmp) / "st"),
                   "GITHUB_OUTPUT": str(Path(tmp) / "out"), "GITHUB_STEP_SUMMARY": str(Path(tmp) / "sum")}
            if with_file:
                env["TI_AGENT_OUTCOME_FILE"] = str(Path(tmp) / "agent_outcome.json")
            lines = []
            code = pc.run(env=env, root=root, api=FakeAPI(runs=r, files=site_for(root)[1]), http=site_for(root)[0],
                          validator=ok_validator, now=NOW, out=lines.append)
            st = Path(env["TI_PC_STATE_DIR"])
            state = sorted((p.name, p.read_text()) for p in st.rglob("*") if p.is_file()) if st.exists() else []
            rec = json.load(open(env["TI_AGENT_OUTCOME_FILE"])) if with_file else None
            return (code, lines, Path(env["GITHUB_OUTPUT"]).read_text(), Path(env["GITHUB_STEP_SUMMARY"]).read_text(), state), rec

    def test_observe_and_active_identical(self):
        for mode in ("observe", "active"):
            plain, _ = self.pass_(mode, False)
            hooked, rec = self.pass_(mode, True)
            self.assertEqual(plain, hooked, mode)
            self.assertEqual(rec["agent"], PC)
            self.assertEqual(rec["mode"], mode)
            self.assertEqual(rec["verdict"], "FAILING")

    def test_outcome_maps_to_idle_in_observe_and_awaiting_approval_in_active(self):
        _, obs = self.pass_("observe", True)
        _, act = self.pass_("active", True)
        e1 = acc_report.build_event("end", PC, ENV_RUN, obs, "success")
        e2 = acc_report.build_event("end", PC, ENV_RUN, act, "failure")
        self.assertEqual((e1["status"], e1["dry_run"], e1["model_tier"], e1["cost_eur"]), ("idle", True, "code", 0.0))
        self.assertEqual(e2["status"], "awaiting_approval")
        self.assertFalse(e2["dry_run"])
        self.assertIn("need a decision", e2["approval_request"]["summary"])


class CountryBuilderUnchanged(unittest.TestCase):
    def test_capturing_facts_does_not_change_the_run(self):
        outs = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as tmp:
                b = make_builder(tmp)
                code = b.run()
                outs.append((code, [l.replace(tmp, "<tmp>") for l in b.captured]))
                facts = b.last_outcome
        self.assertEqual(outs[0], outs[1])
        self.assertIn("exceptions_need_decision", facts)
        self.assertEqual(facts["model_calls"], 0)

    def test_outcome_writer_never_raises_and_preserves_exit_code(self):
        class Broken:
            cfg = None
        country_engine._write_agent_outcome({"TI_AGENT_OUTCOME_FILE": "/tmp/x.json"}, Broken(), 0)   # must not raise
        with tempfile.TemporaryDirectory() as tmp:
            b = make_builder(tmp)
            code = b.run()
            p = str(Path(tmp) / "agent_outcome.json")
            country_engine._write_agent_outcome({"TI_AGENT_OUTCOME_FILE": p}, b, code)
            rec = json.load(open(p))
            self.assertEqual((rec["agent"], rec["exit_code"]), (BUILDER, code))
            ev = acc_report.build_event("end", BUILDER, ENV_RUN, rec, "success")
            self.assertIn(ev["status"], ("idle", "awaiting_approval"))
            self.assertEqual(ev["model_tier"], "code")
            self.assertEqual(ev["cost_eur"], 0.0)


class MonitorEntryPoint(unittest.TestCase):
    """The __main__ wrapper keeps the monitor's exit codes and writes the record only when asked."""

    DRIVER = r'''
import os, sys
path = sys.argv[1]
src = open(path).read().replace("def main(env=None):", "def main(env=None):\n    return _fake()\ndef _real_main(env=None):", 1)
g = {"__name__": "__main__", "__file__": path}
def _fake():
    v = os.environ["FAKE"]
    if v == "raise":
        raise RuntimeError("boom")
    g["_LAST_OUTCOME"] = {"triage": "NOT_RUN_DRY_RUN", "calls": 0, "estimatedUsd": 0.0017, "headlines": {"collected": 5, "new": 2, "pending": 2}, "spend": {"mode": "dry_run"}}
    return int(v)
g["_fake"] = _fake
exec(compile(src, path, "exec"), g)
'''

    def drive(self, fake, outfile=None):
        env = dict(os.environ, FAKE=fake)
        env.pop("TI_AGENT_OUTCOME_FILE", None)
        if outfile:
            env["TI_AGENT_OUTCOME_FILE"] = outfile
        return subprocess.run([sys.executable, "-c", self.DRIVER, str(ROOT / "scripts/continuous_intelligence_monitor.py")],
                              env=env, capture_output=True, text=True)

    def test_exit_codes_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            for fake, want in (("0", 0), ("3", 3), ("4", 4), ("raise", 1)):
                self.assertEqual(self.drive(fake).returncode, want, fake)
                p = os.path.join(tmp, fake + ".json")
                self.assertEqual(self.drive(fake, p).returncode, want, fake)
                rec = json.load(open(p))
                self.assertEqual((rec["agent"], rec["exit_code"]), (MASTER, want))
            self.assertEqual(json.load(open(os.path.join(tmp, "raise.json")))["error"], "RuntimeError")

    def test_dry_run_cycle_maps_to_idle_with_zero_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "o.json")
            self.drive("0", p)
            ev = acc_report.build_event("end", MASTER, ENV_RUN, json.load(open(p)), "success")
            self.assertEqual((ev["status"], ev["dry_run"], ev["model_tier"], ev["cost_eur"]), ("idle", True, "code", 0.0))
            self.assertNotIn("cost_usd_estimated", ev, "a dry-run estimate is not spend")


class Mapping(unittest.TestCase):
    def ev(self, agent, rec, step="success", env=None):
        return acc_report.build_event("end", agent, env or ENV_RUN, rec, step)

    def test_start_is_working_with_stable_event_id(self):
        e = acc_report.build_event("start", BUILDER, ENV_RUN)
        self.assertEqual(e["status"], "working")
        self.assertEqual(e["event_id"], "gh-run-123456-2-start")
        self.assertEqual(e["agent_id"], BUILDER)
        self.assertRegex(e["timestamp"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

    def test_missing_outcome_is_unknown_or_failed(self):
        self.assertEqual(self.ev(MASTER, None, "success")["status"], "unknown")
        self.assertEqual(self.ev(BUILDER, None, "failure")["status"], "failed")
        self.assertEqual(self.ev(PC, None, "cancelled")["status"], "failed")

    def test_master_states(self):
        base = {"agent": MASTER, "exit_code": 0, "outcome": {"triage": "COMPLETED", "calls": 2, "model": "claude-haiku-5-5",
                                                            "estimatedUsd": 0.0039, "spend": {"mode": "live"}, "headlines": {}}}
        e = self.ev(MASTER, base)
        self.assertEqual((e["status"], e["model_tier"], e["dry_run"]), ("idle", "haiku", False))
        self.assertNotIn("cost_eur", e, "no EUR figure without a configured rate")
        self.assertEqual(e["cost_usd_estimated"], 0.0039)
        e = self.ev(MASTER, base, env=dict(ENV_RUN, ACC_EUR_PER_USD="0.86", ACC_EUR_RATE_DATE="2026-10-22"))
        self.assertAlmostEqual(e["cost_eur"], 0.003354)
        for triage, code, want in (("FAILED", 0, "failed"), ("NOT_RUN_NOT_CONFIGURED", 3, "failed"), ("COMPLETED", 4, "failed"), ("SOMETHING_NEW", 0, "unknown")):
            r = json.loads(json.dumps(base)); r["outcome"]["triage"] = triage; r["exit_code"] = code
            self.assertEqual(self.ev(MASTER, r)["status"], want, triage)

    def test_builder_states(self):
        f = {"tasks": 3, "published_records": 1, "exceptions_held": 2, "exceptions_need_decision": 0, "model_calls": 0}
        rec = lambda **k: dict({"agent": BUILDER, "exit_code": 0, "mode": "publish", "facts": f}, **k)
        self.assertEqual(self.ev(BUILDER, rec())["status"], "idle")
        self.assertFalse(self.ev(BUILDER, rec())["dry_run"])
        self.assertTrue(self.ev(BUILDER, rec(mode="observe"))["dry_run"])
        e = self.ev(BUILDER, rec(facts=dict(f, exceptions_need_decision=2)))
        self.assertEqual(e["status"], "awaiting_approval")
        self.assertIn("2 held exception(s)", e["approval_request"]["summary"])
        self.assertEqual(self.ev(BUILDER, rec(exit_code=5, facts=dict(f, publication_failures=["rollback_failed"])))["status"], "failed")
        self.assertEqual(self.ev(BUILDER, rec(exit_code=3, config_errors=1))["status"], "failed")
        self.assertEqual(self.ev(BUILDER, rec(mode="off", facts=None))["status"], "offline")
        self.assertEqual(self.ev(BUILDER, rec(facts=None))["status"], "unknown")
        e = self.ev(BUILDER, rec(facts=dict(f, model_calls=2, models=["claude-haiku-5-5"], estimated_cost_usd=0.002)))
        self.assertEqual(e["model_tier"], "haiku")

    def test_controller_states(self):
        rec = lambda **k: dict({"agent": PC, "exit_code": 0, "mode": "observe", "verdict": "OK", "checks": 9}, **k)
        self.assertEqual(self.ev(PC, rec())["status"], "idle")
        self.assertEqual(self.ev(PC, rec(exit_code=3, config_errors=1))["status"], "failed")
        self.assertEqual(self.ev(PC, rec(verdict=None))["status"], "unknown")

    def test_only_verified_agents_and_bounded_task(self):
        with self.assertRaises(ValueError):
            acc_report.build_event("start", "sqe", ENV_RUN)
        r = {"agent": BUILDER, "exit_code": 5, "mode": "publish", "facts": {"publication_failures": ["x" * 300]}}
        self.assertLessEqual(len(self.ev(BUILDER, r)["task"]), 160)

    def test_outcome_for_another_agent_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "o.json")
            json.dump({"agent": PC, "exit_code": 0}, open(p, "w"))
            self.assertIsNone(acc_report.read_outcome(p, BUILDER))


class Transport(unittest.TestCase):
    SECRET = "c2VjcmV0LXNlY3JldC1zZWNyZXQtc2VjcmV0LTEyMzQ"

    def server(self, status=200):
        got = {}
        secret = self.SECRET

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                got["headers"], got["body"] = {k.lower(): v for k, v in self.headers.items()}, body
                got["valid"] = hmac.compare_digest(acc_report.sign(secret, "POST", self.path, self.headers["X-ACC-Timestamp"],
                                                                   self.headers["X-ACC-Nonce"], body), self.headers["X-ACC-Signature"])
                self.send_response(status); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(json.dumps({"accepted": 1, "results": [{"flags": []}]}).encode())

            def log_message(self, *a):
                pass
        srv = HTTPServer(("127.0.0.1", 0), H)
        self.addCleanup(srv.server_close)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, got

    def env(self, port, **k):
        return dict(ENV_RUN, ACC_REPORT_URL=f"http://127.0.0.1:{port}", ACC_KEY_ID="k_test", ACC_AGENT_SECRET=self.SECRET, **k)

    def test_signed_post(self):
        srv, got = self.server()
        try:
            status, _ = acc_report.post(self.env(srv.server_port), acc_report.build_event("start", PC, ENV_RUN))
            self.assertEqual(status, "ACCEPTED")
            self.assertTrue(got["valid"])
            self.assertEqual(json.loads(got["body"])["events"][0]["agent_id"], PC)
            self.assertEqual(got["headers"]["x-acc-key-id"], "k_test")
        finally:
            srv.shutdown()

    def test_malformed_secret_is_skipped_not_raised(self):
        env = dict(ENV_RUN, ACC_REPORT_URL="http://127.0.0.1:9", ACC_KEY_ID="k", ACC_AGENT_SECRET="x")
        self.assertEqual(acc_report.post(env, {})[0], "SKIPPED_BAD_SECRET")

    def test_not_configured_makes_no_call(self):
        self.assertEqual(acc_report.post(ENV_RUN, {})[0], "SKIPPED_NOT_CONFIGURED")

    def test_plain_http_to_a_remote_host_is_refused(self):
        env = dict(ENV_RUN, ACC_REPORT_URL="http://acc.example.com", ACC_KEY_ID="k", ACC_AGENT_SECRET=self.SECRET)
        self.assertEqual(acc_report.post(env, {})[0], "SKIPPED_INSECURE_URL")

    def test_main_always_exits_zero(self):
        import io, contextlib
        for argv, env in ((["end", "--agent", BUILDER], dict(ENV_RUN, ACC_REPORT_URL="http://127.0.0.1:9", ACC_KEY_ID="k", ACC_AGENT_SECRET=self.SECRET)),
                          (["start", "--agent", "not-an-agent"], ENV_RUN), (["bogus"], ENV_RUN), (["start", "--agent", PC], {})):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(acc_report.main(argv, env), 0, argv)
            self.assertTrue(buf.getvalue().startswith("ACC_REPORT"), buf.getvalue())

    def test_secret_never_printed(self):
        import io, contextlib
        srv, _ = self.server()
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                acc_report.main(["start", "--agent", PC], self.env(srv.server_port))
            self.assertNotIn(self.SECRET, buf.getvalue())
            self.assertIn("ACC_REPORT ACCEPTED", buf.getvalue())
        finally:
            srv.shutdown()


class Workflows(unittest.TestCase):
    """Text checks (no YAML dependency in CI): the report steps are gated, optional and cannot fail the job."""

    @staticmethod
    def steps(text):
        body = text.split("    steps:\n", 1)[1]
        return ["      - " + chunk for chunk in body.split("\n      - ")[1:]] if body.startswith("      - ") is False else \
            [("      - " + c) if not c.startswith("      - ") else c for c in body.split("\n      - ")]

    def test_report_steps_are_gated_optional_and_cannot_fail_the_job(self):
        for wf, step_id in (("continuous-intelligence-monitor.yml", "monitor"), ("country-intelligence-builder.yml", "cb"),
                            ("production-controller.yml", "pc")):
            text = (ROOT / ".github/workflows" / wf).read_text()
            steps = self.steps(text)
            acc = [s for s in steps if "name: ACC report" in s]
            self.assertEqual(len(acc), 2, wf)
            for s in acc:
                self.assertIn("continue-on-error: true", s, wf)
                self.assertIn("timeout-minutes: 1", s, wf)
                self.assertIn("vars.ACC_REPORTING_ENABLED == 'true'", s, wf)
                self.assertIn("github.event_name != 'pull_request'", s, wf)
                self.assertNotIn("ANTHROPIC", s, wf)
            self.assertIn("if: always() &&", acc[1], wf)
            agent = next(s for s in steps if f"id: {step_id}" in s)
            self.assertIn("TI_AGENT_OUTCOME_FILE: ${{ vars.ACC_REPORTING_ENABLED == 'true'", agent, wf)
            self.assertLess(steps.index(acc[0]), steps.index(agent))
            self.assertLess(steps.index(agent), steps.index(acc[1]))


if __name__ == "__main__":
    unittest.main()
