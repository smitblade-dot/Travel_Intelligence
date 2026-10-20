"""Schedule contract and actual gate-shell regression tests (requires PyYAML)."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.load(
    (ROOT / '.github/workflows/daily-data-refresh.yml').read_text(),
    Loader=yaml.BaseLoader,
)


class RefreshScheduleTests(unittest.TestCase):
    def test_schedule_contract(self):
        self.assertEqual(WORKFLOW['on']['schedule'], [
            {'cron': '0 21 * * *', 'timezone': 'Europe/Nicosia'}])
        self.assertIn('workflow_dispatch', WORKFLOW['on'])
        self.assertEqual(WORKFLOW['jobs']['refresh']['needs'], 'schedule_gate')
        self.assertEqual(WORKFLOW['jobs']['refresh']['if'],
                         "needs.schedule_gate.outputs.should_run == 'true'")

    def test_summer_winter_and_transition_evenings(self):
        # 21:00 is outside both the skipped and repeated early-morning hours.
        for day, utc_hour in [('2026-01-15', 19), ('2026-07-15', 18),
                              ('2026-03-28', 19), ('2026-03-29', 18),
                              ('2026-10-24', 18), ('2026-10-25', 19)]:
            with self.subTest(day=day):
                local = datetime.fromisoformat(day + 'T21:00:00').replace(
                    tzinfo=ZoneInfo(WORKFLOW['on']['schedule'][0]['timezone']))
                self.assertEqual(local.astimezone(timezone.utc).hour, utc_hour)
                self.assertEqual(local.replace(fold=0).utcoffset(),
                                 local.replace(fold=1).utcoffset())

    def test_actual_shell_gate_including_delays(self):
        script = WORKFLOW['jobs']['schedule_gate']['steps'][0]['run']
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)
        with tempfile.TemporaryDirectory() as tmp:
            # Any return to runner wall-clock inspection must fail these cases.
            for executable in ('date', 'python', 'python3'):
                path = Path(tmp) / executable
                path.write_text('#!/bin/sh\necho "Unexpected clock lookup" >&2\nexit 99\n')
                path.chmod(0o755)
            cases = [('schedule', '0 21 * * *', 'true'),
                     ('workflow_dispatch', '', 'true'),
                     ('schedule', '0 18 * * *', 'false'),
                     ('schedule', '0 19 * * *', 'false'),
                     ('schedule', '', 'false'), ('push', '0 21 * * *', 'false')]
            # Delay labels include crossing each DST boundary. No clock input
            # exists in the gate, so queue time cannot change its decision.
            for delay in ('none', '2 hours', 'across spring DST', 'across autumn DST'):
                for event, cron, expected in cases:
                    with self.subTest(delay=delay, event=event, cron=cron):
                        output = Path(tmp) / 'output'
                        output.write_text('')
                        result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script],
                            env={**os.environ, 'PATH': tmp + ':' + os.environ['PATH'],
                                 'EVENT_NAME': event, 'SCHEDULED_CRON': cron,
                                 'GITHUB_OUTPUT': str(output)},
                            capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(output.read_text(), f'should_run={expected}\n')


if __name__ == '__main__':
    unittest.main()
