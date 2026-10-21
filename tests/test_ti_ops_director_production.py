"""03AA-04_Production_Controller_TI. Offline: GitHub, Pages and the public site are FIXTURE fakes, no network."""
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ops_support import ROOT, Clock, make_controller

from ti_ops_director import production as pc
from ti_ops_director.monitor_state import FileStateBackend
from ti_ops_director.production import (EXIT_CONFIG, EXIT_NEW_CRITICAL, EXIT_OK, STATE_PATHS, BranchStateBackend,
                                        GitHubAPI, HttpReply, Unavailable, load_pc_config)
from ti_ops_director.production_worker import ProductionVerificationWorker
from ti_ops_director.workers import default_registry

NOW = datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)      # 23:00 Nicosia; today's 21:00 slot is 18:00 UTC
SITE = "https://travelintelligence22.com/"
LEGAL = "FORGEDTITANIUM LTD a Company Registered in the Republic of Cyprus, Reg No: HE 490451"


def ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def run(rid, created, conclusion="success", status="completed", event="schedule"):
    return {"id": rid, "event": event, "status": status, "conclusion": conclusion if status == "completed" else None,
            "created_at": ts(created), "run_started_at": ts(created), "html_url": f"https://example.invalid/{rid}",
            "head_sha": "a" * 40}


def job(name, conclusion="success", steps=()):
    return {"name": name, "conclusion": conclusion,
            "steps": [{"name": n, "conclusion": c} for n, c in steps]}


REFRESH_OK = [job("schedule_gate"), job("refresh", steps=[("Safety-net commit and push", "success"),
                                                           ("Audit discovery feed health", "success")])]


class FakeAPI(GitHubAPI):
    """FIXTURE GitHub: runs per workflow file, jobs per run, Pages build, file contents per ref."""
    def __init__(self, runs=None, jobs=None, pages=None, files=None, head=("b" * 40, NOW - timedelta(hours=2)),
                 down=()):
        self.repo = "smitblade-dot/Travel_Intelligence"
        self.runs, self.jobs, self.files, self.head, self.down = runs or {}, jobs or {}, files or {}, head, set(down)
        self.pages = pages if pages is not None else {"status": "built", "commit": "b" * 40,
                                                      "updated_at": ts(NOW - timedelta(hours=2))}
        self.dispatched = []

    def _gate(self, what):
        if what in self.down or "all" in self.down:
            raise Unavailable(f"GitHub API {what}: 503")

    def workflow_runs(self, workflow, branch, per_page=40):
        self._gate(workflow)
        return [dict(r) for r in self.runs.get(workflow, [])]

    def run_jobs(self, run_id):
        self._gate("jobs")
        return self.jobs.get(run_id, REFRESH_OK)

    def branch_head(self, branch):
        self._gate("commits")
        return self.head

    def pages_latest(self):
        self._gate("pages")
        return self.pages

    def file_at(self, path, ref):
        self._gate("contents")
        return self.files[(path, ref)]


def healthy_runs(now=NOW):
    slot = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)
    return {
        "daily-data-refresh.yml": [run(1, slot + timedelta(minutes=8))],
        "continuous-intelligence-monitor.yml": [run(100 + i, now - timedelta(minutes=15 * i + 3))
                                                for i in range(96)],
        "baseline-population.yml": [run(200, datetime(2026, 10, 21, 20, 35, tzinfo=timezone.utc) - timedelta(days=1))],
    }


class Site:
    """FIXTURE public site: serves given bodies; counts requests."""
    def __init__(self, files, status=200):
        self.files, self.status, self.calls = files, status, []

    def __call__(self, url, **kw):
        self.calls.append(url)
        name = url.split("?")[0].rsplit("/", 1)[-1]
        if self.status != 200:
            return HttpReply(self.status, b"", "http_error")
        return HttpReply(200, self.files[name])


def make_root(tmp, mutate=None, signals_age_h=7, monitor=None):
    root = Path(tempfile.mkdtemp(dir=tmp)) / "repo"
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "scripts").mkdir()
    shutil.copy(ROOT / "config/production_controller.json", root / "config/production_controller.json")
    shutil.copy(ROOT / "scripts/validate_ti_data.py", root / "scripts/validate_ti_data.py")
    data = json.loads((ROOT / "data.json").read_text(encoding="utf-8"))
    data["meta"]["generated"] = ts(NOW - timedelta(hours=1, minutes=40))
    if mutate:
        mutate(data)
    (root / "data.json").write_text(json.dumps(data), encoding="utf-8")
    (root / "data/phase1_signals.json").write_text(json.dumps(
        {"generated": (NOW - timedelta(hours=signals_age_h)).isoformat()}))
    if monitor is not None:
        (root / "data/continuous_monitor_state.json").write_text(json.dumps(monitor))
    return root


def site_for(root):
    body = (Path(root) / "data.json").read_bytes()
    index = f"<html><footer>{LEGAL}</footer></html>".encode()
    files = {("data.json", "b" * 40): body, ("index.html", "b" * 40): index}
    return Site({"data.json": body, "index.html": index}), files


def cfg_for(root, **env):
    return load_pc_config(dict({"TI_PC_MODE": "observe"}, **env), root)


def ok_validator(_root):
    return True, "VALID"


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def evaluate(self, root=None, api=None, state=None, site=None, now=NOW, **env):
        root = root or make_root(self.tmp)
        default_site, files = site_for(root)
        api = api or FakeAPI(runs=healthy_runs(), files=files)
        api.files = api.files or files
        cfg = cfg_for(root, **env)
        results, new_state, delta, planned = pc.evaluate(cfg, api, root, now, state, http=site or default_site,
                                                         validator=ok_validator)
        return cfg, {r.name: r for r in results}, new_state, delta, planned

    def keys(self, result):
        return {f["key"]: f["severity"] for f in result.findings}


class ConfigTests(Harness):
    def test_unset_mode_is_off_and_makes_no_call(self):
        root = make_root(self.tmp)
        api, out = FakeAPI(down={"all"}), []
        code = pc.run(env={}, root=root, api=api, out=out.append)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("OFF (unset)", out[0])

    def test_manual_run_without_mode_is_one_read_only_observation(self):
        cfg = load_pc_config({"TI_PC_EVENT": "workflow_dispatch"}, make_root(self.tmp))
        self.assertEqual((cfg.mode, cfg.mode_source), ("observe", "manual_default"))

    def test_invalid_mode_fails_closed(self):
        root = make_root(self.tmp)
        out = []
        self.assertEqual(pc.run(env={"TI_PC_MODE": "on"}, root=root, out=out.append), EXIT_CONFIG)
        self.assertIn("stays off", out[0])

    def test_active_needs_a_state_branch_and_never_main(self):
        root = make_root(self.tmp)
        self.assertTrue(load_pc_config({"TI_PC_MODE": "active"}, root).errors)
        cfg = load_pc_config({"TI_PC_MODE": "active", "TI_PC_STATE_BRANCH": "main"}, root)
        self.assertIsNone(cfg.state_branch)
        self.assertTrue(any("production branch" in e for e in cfg.errors))
        self.assertFalse(load_pc_config({"TI_PC_MODE": "active", "TI_PC_STATE_BRANCH": "ti-ops-state"}, root).errors)

    def test_recovery_allow_list_only_accepts_dispatchable_workflows_and_is_capped(self):
        root = make_root(self.tmp)
        cfg = load_pc_config({"TI_PC_MODE": "observe", "TI_PC_MAX_DISPATCHES_PER_DAY": "50",
                              "TI_PC_RECOVERY_WORKFLOWS": "continuous-intelligence-monitor.yml, baseline-population.yml,"
                                                          "other.yml"}, root)
        self.assertEqual(cfg.recovery_workflows, ("continuous-intelligence-monitor.yml",))
        self.assertEqual(cfg.max_dispatches_per_day, 3)
        self.assertEqual(len(cfg.warnings), 2)
        self.assertEqual(load_pc_config({"TI_PC_MODE": "observe"}, root).recovery_workflows, ())

    def test_site_url_must_be_https(self):
        cfg = load_pc_config({"TI_PC_MODE": "observe", "TI_PC_SITE_URL": "http://x.example"}, make_root(self.tmp))
        self.assertTrue(cfg.errors)


class DailyWorkflowTests(Harness):
    SLOT = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)

    def daily(self, runs, now=NOW, jobs=None):
        r = healthy_runs()
        r["daily-data-refresh.yml"] = runs
        root = make_root(self.tmp)
        _, files = site_for(root)
        _, res, *_ = self.evaluate(root=root, api=FakeAPI(runs=r, jobs=jobs or {}, files=files), now=now)
        return res["workflow:daily_refresh"]

    def test_on_time_success_is_ok(self):
        res = self.daily([run(1, self.SLOT + timedelta(minutes=5))])
        self.assertEqual((res.status, res.findings), ("OK", []))

    def test_missed_slot_is_critical_and_recoverable(self):
        res = self.daily([run(1, self.SLOT - timedelta(days=1, minutes=-5))], now=self.SLOT + timedelta(hours=3, minutes=5))
        f = res.findings[0]
        self.assertEqual((f["key"], f["severity"], f["dispatch"]),
                         ("workflow:daily_refresh:missed", "critical", "daily-data-refresh.yml"))

    def test_not_started_after_late_threshold_is_a_warning(self):
        res = self.daily([run(1, self.SLOT - timedelta(days=1))], now=self.SLOT + timedelta(minutes=90))
        self.assertEqual(self.keys(res), {"workflow:daily_refresh:delayed": "warning"})

    def test_inside_grace_judges_the_previous_slot_so_a_miss_is_not_forgotten(self):
        res = self.daily([], now=self.SLOT + timedelta(minutes=20))
        self.assertEqual(res.facts["slot_utc"], "2026-10-20T18:00:00+00:00")
        self.assertEqual(self.keys(res), {"workflow:daily_refresh:missed": "critical"})

    def test_late_start_is_reported_even_when_it_succeeded(self):
        # The measured 20 October case: 21:21 UTC for an 18:00 UTC (21:00 Nicosia) slot.
        res = self.daily([run(1, self.SLOT + timedelta(minutes=201))], now=self.SLOT + timedelta(hours=4))
        self.assertEqual(self.keys(res), {"workflow:daily_refresh:delayed": "warning"})

    def test_failure_after_the_output_commit_is_post_output_and_not_redispatched(self):
        jobs = {1: [job("refresh", "failure", [("Safety-net commit and push", "success"),
                                               ("Audit discovery feed health", "failure")])]}
        res = self.daily([run(1, self.SLOT + timedelta(minutes=5), "failure")], jobs=jobs)
        f = res.findings[0]
        self.assertEqual((f["key"], f["severity"], f["dispatch"]),
                         ("workflow:daily_refresh:post_output_failure", "warning", None))
        self.assertIn("Audit discovery feed health", f["summary"])

    def test_failure_before_the_output_commit_is_critical(self):
        jobs = {1: [job("refresh", "failure", [("Collect Phase 1 open-data signals", "failure"),
                                               ("Safety-net commit and push", "skipped")])]}
        res = self.daily([run(1, self.SLOT + timedelta(minutes=5), "failure")], jobs=jobs)
        self.assertEqual(self.keys(res), {"workflow:daily_refresh:failed": "critical"})
        self.assertEqual(res.findings[0]["dispatch"], "daily-data-refresh.yml")

    def test_success_with_the_work_skipped_by_the_gate_is_critical(self):
        jobs = {1: [job("schedule_gate"), job("refresh", "skipped")]}
        res = self.daily([run(1, self.SLOT + timedelta(minutes=5))], jobs=jobs)
        self.assertEqual(self.keys(res), {"workflow:daily_refresh:gate_skipped": "critical"})

    def test_a_later_successful_manual_run_clears_a_failed_scheduled_run(self):
        jobs = {1: [job("refresh", "failure", [("Safety-net commit and push", "skipped")])]}
        res = self.daily([run(2, self.SLOT + timedelta(minutes=50), event="workflow_dispatch"),
                          run(1, self.SLOT + timedelta(minutes=5), "failure")], jobs=jobs)
        self.assertEqual(res.findings, [])

    def test_stalled_run(self):
        res = self.daily([run(1, self.SLOT + timedelta(minutes=5), status="in_progress")],
                         now=self.SLOT + timedelta(minutes=100))
        self.assertEqual(self.keys(res), {"workflow:daily_refresh:stalled": "critical"})

    def test_winter_slot_follows_cyprus_time(self):
        winter_now = datetime(2026, 10, 26, 21, 0, tzinfo=timezone.utc)
        res = self.daily([run(1, datetime(2026, 10, 26, 19, 4, tzinfo=timezone.utc))], now=winter_now)
        self.assertEqual(res.facts["slot_utc"], "2026-10-26T19:00:00+00:00")
        self.assertEqual(res.findings, [])

    def test_matrix_failures_are_named_once(self):
        jobs = {1: [job(f"generate ({c})", "failure", [("Run actions/upload-artifact@v4", "failure")])
                    for c in ("A", "B", "C")]}
        r = healthy_runs()
        r["baseline-population.yml"] = [run(1, datetime(2026, 10, 20, 23, 16, tzinfo=timezone.utc), "failure")]
        root = make_root(self.tmp)
        _, files = site_for(root)
        _, res, *_ = self.evaluate(root=root, api=FakeAPI(runs=r, jobs=jobs, files=files))
        f = res["workflow:baseline_build"].findings
        self.assertEqual([x["key"] for x in f], ["workflow:baseline_build:failed"])
        self.assertIn("'Run actions/upload-artifact@v4' in 3 jobs", f[0]["summary"])
        self.assertIsNone(f[0]["dispatch"])      # baseline build is not dispatchable (Sonnet cost)


class IntervalWorkflowTests(Harness):
    def monitor(self, runs):
        r = healthy_runs()
        r["continuous-intelligence-monitor.yml"] = runs
        root = make_root(self.tmp)
        _, files = site_for(root)
        _, res, *_ = self.evaluate(root=root, api=FakeAPI(runs=r, files=files))
        return res["workflow:monitor"]

    def test_regular_runs_are_ok(self):
        self.assertEqual(self.monitor(healthy_runs()["continuous-intelligence-monitor.yml"]).findings, [])

    def test_delayed_and_missed(self):
        self.assertEqual(self.keys(self.monitor([run(1, NOW - timedelta(minutes=70))]))["workflow:monitor:delayed"],
                         "warning")
        missed = self.monitor([run(1, NOW - timedelta(minutes=200))])
        self.assertEqual(self.keys(missed)["workflow:monitor:missed"], "critical")
        self.assertEqual(self.keys(self.monitor([]))["workflow:monitor:missed"], "critical")

    def test_under_delivery_matches_the_observed_21_october_pattern(self):
        runs = [run(i, NOW - timedelta(minutes=m)) for i, m in enumerate((10, 300, 340, 790, 810))]
        runs[-1]["event"] = "workflow_dispatch"
        res = self.monitor(runs)
        self.assertEqual(self.keys(res), {"workflow:monitor:under_delivery": "warning"})
        self.assertEqual(res.facts["runs_in_window"], 5)

    def test_latest_failed_run_is_critical(self):
        res = self.monitor([run(2, NOW - timedelta(minutes=5), "failure"), run(1, NOW - timedelta(minutes=20))])
        self.assertEqual(self.keys(res), {"workflow:monitor:failed": "critical"})

    def test_pushes_and_pull_requests_do_not_count_as_production_runs(self):
        res = self.monitor([run(1, NOW - timedelta(minutes=5), event="push")])
        self.assertEqual(self.keys(res)["workflow:monitor:missed"], "critical")


class OutputTests(Harness):
    def outputs(self, mutate=None, state=None, validator=ok_validator, **kw):
        root = make_root(self.tmp, mutate=mutate, **kw)
        cfg = cfg_for(root)
        return pc.check_outputs(root, cfg, NOW, baseline=(state or {}).get("outputBaseline"), validator=validator)

    def test_current_production_data_has_only_the_known_baseline_gap(self):
        root = make_root(self.tmp)
        res = pc.check_outputs(root, cfg_for(root), NOW)          # the real validator on the real data
        self.assertEqual({f["key"] for f in res.findings if f["severity"] != "info"}, {"output:baseline_gaps"})

    def test_invalid_data_is_critical(self):
        res = self.outputs(validator=lambda root: (False, "INVALID: x"))
        self.assertEqual(self.keys(res)["output:invalid"], "critical")

    def test_staleness_thresholds(self):
        def age(h):
            return lambda d: d["meta"].__setitem__("generated", ts(NOW - timedelta(hours=h)))
        self.assertNotIn("output:stale", self.keys(self.outputs(age(20))))
        self.assertEqual(self.keys(self.outputs(age(30)))["output:stale"], "warning")
        self.assertEqual(self.keys(self.outputs(age(60)))["output:stale"], "critical")
        self.assertEqual(self.keys(self.outputs(lambda d: d["meta"].pop("generated")))["output:generated_missing"],
                         "critical")

    def test_counts_must_match_the_data(self):
        res = self.outputs(lambda d: d["meta"]["counts"].__setitem__("records", 1))
        self.assertIn("records", res.findings[0]["evidence"]["mismatch"])
        res = self.outputs(lambda d: d["meta"]["currentIntelligence"].__setitem__("events", 0))
        self.assertEqual(self.keys(res)["output:counts"], "critical")

    def test_implausible_shrink_against_the_last_good_run_and_the_minimum(self):
        def drop(d):
            d["records"] = d["records"][:100]
            d["meta"]["counts"]["records"] = 100
        res = self.outputs(drop, state={"outputBaseline": {"records": 381}})
        self.assertEqual(self.keys(res)["output:shrink"], "critical")

        def few(d):
            d["countries"] = d["countries"][:10]
            d["meta"]["counts"]["countries"] = 10
        self.assertIn("countries", next(f for f in self.outputs(few).findings if f["key"] == "output:shrink")["evidence"])

    def test_oilgas_summary_must_not_be_empty(self):
        self.assertEqual(self.keys(self.outputs(lambda d: d.__setitem__("oilGasSummary", {})))["output:oilgas_missing"],
                         "critical")

    def test_stale_discovery_evidence(self):
        self.assertEqual(self.keys(self.outputs(signals_age_h=40))["output:discovery_stale"], "warning")

    def test_monitor_spend_outcomes(self):
        def mon(triage, **st):
            return dict({"lastOutcome": {"triage": triage, "reason": "x"}}, **st)
        self.assertEqual(self.keys(self.outputs(monitor=mon("NOT_RUN_NOT_CONFIGURED")))["output:monitor_not_configured"],
                         "critical")
        self.assertEqual(self.keys(self.outputs(monitor=mon("NOT_RUN_DRY_RUN", consecutiveUntriaged=2)))[
            "output:monitor_dry_run"], "info")
        self.assertEqual(self.keys(self.outputs(monitor=mon("NOT_RUN_BUDGET_EXHAUSTED")))["output:monitor_budget"],
                         "warning")
        self.assertNotIn("output:monitor_failing", self.keys(self.outputs(monitor=mon("FAILED", consecutiveFailed=1))))
        self.assertEqual(self.keys(self.outputs(monitor=mon("FAILED", consecutiveFailed=2)))["output:monitor_failing"],
                         "critical")
        old = {"open": [{"id": "r", "usd": 0.1, "at": ts(NOW - timedelta(hours=2))}]}
        self.assertIn("output:monitor_abandoned_reservations",
                      self.keys(self.outputs(monitor=mon("COMPLETED", spend=old))))


class ReleaseTests(Harness):
    def release(self, api_kw=None, site=None, now=NOW, served=None):
        root = make_root(self.tmp)
        default_site, files = site_for(root)
        if served:
            default_site.files.update(served)
        api = FakeAPI(runs=healthy_runs(), files=files, **(api_kw or {}))
        _, res, *_ = self.evaluate(root=root, api=api, site=site or default_site, now=now)
        return res["release"]

    def test_serving_the_release_is_ok(self):
        res = self.release()
        self.assertEqual((res.status, res.facts["files_match_release"]), ("OK", True))

    def test_pages_build_errored(self):
        res = self.release({"pages": {"status": "errored", "commit": "b" * 40, "updated_at": ts(NOW),
                                      "error": {"message": "Page build failed."}}})
        self.assertEqual(self.keys(res)["release:build_failed"], "critical")

    def test_main_not_deployed_after_grace_but_fine_inside_it(self):
        late = self.release({"head": ("c" * 40, NOW - timedelta(minutes=40))})
        self.assertEqual(self.keys(late)["release:not_deployed"], "critical")
        fresh = self.release({"head": ("c" * 40, NOW - timedelta(minutes=5))})
        self.assertNotIn("release:not_deployed", self.keys(fresh))

    def test_public_site_serving_other_content(self):
        stale = self.release(served={"data.json": b'{"meta": {"generated": "old"}}'})
        self.assertEqual(self.keys(stale)["release:stale_content"], "critical")
        propagating = self.release({"pages": {"status": "built", "commit": "b" * 40,
                                              "updated_at": ts(NOW - timedelta(minutes=3))}},
                                   served={"data.json": b'{"meta": {}}'})
        self.assertEqual(self.keys(propagating)["release:stale_content"], "info")

    def test_unreachable_site_and_missing_legal_footer(self):
        down = self.release(site=Site({}, status=503))
        self.assertEqual(self.keys(down), {"release:unreachable": "critical"})
        no_footer = self.release(served={"index.html": b"<html>no footer</html>"})
        self.assertEqual(self.keys(no_footer)["release:legal_text"], "critical")

    def test_cache_busting_and_no_cache_header_are_used(self):
        site = Site({"data.json": b"{}", "index.html": LEGAL.encode()})
        calls = []
        site_wrap = lambda url, **kw: calls.append(kw) or site(url, **kw)  # noqa: E731
        self.release(site=site_wrap)
        self.assertTrue(all(c["headers"]["Cache-Control"] == "no-cache" for c in calls))
        self.assertTrue(all("?pc=" in u for u in site.calls))

    def test_api_unavailable_is_unverified_not_healthy(self):
        res = self.release({"down": {"pages"}})
        self.assertEqual((res.status, res.findings), ("UNVERIFIED", []))


class LedgerTests(Harness):
    def failing_api(self, root, conclusion="failure"):
        r = healthy_runs()
        r["continuous-intelligence-monitor.yml"] = [run(2, NOW - timedelta(minutes=5), conclusion),
                                                    run(1, NOW - timedelta(minutes=20))]
        return FakeAPI(runs=r, files=site_for(root)[1])

    def test_one_task_per_exception_across_runs_then_resolution_and_reopening(self):
        root = make_root(self.tmp)
        _, _, s1, d1, _ = self.evaluate(root=root, api=self.failing_api(root))
        key = "workflow:monitor:failed"
        t1 = next(t for t in s1["tasks"] if t["dedupe_key"] == key)
        self.assertIn(t1["id"], d1["opened"])
        _, _, s2, d2, _ = self.evaluate(root=root, api=self.failing_api(root), state=s1, now=NOW + timedelta(hours=1))
        t2 = [t for t in s2["tasks"] if t["dedupe_key"] == key]
        self.assertEqual((len(t2), t2[0]["id"], t2[0]["occurrences"]), (1, t1["id"], 2))
        self.assertIn(t1["id"], d2["ongoing"])
        _, _, s3, d3, _ = self.evaluate(root=root, state=s2, now=NOW + timedelta(hours=2))
        done = next(t for t in s3["tasks"] if t["id"] == t1["id"])
        self.assertEqual(done["state"], "completed")
        self.assertIn(t1["id"], d3["resolved"])
        _, _, s4, d4, _ = self.evaluate(root=root, api=self.failing_api(root), state=s3, now=NOW + timedelta(hours=3))
        reopened = [t for t in s4["tasks"] if t["dedupe_key"] == key and t["state"] == "blocked"]
        self.assertEqual(len(reopened), 1)
        self.assertNotEqual(reopened[0]["id"], t1["id"])      # history kept, not overwritten

    def test_unverified_check_keeps_its_open_tasks(self):
        root = make_root(self.tmp)
        _, _, s1, _, _ = self.evaluate(root=root, api=self.failing_api(root))
        api = self.failing_api(root)
        api.down = {"continuous-intelligence-monitor.yml"}
        _, res, s2, d2, _ = self.evaluate(root=root, api=api, state=s1, now=NOW + timedelta(hours=1))
        self.assertEqual(res["workflow:monitor"].status, "UNVERIFIED")
        self.assertEqual(d2["resolved"], [])
        self.assertTrue(any(t["dedupe_key"] == "workflow:monitor:failed" and t["state"] == "blocked"
                            for t in s2["tasks"]))
        self.assertEqual(pc.verdict(list(res.values()), s2["tasks"]), "FAILING")

    def test_escalation_from_warning_to_critical(self):
        root = make_root(self.tmp, mutate=lambda d: d["meta"].__setitem__("generated", ts(NOW - timedelta(hours=30))))
        _, _, s1, _, _ = self.evaluate(root=root)
        _, _, s2, d2, _ = self.evaluate(root=root, state=s1, now=NOW + timedelta(hours=22))
        t = next(t for t in s2["tasks"] if t["dedupe_key"] == "output:stale")
        self.assertEqual(t["severity"], "critical")
        self.assertIn(t["id"], d2["escalated"])

    def test_resolved_tasks_are_purged_after_retention(self):
        old = {"id": "pc-old", "dedupe_key": "output:x", "state": "completed", "severity": "warning",
               "resolved_utc": ts(NOW - timedelta(days=40))}
        _, _, s, _, _ = self.evaluate(state={"tasks": [old]})
        self.assertNotIn("pc-old", [t["id"] for t in s["tasks"]])

    def test_controller_notices_its_own_gap(self):
        _, res, *_ = self.evaluate(state={"lastRun": ts(NOW - timedelta(hours=5))})
        self.assertEqual(self.keys(res["controller"]), {"controller:gap": "warning"})

    def test_task_record_carries_the_director_fields_and_no_secrets(self):
        root = make_root(self.tmp)
        _, _, s, _, _ = self.evaluate(root=root, api=self.failing_api(root))
        t = s["tasks"][0]
        for k in ("id", "dedupe_key", "task_type", "worker", "business_application", "substream", "state",
                  "requires_human", "blocking_reason", "evidence", "first_seen_utc", "last_seen_utc"):
            self.assertIn(k, t)
        self.assertEqual(t["substream"], "03AA-04 Intelligence Automation Agents")


class DispatchTests(Harness):
    def setUp(self):
        super().setUp()
        self.root = make_root(self.tmp)
        r = healthy_runs()
        r["continuous-intelligence-monitor.yml"] = [run(1, NOW - timedelta(minutes=300))]
        self.api = FakeAPI(runs=r, files=site_for(self.root)[1])

    def go(self, state=None, now=NOW, **env):
        env = dict({"TI_PC_STATE_BACKEND": "file"}, **env)
        return self.evaluate(root=self.root, api=self.api, state=state, now=now, **env)

    def test_no_dispatch_by_default_even_in_active_mode(self):
        self.assertEqual(self.go(TI_PC_MODE="active")[4], [])

    def test_observe_never_dispatches(self):
        self.assertEqual(self.go(TI_PC_RECOVERY_WORKFLOWS="continuous-intelligence-monitor.yml")[4], [])

    def test_bounded_dispatch_per_workflow_per_day(self):
        env = {"TI_PC_MODE": "active", "TI_PC_RECOVERY_WORKFLOWS": "continuous-intelligence-monitor.yml"}
        _, _, s1, _, p1 = self.go(**env)
        self.assertEqual([(p["workflow"], p["ref"]) for p in p1], [("continuous-intelligence-monitor.yml", "main")])
        _, _, s2, _, p2 = self.go(state=s1, now=NOW + timedelta(hours=1), **env)
        self.assertEqual(p2, [])                                    # cap reached for this UTC day
        _, _, _, _, p3 = self.go(state=s2, now=NOW + timedelta(days=1), **env)
        self.assertEqual(len(p3), 1)                                # next day: allowed once more

    def test_no_dispatch_while_a_run_is_in_progress(self):
        self.api.runs["continuous-intelligence-monitor.yml"] = [
            run(2, NOW - timedelta(minutes=2), status="in_progress"), run(1, NOW - timedelta(minutes=300))]
        env = {"TI_PC_MODE": "active", "TI_PC_RECOVERY_WORKFLOWS": "continuous-intelligence-monitor.yml"}
        self.assertEqual(self.go(**env)[4], [])


class RecoveryJobTests(Harness):
    ALLOW = {"TI_PC_RECOVERY_WORKFLOWS": "continuous-intelligence-monitor.yml"}

    def go(self, dispatches, **env):
        calls, lines = [], []
        runner = lambda cmd: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0)  # noqa: E731
        code = pc.run_dispatch(env=dict(self.ALLOW, TI_PC_DISPATCHES=dispatches, **env), root=make_root(self.tmp),
                               runner=runner, out=lines.append)
        return code, calls, lines

    def test_round_trip_from_the_check_jobs_output(self):
        root = make_root(self.tmp)
        env = {"TI_PC_MODE": "active", "TI_PC_STATE_BACKEND": "file", "TI_PC_STATE_DIR": str(Path(self.tmp) / "s"),
               "GITHUB_OUTPUT": str(Path(self.tmp) / "out"), **self.ALLOW}
        r = healthy_runs()
        r["continuous-intelligence-monitor.yml"] = [run(1, NOW - timedelta(minutes=300))]
        pc.run(env=env, root=root, api=FakeAPI(runs=r, files=site_for(root)[1]), http=site_for(root)[0],
               validator=ok_validator, now=NOW, out=lambda *_: None)
        emitted = next(l for l in (Path(self.tmp) / "out").read_text().splitlines() if l.startswith("dispatches="))
        code, calls, _ = self.go(emitted.split("=", 1)[1])
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(calls, [["gh", "workflow", "run", "continuous-intelligence-monitor.yml", "--repo",
                                  "smitblade-dot/Travel_Intelligence", "--ref", "main"]])

    def test_empty_plan_dispatches_nothing(self):
        self.assertEqual(self.go("[]")[:2], (EXIT_OK, []))
        self.assertEqual(self.go("")[:2], (EXIT_OK, []))

    def test_refuses_anything_not_allow_listed_or_off_main(self):
        for bad in ('[{"workflow": "baseline-population.yml", "ref": "main"}]',
                    '[{"workflow": "daily-data-refresh.yml", "ref": "main"}]',
                    '[{"workflow": "continuous-intelligence-monitor.yml", "ref": "ti-ops-state"}]',
                    '[{"workflow": "x.yml; rm -rf /", "ref": "main"}]', '{"workflow": 1}', 'not json', '[null]'):
            with self.subTest(bad=bad):
                code, calls, lines = self.go(bad)
                self.assertEqual((code, calls), (EXIT_CONFIG, []))
                self.assertTrue(lines[0].startswith("::error title=Production recovery refused"))

    def test_a_failed_dispatch_turns_the_job_red(self):
        lines = []
        code = pc.run_dispatch(env=dict(self.ALLOW, TI_PC_DISPATCHES='[{"workflow": '
                                        '"continuous-intelligence-monitor.yml", "ref": "main"}]'),
                               root=make_root(self.tmp), runner=lambda cmd: subprocess.CompletedProcess(cmd, 1),
                               out=lines.append)
        self.assertEqual(code, pc.EXIT_STATE)


class EntryPointTests(Harness):
    def env(self, **kw):
        out = Path(self.tmp) / "out"
        summary = Path(self.tmp) / "summary"
        return dict({"TI_PC_MODE": "active", "TI_PC_STATE_BACKEND": "file", "TI_PC_STATE_DIR": str(Path(self.tmp) / "st"),
                     "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary)}, **kw), out, summary

    def failing(self, root):
        r = healthy_runs()
        r["continuous-intelligence-monitor.yml"] = [run(1, NOW - timedelta(minutes=300))]
        return FakeAPI(runs=r, files=site_for(root)[1])

    def test_active_run_persists_state_report_and_goes_red_only_once(self):
        root = make_root(self.tmp)
        env, out, summary = self.env(TI_PC_RECOVERY_WORKFLOWS="continuous-intelligence-monitor.yml")
        site = site_for(root)[0]
        lines = []
        code = pc.run(env=env, root=root, api=self.failing(root), http=site, validator=ok_validator, now=NOW,
                      out=lines.append)
        self.assertEqual(code, EXIT_NEW_CRITICAL)
        self.assertTrue(any(l.startswith("::error title=Production critical") for l in lines))
        state = json.loads((Path(env["TI_PC_STATE_DIR"]) / STATE_PATHS["state"]).read_text())
        self.assertTrue((Path(env["TI_PC_STATE_DIR"]) / STATE_PATHS["report"]).read_text().startswith("# Production report"))
        self.assertEqual(state["dispatches"][0]["workflow"], "continuous-intelligence-monitor.yml")
        self.assertIn('dispatches=[{"workflow": "continuous-intelligence-monitor.yml", "ref": "main"}]',
                      out.read_text())
        self.assertIn("## Verdict: FAILING", summary.read_text())
        lines = []
        code = pc.run(env=env, root=root, api=self.failing(root), http=site, validator=ok_validator,
                      now=NOW + timedelta(hours=1), out=lines.append)
        self.assertEqual(code, EXIT_OK)                       # standing fault: reported, not re-alerted
        self.assertFalse(any(l.startswith("::error") for l in lines))

    def test_observe_writes_nothing_and_stays_green(self):
        root = make_root(self.tmp)
        env, out, _ = self.env(TI_PC_MODE="observe")
        code = pc.run(env=env, root=root, api=self.failing(root), http=site_for(root)[0], validator=ok_validator,
                      now=NOW, out=lambda *_: None)
        self.assertEqual(code, EXIT_OK)
        self.assertFalse(Path(env["TI_PC_STATE_DIR"]).exists())
        self.assertIn("dispatches=[]", out.read_text())

    def test_cli_command_reaches_the_controller(self):
        p = subprocess.run(["python3", str(ROOT / "scripts/ops_director.py"), "production-check"], cwd=self.tmp,
                           env={"PATH": "/usr/bin:/bin", "TI_PC_MODE": "off"}, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("03AA-04_Production_Controller_TI: OFF", p.stdout)


class StateBranchTests(unittest.TestCase):
    """Real git against a local bare remote: the orphan state branch and compare-and-swap writes."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        g = lambda *a, cwd=None: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)  # noqa: E731
        g("init", "--bare", "-q", str(self.tmp / "remote.git"))
        g("init", "-q", str(self.tmp / "work"))
        w = self.tmp / "work"
        (w / "data.json").write_text("{}")
        g("-c", "user.name=t", "-c", "user.email=t@t", "add", ".", cwd=w)
        g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "main", cwd=w)
        g("remote", "add", "origin", str(self.tmp / "remote.git"), cwd=w)
        g("push", "-q", "origin", "HEAD:refs/heads/main", cwd=w)
        self.work = w

    def test_creates_the_state_branch_and_never_touches_main(self):
        main_before = subprocess.run(["git", "ls-remote", str(self.tmp / "remote.git"), "main"],
                                     capture_output=True, text=True).stdout
        b = BranchStateBackend(self.work, STATE_PATHS, branch="ti-ops-state", mirror_worktree=False)
        self.assertEqual(b.snapshot().state(), {})
        b.transact(lambda snap: ({"state": '{"n": 1}\n', "report": "r\n"}, None), message="t")
        b2 = BranchStateBackend(self.work, STATE_PATHS, branch="ti-ops-state", mirror_worktree=False)
        b2.transact(lambda snap: ({"state": json.dumps({"n": snap.state()["n"] + 1})}, None), message="t")
        self.assertEqual(b.snapshot().state(), {"n": 2})
        self.assertEqual(subprocess.run(["git", "ls-remote", str(self.tmp / "remote.git"), "main"],
                                        capture_output=True, text=True).stdout, main_before)
        files = subprocess.run(["git", "--git-dir", str(self.tmp / "remote.git"), "ls-tree", "--name-only",
                                "ti-ops-state"], capture_output=True, text=True).stdout.split()
        self.assertEqual(sorted(files), sorted(STATE_PATHS.values()))
        self.assertFalse((self.work / STATE_PATHS["state"]).exists())     # nothing mirrored into main's checkout


class MasterAgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_registry_implements_production_verification_and_keeps_recovery_planned(self):
        reg = default_registry()
        self.assertTrue(reg.get("production_verification").implemented)
        self.assertFalse(reg.get("production_recovery").implemented)
        self.assertEqual(reg.for_type("production_diagnostic").name, "production_recovery")

    def test_queue_worker_creates_blocked_follow_ups_once_and_cancels_on_resolution(self):
        root = make_root(self.tmp)
        ctl, clock = make_controller(self.tmp, clock=Clock(ts(NOW)))
        ctl.cfg.repo_root = str(root)
        body = (root / "data.json").read_bytes()
        site = Site({"data.json": body, "index.html": LEGAL.encode()})
        runs = healthy_runs()
        runs["continuous-intelligence-monitor.yml"] = [run(1, NOW - timedelta(minutes=300))]
        api_data = {"runs": runs}

        def transport(url, **kw):
            if "api.github.com" not in url:
                return site(url, **kw)
            path = url.split("api.github.com")[1]
            if "/pages/builds/latest" in path:
                return HttpReply(200, json.dumps({"status": "built", "commit": "b" * 40,
                                                  "updated_at": ts(NOW - timedelta(hours=1))}).encode())
            if "/commits/" in path:
                return HttpReply(200, json.dumps({"sha": "b" * 40, "commit": {"committer": {
                    "date": ts(NOW - timedelta(hours=1))}}}).encode())
            if "/contents/" in path:
                return HttpReply(200, body if "data.json" in path else LEGAL.encode())
            if "/jobs" in path:
                return HttpReply(200, json.dumps({"jobs": REFRESH_OK}).encode())
            wf = path.split("/workflows/")[1].split("/")[0]
            return HttpReply(200, json.dumps({"workflow_runs": api_data["runs"].get(wf, [])}).encode())

        worker = next(w for w in ctl.registry.all() if w.name == "production_verification")
        worker.transport = staticmethod(transport)
        import os
        os.environ["TI_PC_STATE_DIR"] = str(Path(self.tmp) / "pcstate")
        self.addCleanup(os.environ.pop, "TI_PC_STATE_DIR", None)
        ctl.enqueue(task_type="production_verification", inputs={"slot": 1}, dedupe_subject="1")
        ctl.tick()
        done = [t for t in ctl.store.list_tasks() if t["task_type"] == "production_verification"][0]
        self.assertEqual(done["state"], "completed", done["error"])
        follow = [t for t in ctl.store.list_tasks(("blocked",)) if t["worker"] == "production_recovery"]
        keys = {json.loads(t["inputs"])["dedupe_key"] for t in follow}
        self.assertIn("workflow:monitor:missed", keys)
        self.assertTrue(all(t["requires_human"] for t in follow))
        clock.advance(hours=1)
        ctl.enqueue(task_type="production_verification", inputs={"slot": 2}, dedupe_subject="2")
        ctl.tick()
        again = [t for t in ctl.store.list_tasks(("blocked",)) if t["worker"] == "production_recovery"]
        self.assertEqual(len(again), len(follow))               # deduplicated, not duplicated per run
        api_data["runs"] = healthy_runs(NOW + timedelta(hours=2))
        clock.advance(hours=1)
        ctl.enqueue(task_type="production_verification", inputs={"slot": 3}, dedupe_subject="3")
        ctl.tick()
        cancelled = [t for t in ctl.store.list_tasks(("cancelled",))
                     if json.loads(t["inputs"]).get("dedupe_key") == "workflow:monitor:missed"]
        self.assertEqual(len(cancelled), 1)

    def test_queue_worker_refuses_writes_and_unlisted_hosts(self):
        from ti_ops_director.workers import CapabilityError, WorkContext
        w = ProductionVerificationWorker()
        ctl, _ = make_controller(self.tmp)
        ctx = WorkContext(ctl.cfg, ctl.store, None, w, "t", "o",
                          allowed_hosts=w.hosts_for({}, ctl.cfg))
        http = w._guarded(ctx)
        with self.assertRaises(CapabilityError):
            http("https://evil.example/x")
        with self.assertRaises(CapabilityError):
            http("https://api.github.com/repos/x/actions/workflows/y/dispatches", method="POST")

    def test_run_due_schedules_production_checks_only_when_configured(self):
        ctl, _ = make_controller(self.tmp)
        self.assertNotIn("production_verification", ctl.run_due(mode="fixture", fixtures={}))
        ctl.cfg.production_check_interval_minutes = 60
        with redirect_stdout(io.StringIO()):
            done = ctl.run_due(mode="live", snapshot_dir=make_root(self.tmp))
        self.assertTrue(done["production_verification"]["created"])

    def test_director_report_has_a_production_section(self):
        from ti_ops_director.report import build_report, render_markdown
        ctl, _ = make_controller(self.tmp)
        ctl.cfg.repo_root = self.tmp
        text = render_markdown(build_report(ctl))
        self.assertIn("## Production controller (03AA-04_Production_Controller_TI)", text)
        self.assertIn("TI_PC_STATE_BRANCH", text)


class WorkflowContractTests(unittest.TestCase):
    """Text checks (PyYAML is not installed in this test job)."""
    TEXT = (ROOT / ".github/workflows/production-controller.yml").read_text()

    def test_inert_until_mode_is_set_and_least_privilege(self):
        t = self.TEXT
        self.assertIn("if: github.event_name == 'workflow_dispatch' || vars.TI_PC_MODE == 'observe' || "
                      "vars.TI_PC_MODE == 'active'", t)
        self.assertIn("permissions: {}", t)
        check, recover = t.split("\n  recover:")
        self.assertIn("actions: read", check)
        self.assertNotIn("actions: write", check)
        self.assertIn("actions: write", recover)
        self.assertNotIn("contents: write", recover)
        self.assertIn("python3 scripts/ops_director.py production-dispatch", recover)
        self.assertIn("python3 scripts/ops_director.py production-check", check)
        self.assertNotIn("git push", t)
        self.assertIn("cancel-in-progress: false", t)

    def test_watched_workflow_names_exist(self):
        names = {(ROOT / ".github/workflows" / f).read_text().split("\n", 1)[0].removeprefix("name: ").strip()
                 for f in ("daily-data-refresh.yml", "baseline-population.yml")}
        for n in names:
            self.assertIn(f'"{n}"', self.TEXT)

    def test_monitored_workflow_files_exist(self):
        cfg = json.loads((ROOT / "config/production_controller.json").read_text())
        for w in cfg["workflows"]:
            self.assertTrue((ROOT / ".github/workflows" / w["file"]).exists(), w["file"])
        refresh = (ROOT / ".github/workflows/daily-data-refresh.yml").read_text()
        self.assertIn("- name: " + cfg["workflows"][0]["output_step"], refresh)
        self.assertIn("  " + cfg["workflows"][0]["required_job"] + ":", refresh)


if __name__ == "__main__":
    unittest.main()
