"""Publication must reject an early agent exit even if its process succeeded."""
import copy
import json
from pathlib import Path
import sys
import os
import subprocess
import tempfile
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from refresh_review import validate, shell_background


class ReviewCompletionTests(unittest.TestCase):
    def setUp(self):
        self.data = {'countries': [{'id': 'gb'}, {'id': 'cy'}],
                     'events': [{'id': 'topic', 'lifecycleStage': 'HOT_TOPIC'}]}
        def row(i):
            return {'id': i, 'outcome': 'no_material_change',
                    'sourceUrls': ['https://www.gov.uk/foreign-travel-advice'], 'note': 'Reviewed current guidance'}
        self.report = {'status': 'COMPLETE', 'runId': '123', 'pendingTasks': 0,
                       'countryReviews': [row('gb'), row('cy')], 'hotTopicReviews': [row('topic')]}
        self.execution = [{'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'name': 'WebFetch', 'input': {'url': 'https://www.gov.uk/foreign-travel-advice'}}]}},
            {'type': 'result', 'subtype': 'success', 'is_error': False}]

    def check(self, report=None, execution=None):
        return validate(self.report if report is None else report,
                        self.execution if execution is None else execution, self.data, '123')

    def test_complete_no_change_review_is_valid_without_forcing_data_edits(self):
        self.assertIn('PASS', self.check())

    def test_early_exit_missing_current_run_or_unverified_coverage_cannot_publish(self):
        cases = [None, {}, {**self.report, 'status': 'INCOMPLETE'}, {**self.report, 'pendingTasks': 5},
                 {**self.report, 'runId': 'old'}, {**self.report, 'pendingTasks': False}]
        for field in ('countryReviews', 'hotTopicReviews'):
            cases.append({**self.report, field: self.report[field][:-1]})
            cases.append({**self.report, field: self.report[field] + [self.report[field][0]]})
            for patch in ({'outcome': 'unverified'}, {'sourceUrls': []}, {'sourceUrls': ['file:///tmp/stale']}, {'note': ''}):
                r = copy.deepcopy(self.report)
                r[field][0].update(patch)
                cases.append(r)
        for report in cases:
            with self.subTest(report=report), self.assertRaises(ValueError):
                validate(report, self.execution, self.data, '123')

    def test_successful_process_with_background_work_or_no_research_is_rejected(self):
        for execution in ([], [self.execution[-1]],
                          [*self.execution, {'type': 'result', 'subtype': 'error_max_turns', 'is_error': True}]):
            with self.assertRaises(ValueError):
                self.check(execution=execution)
        for name, args in [('Agent', {}), ('Task', {}), ('Bash', {'run_in_background': True}),
                           ('Bash', {'command': 'sleep 20 &'}), ('Bash', {'command': 'nohup worker'})]:
            execution = copy.deepcopy(self.execution)
            execution[0]['message']['content'].append({'type': 'tool_use', 'name': name, 'input': args})
            with self.assertRaises(ValueError):
                self.check(execution=execution)

    def test_shell_background_check_allows_normal_redirection_and_quoted_ampersands(self):
        self.assertFalse(shell_background("python3 check.py 2>&1 && echo done"))
        self.assertFalse(shell_background("printf '%s' 'literal & value'"))
        self.assertTrue(shell_background('worker & wait'))

    def test_scope_tracks_active_topics_but_keeps_review_due_for_newly_resolved_topic(self):
        expected = {'countryIds': ['gb', 'cy'], 'hotTopicIds': ['topic']}
        self.data['events'][0]['eventStatus'] = 'RESOLVED'
        self.assertIn('PASS', validate(self.report, self.execution, self.data, '123', expected))
        self.data['countries'].pop()
        with self.assertRaisesRegex(ValueError, 'inventory'):
            validate(self.report, self.execution, self.data, '123', expected)

    def test_cli_retains_parseable_failed_report_and_reason(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/refresh_review.py'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data.json').write_text(json.dumps(self.data))
            (root / 'execution.json').write_text(json.dumps(self.execution))
            env = {**os.environ, 'RUNNER_TEMP': tmp, 'GITHUB_RUN_ID': '123',
                   'EXECUTION_FILE': str(root / 'execution.json'),
                   'REVIEW_OUTPUT': json.dumps({**self.report, 'pendingTasks': 5})}
            subprocess.run([sys.executable, str(script), 'prepare'], cwd=tmp, env=env, capture_output=True, check=True)
            result = subprocess.run([sys.executable, str(script)], cwd=tmp, env=env, capture_output=True)
            self.assertEqual(result.returncode, 1)
            evidence = json.loads((root / 'ti-refresh-review.json').read_text())
            self.assertEqual(evidence['validation'], 'FAILED')
            self.assertIn('unfinished', evidence['reason'])
            self.assertEqual(evidence['review']['pendingTasks'], 5)

    def test_actual_workflow_requires_completion_before_metadata_and_commit(self):
        root = Path(__file__).resolve().parents[1]
        workflow = yaml.load((root / '.github/workflows/daily-data-refresh.yml').read_text(), Loader=yaml.BaseLoader)
        steps = workflow['jobs']['refresh']['steps']
        names = [s.get('name') for s in steps]
        gate = names.index('Verify intelligence review completed')
        self.assertLess(gate, names.index('Synchronise public intelligence metadata'))
        self.assertLess(gate, names.index('Safety-net commit and push'))
        self.assertNotIn('continue-on-error', steps[gate])
        candidate = next(s for s in steps if s.get('name') == 'Preserve unapproved candidate for recovery')
        self.assertEqual(candidate['if'], 'always()')
        self.assertLess(names.index(candidate['name']), gate)
        agent = next(s for s in steps if s.get('id') == 'intelligence_review')
        self.assertIn('--tools "Bash,Read,Write,Edit,Glob,Grep,WebSearch,WebFetch"', agent['with']['claude_args'])
        self.assertIn('--json-schema', agent['with']['claude_args'])


if __name__ == '__main__':
    unittest.main()
