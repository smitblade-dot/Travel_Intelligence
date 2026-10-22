"""Publication must reject an early agent exit even if its process succeeded."""
import copy
import json
from pathlib import Path
import sys
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from refresh_review import HOT_TOPICS, validate


class ReviewCompletionTests(unittest.TestCase):
    def setUp(self):
        self.data = {'countries': [{'id': 'gb'}, {'id': 'cy'}]}
        def row(i):
            return {'id': i, 'outcome': 'no_material_change',
                    'sourceUrls': ['https://www.gov.uk/foreign-travel-advice'], 'note': 'Reviewed current guidance'}
        self.report = {'status': 'COMPLETE', 'runId': '123', 'pendingTasks': 0,
                       'countryReviews': [row('gb'), row('cy')], 'hotTopicReviews': [row(i) for i in HOT_TOPICS]}
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
        for name, args in [('Agent', {}), ('Task', {}), ('Bash', {'run_in_background': True})]:
            execution = copy.deepcopy(self.execution)
            execution[0]['message']['content'].append({'type': 'tool_use', 'name': name, 'input': args})
            with self.assertRaises(ValueError):
                self.check(execution=execution)

    def test_actual_workflow_requires_completion_before_metadata_and_commit(self):
        root = Path(__file__).resolve().parents[1]
        workflow = yaml.load((root / '.github/workflows/daily-data-refresh.yml').read_text(), Loader=yaml.BaseLoader)
        steps = workflow['jobs']['refresh']['steps']
        names = [s.get('name') for s in steps]
        gate = names.index('Verify intelligence review completed')
        self.assertLess(gate, names.index('Synchronise public intelligence metadata'))
        self.assertLess(gate, names.index('Safety-net commit and push'))
        self.assertNotIn('continue-on-error', steps[gate])
        agent = next(s for s in steps if s.get('id') == 'intelligence_review')
        self.assertIn('--tools "Bash,Read,Write,Edit,Glob,Grep,WebSearch,WebFetch"', agent['with']['claude_args'])
        self.assertIn('--json-schema', agent['with']['claude_args'])


if __name__ == '__main__':
    unittest.main()
