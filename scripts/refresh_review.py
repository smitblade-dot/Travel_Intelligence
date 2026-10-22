#!/usr/bin/env python3
"""Require current-run, completed research evidence before a refresh can publish.

This validates the reviewer's completion claim and coverage, not the truth of
source claims. SourceObservation and TI model validation remain mandatory.
"""
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

HOT_TOPICS = ('gb-fairford-bomber-redeployment-2026-10-04', 'ru-sochi-aframax-rio-2026-10-06')


def schema():
    review = {'type': 'object', 'additionalProperties': False,
              'properties': {'id': {'type': 'string'},
                             'outcome': {'type': 'string', 'enum': ['changed', 'no_material_change', 'unverified']},
                             'sourceUrls': {'type': 'array', 'items': {'type': 'string'}},
                             'note': {'type': 'string'}},
              'required': ['id', 'outcome', 'sourceUrls', 'note']}
    return {'type': 'object', 'additionalProperties': False,
            'properties': {'status': {'type': 'string', 'enum': ['COMPLETE', 'INCOMPLETE']},
                           'runId': {'type': 'string'}, 'pendingTasks': {'type': 'integer', 'minimum': 0},
                           'countryReviews': {'type': 'array', 'items': review},
                           'hotTopicReviews': {'type': 'array', 'items': review}},
            'required': ['status', 'runId', 'pendingTasks', 'countryReviews', 'hotTopicReviews']}


def validate(report, execution, data, run_id):
    if not run_id or not isinstance(report, dict) or report.get('runId') != run_id:
        raise ValueError('Missing completion report or wrong run ID')
    if report.get('status') != 'COMPLETE' or type(report.get('pendingTasks')) is not int or report['pendingTasks'] != 0:
        raise ValueError('Review is incomplete or has unfinished work')
    if not isinstance(execution, list) or not execution:
        raise ValueError('Missing execution evidence')
    results = [m for m in execution if isinstance(m, dict) and m.get('type') == 'result']
    if not results or results[-1].get('is_error') is not False or results[-1].get('subtype') != 'success':
        raise ValueError('Reviewer did not finish successfully')
    tools = []
    for message in execution:
        if not isinstance(message, dict):
            continue
        for block in (message.get('message') or {}).get('content', []):
            if isinstance(block, dict) and block.get('type') == 'tool_use':
                tools.append(block)
    if any(t.get('name') in ('Agent', 'Task', 'TeamCreate') or
           (t.get('input') or {}).get('run_in_background') is True for t in tools):
        raise ValueError('Delegated/background research is not permitted in this one-shot refresh')
    if not any(t.get('name') in ('WebSearch', 'WebFetch') for t in tools):
        raise ValueError('No live source research was recorded')
    for field, expected in [('countryReviews', {c['id'] for c in data['countries']}),
                            ('hotTopicReviews', set(HOT_TOPICS))]:
        rows = report.get(field)
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError('Invalid review coverage')
        ids = [r.get('id') for r in rows]
        if len(ids) != len(expected) or set(ids) != expected:
            raise ValueError('Review coverage is missing, duplicated or unexpected: ' + field)
        for row in rows:
            urls = row.get('sourceUrls')
            if (row.get('outcome') not in ('changed', 'no_material_change') or
                    not isinstance(row.get('note'), str) or not row['note'].strip() or
                    not isinstance(urls, list) or not urls or
                    any(not isinstance(u, str) or urlsplit(u).scheme != 'https' or
                        not urlsplit(u).hostname or urlsplit(u).username for u in urls)):
                raise ValueError('Unverified country/topic or missing source evidence')
    return f"PASS: completed review of {len(data['countries'])} countries and {len(HOT_TOPICS)} Hot Topics; no pending work"


def main():
    if sys.argv[1:] == ['schema']:
        print(json.dumps(schema(), separators=(',', ':')))
        return 0
    try:
        report = json.loads(os.environ.get('REVIEW_OUTPUT') or 'null')
        execution = json.loads(Path(os.environ['EXECUTION_FILE']).read_text())
        data = json.loads(Path('data.json').read_text())
        summary = validate(report, execution, data, os.environ.get('GITHUB_RUN_ID'))
        print(summary)
        # Store only the review contract, never raw SDK logs with environment data.
        evidence = Path(os.environ['RUNNER_TEMP']) / 'ti-refresh-review.json'
        evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print('::error::Intelligence review completion is unverified; publication stopped')
        return 1


if __name__ == '__main__':
    sys.exit(main())
