#!/usr/bin/env python3
"""Require current-run, completed research evidence before a refresh can publish.

This validates the reviewer's completion claim and coverage, not the truth of
source claims. SourceObservation and TI model validation remain mandatory.
"""
import json
import os
from pathlib import Path
import re
import shlex
import sys
from urllib.parse import urlsplit

def review_scope(data):
    return {'countryIds': sorted(c['id'] for c in data['countries']),
            'hotTopicIds': sorted(e['id'] for e in data.get('events', [])
                                  if e.get('lifecycleStage') == 'HOT_TOPIC' and
                                  e.get('eventStatus') not in ('RESOLVED', 'SUPERSEDED', 'FALSE') and
                                  e.get('publicationStatus') != 'WITHDRAWN')}


def shell_background(command):
    # A heuristic for accidental shell backgrounding, not a shell sandbox.
    # Remove ordinary fd duplication so `2>&1` is not mistaken for a job.
    command = re.sub(r'\d*>\s*&\s*\d+', '', command)
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars='&;|')
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return True
    return '&' in tokens or any(t in ('nohup', 'disown', 'setsid') for t in tokens)


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


def validate(report, execution, data, run_id, expected=None):
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
           (t.get('input') or {}).get('run_in_background') is True or
           (t.get('name') == 'Bash' and shell_background((t.get('input') or {}).get('command', '')))
           for t in tools):
        raise ValueError('Delegated/background research is not permitted in this one-shot refresh')
    if not any(t.get('name') in ('WebSearch', 'WebFetch') for t in tools):
        raise ValueError('No live source research was recorded')
    scope = review_scope(data)
    expected = expected or scope
    if set(scope['countryIds']) != set(expected['countryIds']):
        raise ValueError('Country inventory changed during review')
    topics = set(expected['hotTopicIds']) | set(scope['hotTopicIds'])
    for field, ids_expected in [('countryReviews', set(expected['countryIds'])), ('hotTopicReviews', topics)]:
        rows = report.get(field)
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ValueError('Invalid review coverage')
        ids = [r.get('id') for r in rows]
        if len(ids) != len(ids_expected) or set(ids) != ids_expected:
            raise ValueError('Review coverage is missing, duplicated or unexpected: ' + field)
        for row in rows:
            urls = row.get('sourceUrls')
            if (row.get('outcome') not in ('changed', 'no_material_change') or
                    not isinstance(row.get('note'), str) or not row['note'].strip() or
                    not isinstance(urls, list) or not urls or
                    any(not isinstance(u, str) or urlsplit(u).scheme != 'https' or
                        not urlsplit(u).hostname or urlsplit(u).username for u in urls)):
                raise ValueError('Unverified country/topic or missing source evidence')
    return f"PASS: completed review of {len(data['countries'])} countries and {len(topics)} Hot Topics; no pending work"


def main():
    evidence = Path(os.environ.get('RUNNER_TEMP', '.')) / 'ti-refresh-review.json'
    scope_path = Path(os.environ.get('RUNNER_TEMP', '.')) / 'ti-refresh-input.json'
    if sys.argv[1:] == ['prepare']:
        scope = review_scope(json.loads(Path('data.json').read_text()))
        scope['runId'] = os.environ['GITHUB_RUN_ID']
        scope_path.write_text(json.dumps(scope) + '\n')
        print('schema=' + json.dumps(schema(), separators=(',', ':')))
        return 0
    if sys.argv[1:] == ['schema']:
        print(json.dumps(schema(), separators=(',', ':')))
        return 0
    report = None
    diagnostic = {'validation': 'FAILED', 'reason': 'Missing or malformed completion evidence',
                  'candidateStatus': 'UNAPPROVED - diagnostic only; do not publish without review'}
    try:
        report = json.loads(os.environ.get('REVIEW_OUTPUT') or 'null')
        execution = json.loads(Path(os.environ['EXECUTION_FILE']).read_text())
        data = json.loads(Path('data.json').read_text())
        expected = json.loads(scope_path.read_text())
        if expected.get('runId') != os.environ.get('GITHUB_RUN_ID'):
            raise ValueError('Wrong input-scope run ID')
        try:
            summary = validate(report, execution, data, os.environ.get('GITHUB_RUN_ID'), expected)
        except ValueError as exc:
            diagnostic['reason'] = str(exc)  # validate() uses fixed messages, never raw source/error bodies.
            raise
        diagnostic.update(validation='PASS', reason=summary)
        print(summary)
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        print('::error::Intelligence review completion is unverified; publication stopped')
        return 1
    finally:
        # Keep the parseable report on failure too, but never raw SDK logs/env.
        diagnostic['review'] = report if isinstance(report, dict) else None
        evidence.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    sys.exit(main())
