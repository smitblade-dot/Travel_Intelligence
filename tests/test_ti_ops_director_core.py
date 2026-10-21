"""Controller, queue, recovery, locking, scheduling and report tests (offline, FIXTURE data only)."""
import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ops_support import *   # noqa: F401,F403
from ops_support import ROOT
from ti_ops_director.config import load_config
from ti_ops_director.feed_scan import scan_feed_health
from ti_ops_director.publication import PublicationDisabled, PublicationGateway
from ti_ops_director.report import build_report, render_markdown
from ti_ops_director.scheduling import last_daily_slot, next_daily_run
from ti_ops_director.store import Store


def reg_with(*workers):
    reg = default_registry()
    for w in workers:
        reg.register(w)
    return reg


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_queue_persists_across_restart(self):
        ctl, clock = make_controller(self.tmp, registry=reg_with(RecordingWorker()))
        tid, created = ctl.enqueue(task_type="recording", inputs={"a": 1}, dedupe_subject="x")
        self.assertTrue(created)
        ctl2, _ = make_controller(self.tmp, cfg=ctl.cfg, clock=clock, registry=reg_with(RecordingWorker()))
        self.assertEqual(ctl2.store.get(tid)["state"], "pending")
        self.assertEqual(ctl2.store.get(tid)["business_application"], "Travel Intelligence")
        self.assertEqual(ctl2.store.get(tid)["substream"], "03AA-04 Intelligence Automation Agents")

    def test_duplicate_inputs_do_not_create_duplicate_tasks(self):
        ctl, clock = make_controller(self.tmp, registry=reg_with(RecordingWorker()))
        a = ctl.enqueue(task_type="recording", inputs={"a": 1}, dedupe_subject="x")
        b = ctl.enqueue(task_type="recording", inputs={"a": 1}, dedupe_subject="x")
        self.assertEqual(a[0], b[0])
        self.assertFalse(b[1])
        ctl.tick()                                            # now completed: still no duplicate
        c = ctl.enqueue(task_type="recording", inputs={"a": 1}, dedupe_subject="x")
        self.assertFalse(c[1])
        d = ctl.enqueue(task_type="recording", inputs={"a": 2}, dedupe_subject="x")   # changed input
        self.assertTrue(d[1])

    def test_crash_recovery_reruns_once_without_duplicate_completion(self):
        log = []
        ctl, clock = make_controller(self.tmp, registry=reg_with(RecordingWorker(log)))
        tid, _ = ctl.enqueue(task_type="recording", inputs={}, dedupe_subject="x")
        self.assertTrue(ctl.store.claim(tid, "dead-process", 300))        # process dies mid-task
        self.assertEqual(ctl.tick()["started"], [])                        # lease still valid: untouched
        clock.advance(seconds=301)
        s = ctl.tick()
        self.assertEqual(s["recovered"], [tid])
        self.assertEqual(s["started"], [tid])
        self.assertEqual(log, [tid])
        t = ctl.store.get(tid)
        self.assertEqual((t["state"], t["attempts"]), ("completed", 2))
        done = [e for e in ctl.store.query("SELECT kind FROM events WHERE task_id=?", (tid,)) if e["kind"] == "finished_completed"]
        self.assertEqual(len(done), 1)
        clock.advance(seconds=1000)
        self.assertEqual(ctl.tick()["started"], [])                        # nothing re-run

    def test_lease_expiry_at_max_attempts_fails(self):
        ctl, clock = make_controller(self.tmp, cfg=make_cfg(self.tmp, max_attempts=1), registry=reg_with(RecordingWorker()))
        tid, _ = ctl.enqueue(task_type="recording", inputs={}, dedupe_subject="x")
        ctl.store.claim(tid, "dead", 10)
        clock.advance(seconds=11)
        ctl.store.recover_expired()
        self.assertEqual(ctl.store.get(tid)["state"], "failed")

    def test_concurrent_claim_has_one_winner(self):
        ctl, _ = make_controller(self.tmp, registry=reg_with(RecordingWorker()))
        tid, _ = ctl.enqueue(task_type="recording", inputs={}, dedupe_subject="x")
        wins = []
        ths = [threading.Thread(target=lambda i=i: wins.append(ctl.store.claim(tid, f"o{i}", 60))) for i in range(8)]
        [t.start() for t in ths]; [t.join() for t in ths]
        self.assertEqual(sum(wins), 1)
        self.assertEqual(ctl.store.get(tid)["attempts"], 1)

    def test_single_active_scheduler(self):
        ctl, _ = make_controller(self.tmp, registry=reg_with(RecordingWorker()))
        ctl.store.acquire_lock("ops_director_scheduler", "someone-else", 300)
        self.assertEqual(ctl.tick()["skipped"], "another_scheduler_active")

    def test_bounded_concurrency_and_priority(self):
        log = []
        cfg = make_cfg(self.tmp, max_concurrency=2)
        ctl, _ = make_controller(self.tmp, cfg=cfg, registry=reg_with(RecordingWorker(log)))
        ids = [ctl.enqueue(task_type="recording", inputs={"n": n}, dedupe_subject=str(n), priority=p)[0]
               for n, p in enumerate([10, 90, 50, 70])]
        s = ctl.tick()
        self.assertEqual(len(s["started"]), 2)
        self.assertEqual(set(log), {ids[1], ids[3]})                       # the two highest priorities

    def test_dependencies(self):
        log = []
        ctl, _ = make_controller(self.tmp, registry=reg_with(RecordingWorker(log)))
        a, _ = ctl.enqueue(task_type="recording", inputs={"n": 1}, dedupe_subject="a", priority=10)
        b, _ = ctl.enqueue(task_type="recording", inputs={"n": 2}, dedupe_subject="b", priority=99, depends_on=[a])
        ctl.tick()
        self.assertEqual(log, [a])
        ctl.tick()
        self.assertEqual(log, [a, b])

    def test_failed_dependency_blocks_dependant(self):
        ctl, _ = make_controller(self.tmp, cfg=make_cfg(self.tmp, max_attempts=1),
                                 registry=reg_with(RecordingWorker(fail=True)))
        a, _ = ctl.enqueue(task_type="recording", inputs={}, dedupe_subject="a")
        ctl.tick()
        self.assertEqual(ctl.store.get(a)["state"], "failed")
        reg = ctl.registry
        b, _ = ctl.enqueue(task_type="feed_health_diagnostic", inputs={"x": 1}, dedupe_subject="b", depends_on=[a])
        ctl.tick()
        self.assertEqual(ctl.store.get(b)["blocking_reason"], f"dependency_failed:{a}")

    def test_unimplemented_worker_is_explicitly_blocked_then_unblocks(self):
        ctl, _ = make_controller(self.tmp)
        tid, _ = ctl.enqueue(task_type="verification", inputs={"claim": "x"}, dedupe_subject="x")
        ctl.tick()
        t = ctl.store.get(tid)
        self.assertEqual((t["state"], t["blocking_reason"]), ("blocked", "worker_not_implemented:verification"))
        ctl.registry._workers["verification"] = RecordingWorker()          # worker becomes available
        ctl.registry._workers["verification"].name = "verification"
        ctl.tick()
        self.assertEqual(ctl.store.get(tid)["state"], "completed")

    def test_unregistered_task_type_is_blocked(self):
        ctl, _ = make_controller(self.tmp)
        tid, _ = ctl.enqueue(task_type="nonsense", inputs={}, dedupe_subject="x")
        ctl.tick()
        self.assertEqual(ctl.store.get(tid)["state"], "blocked")

    def test_pause_and_stop(self):
        log = []
        ctl, _ = make_controller(self.tmp, registry=reg_with(RecordingWorker(log)))
        tid, _ = ctl.enqueue(task_type="recording", inputs={}, dedupe_subject="x")
        ctl.pause()
        self.assertEqual(ctl.tick()["skipped"], "paused")
        self.assertEqual(log, [])
        ctl.resume()
        ctl.stop()
        self.assertEqual(ctl.tick()["skipped"], "stopped")
        ctl.resume()
        ctl.tick()
        self.assertEqual(log, [tid])

    def test_cancel(self):
        ctl, _ = make_controller(self.tmp, registry=reg_with(RecordingWorker()))
        tid, _ = ctl.enqueue(task_type="recording", inputs={}, dedupe_subject="x")
        self.assertTrue(ctl.store.cancel(tid))
        ctl.tick()
        self.assertEqual(ctl.store.get(tid)["state"], "cancelled")

    def test_retry_bounded_then_failed(self):
        ctl, _ = make_controller(self.tmp, cfg=make_cfg(self.tmp, max_attempts=2),
                                 registry=reg_with(RecordingWorker(fail=True)))
        tid, _ = ctl.enqueue(task_type="recording", inputs={}, dedupe_subject="x")
        for _ in range(5):
            ctl.tick()
        t = ctl.store.get(tid)
        self.assertEqual((t["state"], t["attempts"]), ("failed", 2))


class ScheduleTests(unittest.TestCase):
    def test_cyprus_summer_winter_and_transitions(self):
        def utc_hour(day):
            now = datetime.fromisoformat(day + "T00:30:00+00:00")
            return next_daily_run(now, "21:00", "Europe/Nicosia")
        self.assertEqual(utc_hour("2026-07-15").hour, 18)      # EEST, UTC+3
        self.assertEqual(utc_hour("2026-01-15").hour, 19)      # EET, UTC+2
        self.assertEqual(utc_hour("2026-03-28").hour, 19)      # day before clocks go forward
        self.assertEqual(utc_hour("2026-03-29").hour, 18)      # after the change
        self.assertEqual(utc_hour("2026-10-24").hour, 18)
        self.assertEqual(utc_hour("2026-10-25").hour, 19)      # clocks went back

    def test_gap_time_moves_forward(self):
        from ti_ops_director.scheduling import local_to_utc
        d = datetime(2026, 3, 29).date()
        self.assertEqual(local_to_utc(d, "03:30", "Europe/Nicosia").hour, 1)   # 03:30 does not exist; 04:30 EEST = 01:30Z

    def test_last_slot_and_next(self):
        now = datetime.fromisoformat("2026-10-21T10:00:00+00:00")
        self.assertEqual(last_daily_slot(now, "21:00", "Europe/Nicosia").isoformat(), "2026-10-20T18:00:00+00:00")
        self.assertEqual(next_daily_run(now, "21:00", "Europe/Nicosia").isoformat(), "2026-10-21T18:00:00+00:00")

    def test_run_due_coalesces_missed_and_prevents_overlap(self):
        tmp = tempfile.mkdtemp()
        root = make_root(tmp, snapshot())
        ctl, clock = make_controller(tmp)
        r1 = ctl.run_due(snapshot_dir=root, mode="fixture", fixtures={}, env_override={})
        self.assertIn("feed_health_scan", r1)
        r2 = ctl.run_due(snapshot_dir=root, mode="fixture", fixtures={}, env_override={})
        self.assertEqual(r2, {})                                # same slot: nothing re-run
        clock.advance(hours=3)                                  # 12 slots missed: exactly one run
        r3 = ctl.run_due(snapshot_dir=root, mode="fixture", fixtures={}, env_override={})
        self.assertIn("feed_health_scan", r3)
        clock.advance(minutes=20)
        ctl.store.acquire_lock("responsibility:feed_health_scan", "other", 600)
        self.assertEqual(ctl.run_due(snapshot_dir=root, mode="fixture", fixtures={}, env_override={})["feed_health_scan"],
                         "overlap_skipped")
        ctl.store.release_lock("responsibility:feed_health_scan", "other")
        clock.advance(hours=9)                                  # 19:20Z crosses the 18:00Z (21:00 EEST) slot
        ctl.run_due(snapshot_dir=root, mode="fixture", fixtures={}, env_override={})
        daily = [t for t in ctl.store.list_tasks() if t["task_type"] == "daily_consolidation"]
        self.assertEqual(len(daily), 2)                         # one per 21:00 slot crossed
        ctl.tick()
        self.assertTrue(all(ctl.store.get(t["id"])["state"] == "blocked" for t in daily))


class ConfigAndPublicationTests(unittest.TestCase):
    def test_safe_defaults(self):
        cfg = load_config(None, env={})
        self.assertTrue(cfg.dry_run)
        self.assertFalse(cfg.paid_allowance_configured)
        self.assertIsNone(cfg.opus_approval)
        self.assertFalse(cfg.publication_enabled)

    def test_unknown_config_key_rejected(self):
        p = Path(tempfile.mkdtemp()) / "c.json"
        p.write_text(json.dumps({"enable_everything": True}))
        with self.assertRaises(ValueError):
            load_config(str(p), env={})

    def test_example_config_loads_and_has_no_allowance(self):
        cfg = load_config(str(ROOT / "config/ops_director.example.json"), env={})
        self.assertTrue(cfg.dry_run)
        self.assertFalse(cfg.paid_allowance_configured)

    def test_publication_always_refused(self):
        cfg = make_cfg(tempfile.mkdtemp(), publication_enabled=True)
        with self.assertRaises(PublicationDisabled):
            PublicationGateway(cfg).publish({"id": "x"})


class ReportTests(unittest.TestCase):
    def test_report_labels_fixture_and_does_not_claim_readiness(self):
        tmp = tempfile.mkdtemp()
        root = make_root(tmp, snapshot())
        ctl, _ = make_controller(tmp)
        fx = json.loads((ROOT / "tests/fixtures/ops_director/fixture_responses.json").read_text())
        ctl.run_due(snapshot_dir=root, mode="fixture", fixtures=fx, env_override={})
        for _ in range(3):
            ctl.tick()
        rep = build_report(ctl)
        md = render_markdown(rep)
        self.assertIn("FIXTURE", md)
        self.assertIn("0 diagnostic task(s) with live evidence", md)
        self.assertIn("no evidence: nothing deployed", md)
        self.assertIn("This is not a production-ready claim", md)
        self.assertNotIn("production ready", md.lower().replace("production-ready claim", ""))
        by = {d["source"]: d for d in rep["feed_health"]}
        self.assertEqual(by["gdelt-events"]["classification"], "provider_outage")
        self.assertEqual(by["reliefweb-disasters"]["classification"], "approval_required")
        self.assertTrue(rep["needs_marc"])
        self.assertTrue(any(w["worker"] == "verification" for w in rep["unimplemented_workers"]))

    def test_eur_conversion_is_flagged_without_rate_and_dated_with_rate(self):
        tmp = tempfile.mkdtemp()
        ctl, _ = make_controller(tmp)
        self.assertIsNone(build_report(ctl)["spend_month_eur"]["eur"])
        self.assertIn("No verified", render_markdown(build_report(ctl)))
        with ctl.store.tx() as db:
            db.execute("INSERT INTO fx_rates VALUES('2026-10-20','USD','EUR',0.9,'FIXTURE rate for test')")
        self.assertEqual(build_report(ctl)["spend_month_eur"]["rate_date"], "2026-10-20")

    def test_scan_is_read_only(self):
        tmp = tempfile.mkdtemp()
        root = make_root(tmp, snapshot())
        before = (root / "data/phase1_signals.json").read_bytes()
        ctl, _ = make_controller(tmp)
        scan_feed_health(ctl, mode="fixture", root=root, fixtures={}, env_override={})
        self.assertEqual(before, (root / "data/phase1_signals.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
