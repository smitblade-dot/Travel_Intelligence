"""Schedule contract and actual gate-shell regression tests (requires PyYAML)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from ti_ops_director import refresh_slot  # noqa: E402

WORKFLOW = yaml.load(
    (ROOT / '.github/workflows/daily-data-refresh.yml').read_text(),
    Loader=yaml.BaseLoader,
)
CONTROLLER = json.loads((ROOT / 'config/production_controller.json').read_text())
SUMMER, WINTER = '0 18 * * *', '0 19 * * *'


def utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


class RefreshScheduleTests(unittest.TestCase):
    def test_schedule_contract(self):
        # Plain UTC crons: GitHub's `timezone:` key fired at 21:00 UTC on 20 and 21 Oct 2026.
        self.assertEqual(WORKFLOW['on']['schedule'], [{'cron': SUMMER}, {'cron': WINTER}])
        self.assertIn('workflow_dispatch', WORKFLOW['on'])
        self.assertIn('github.event.schedule', WORKFLOW['run-name'])
        self.assertEqual(WORKFLOW['jobs']['refresh']['needs'], 'schedule_gate')
        self.assertEqual(WORKFLOW['jobs']['refresh']['if'],
                         "needs.schedule_gate.outputs.should_run == 'true'")

    def test_gate_workflow_and_controller_agree(self):
        spec = next(w for w in CONTROLLER['workflows'] if w['id'] == 'daily_refresh')
        self.assertEqual(spec['file'], 'daily-data-refresh.yml')
        self.assertEqual(spec['schedule']['local_time'], refresh_slot.LOCAL_TIME)
        self.assertEqual(spec['schedule']['timezone'], refresh_slot.TIMEZONE)
        self.assertEqual(tuple(spec['schedule']['utc_crons']), refresh_slot.CRONS)
        self.assertEqual(tuple(e['cron'] for e in WORKFLOW['on']['schedule']), refresh_slot.CRONS)

    def test_exactly_one_cron_is_2100_cyprus_every_day(self):
        tz = ZoneInfo(refresh_slot.TIMEZONE)
        day = date(2026, 1, 1)
        while day < date(2028, 1, 1):
            with self.subTest(day=day):
                kept = [c for c in refresh_slot.CRONS
                        if refresh_slot.is_refresh_slot(c, refresh_slot.cron_instance(
                            c, datetime(day.year, day.month, day.day, 23, tzinfo=timezone.utc)))]
                self.assertEqual(len(kept), 1)
                inst = refresh_slot.cron_instance(kept[0], datetime(day.year, day.month, day.day, 23,
                                                                    tzinfo=timezone.utc))
                self.assertEqual(inst.astimezone(tz).strftime('%Y-%m-%d %H:%M'), f'{day} 21:00')
            day += timedelta(days=1)

    def test_dst_transition_evenings(self):
        for day, expected in [('2026-03-28', WINTER), ('2026-03-29', SUMMER),
                              ('2026-10-24', SUMMER), ('2026-10-25', WINTER), ('2026-10-26', WINTER),
                              ('2026-01-15', WINTER), ('2026-07-15', SUMMER)]:
            with self.subTest(day=day):
                d = date.fromisoformat(day)
                late = datetime(d.year, d.month, d.day, 23, 30, tzinfo=timezone.utc)
                self.assertEqual([c for c in refresh_slot.CRONS if refresh_slot.is_refresh_slot(c, late)],
                                 [expected])

    def test_actual_shell_gate_including_delays(self):
        steps = WORKFLOW['jobs']['schedule_gate']['steps']
        self.assertEqual(steps[0]['with']['sparse-checkout'], 'scripts/ti_ops_director/refresh_slot.py')
        script = steps[1]['run']
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)
        # (event, cron, nominal UTC fire time, expected) around the 25 Oct 2026 autumn change
        # (01:00 UTC; EEST 21:00 = 18:00 UTC before it, EET 21:00 = 19:00 UTC after it) and
        # the 29 Mar 2026 spring change.
        cases = [('schedule', SUMMER, utc(2026, 10, 24, 18), 'true'),
                 ('schedule', WINTER, utc(2026, 10, 24, 19), 'false'),
                 ('schedule', SUMMER, utc(2026, 10, 25, 18), 'false'),
                 ('schedule', WINTER, utc(2026, 10, 25, 19), 'true'),
                 ('schedule', SUMMER, utc(2026, 10, 26, 18), 'false'),
                 ('schedule', WINTER, utc(2026, 10, 26, 19), 'true'),
                 ('schedule', SUMMER, utc(2026, 3, 28, 18), 'false'),
                 ('schedule', WINTER, utc(2026, 3, 28, 19), 'true'),
                 ('schedule', SUMMER, utc(2026, 3, 29, 18), 'true'),
                 ('schedule', WINTER, utc(2026, 3, 29, 19), 'false'),
                 ('workflow_dispatch', '', utc(2026, 10, 25, 3), 'true'),
                 ('schedule', '0 21 * * *', utc(2026, 10, 24, 21), 'false'),
                 ('schedule', '', utc(2026, 10, 24, 18), 'false'),
                 ('push', SUMMER, utc(2026, 10, 24, 18), 'false')]
        # Queue delays, including 3h20m (the measured 20-21 Oct delay), one that crosses UTC midnight
        # and the 25 Oct 01:00 UTC DST change, and one just under 24 hours: none changes a decision.
        delays = [timedelta(0), timedelta(minutes=21), timedelta(hours=3, minutes=20),
                  timedelta(hours=7, minutes=30), timedelta(hours=23, minutes=59)]
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / 'date'
            stub.write_text('#!/bin/sh\necho "$FAKE_EPOCH"\n')
            stub.chmod(0o755)
            for delay in delays:
                for event, cron, fired, expected in cases:
                    with self.subTest(delay=str(delay), event=event, cron=cron, fired=fired.isoformat()):
                        output = Path(tmp) / 'output'
                        output.write_text('')
                        result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script], cwd=ROOT,
                            env={**os.environ, 'PATH': tmp + ':' + os.environ['PATH'],
                                 'FAKE_EPOCH': str(int((fired + delay).timestamp())),
                                 'EVENT_NAME': event, 'SCHEDULED_CRON': cron,
                                 'GITHUB_OUTPUT': str(output)},
                            capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(output.read_text(), f'should_run={expected}\n')


if __name__ == '__main__':
    unittest.main()
