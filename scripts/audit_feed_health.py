#!/usr/bin/env python3
"""Fail visibly on degraded discovery coverage, separately from TI model QA."""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit(root=ROOT):
    signals = json.loads((root / 'data/phase1_signals.json').read_text())
    config = json.loads((root / 'config/phase1_sources.json').read_text())
    network = json.loads((root / 'data/transport_network.json').read_text())
    problems = []
    generated = datetime.fromisoformat(signals['generated'])
    age = datetime.now(timezone.utc) - generated if generated.tzinfo else None
    if age is None or age > timedelta(hours=24) or age < -timedelta(minutes=5):
        problems.append('Discovery snapshot is stale or has no timezone')
    lines = ['## Discovery feed health', '', '| Source | Status | Detail |', '|---|---|---|']
    for source in config['sources']:
        item = signals.get('sources', {}).get(source['id'], {})
        status = item.get('status', 'unverified')
        detail = item.get('error', '')
        if not detail and 'count' in item:
            detail = f"{item['count']} observations/reference rows"
        if 'requestedAreas' in item:
            detail += f"; areas {item.get('successfulAreas', 0)}/{item['requestedAreas']}"
        if not item.get('ok') or status != 'healthy':
            problems.append(source['id'] + ': ' + (detail or status))
        lines.append(f"| {source['name']} | {status} | {detail} |")
    reference = network.get('airportReference', {})
    if reference.get('status') != 'healthy' or not network.get('nodes', {}).get('airports'):
        problems.append('Airport reference is stale, unavailable or empty')
    lines.extend(['', f"Airport references: {len(network.get('nodes', {}).get('airports', []))}", '',
                  'Structural TI validation and feed availability are separate checks.'])
    summary = '\n'.join(lines) + '\n'
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
            output.write(summary)
    for problem in problems:
        print('::error title=Discovery coverage degraded::' + problem)
    return 1 if problems else 0


if __name__ == '__main__':
    try:
        sys.exit(audit())
    except (OSError, ValueError, KeyError, TypeError):
        print('::error::Feed health evidence is missing or malformed')
        sys.exit(1)
