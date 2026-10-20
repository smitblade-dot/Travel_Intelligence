"""Bounded HTTP recovery and validated, per-run airport reference caching."""
import csv
import io
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'config' / 'phase1_sources.json'


def source_config(source_id):
    return next(s for s in json.loads(CONFIG.read_text())['sources'] if s['id'] == source_id)


def fetch(url, attempts=3):
    for attempt in range(attempts):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'TravelIntelligence/1.0'}), timeout=20) as response:
                return response.read()
        except HTTPError as exc:
            # Never include request URLs: FIRMS keys are embedded in their path.
            if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise RuntimeError(f'HTTP {exc.code}') from None
            retry_after = exc.headers.get('Retry-After', '')
            delay = 5 * (attempt + 1)
            if retry_after.isdigit():
                delay = int(retry_after)
            elif retry_after:
                try:
                    delay = max(0, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError):
                    pass
            if delay > 60:
                raise RuntimeError(f'HTTP {exc.code}; provider requested a later retry') from None
            time.sleep(delay)
        except (URLError, TimeoutError, OSError):
            if attempt == attempts - 1:
                raise RuntimeError('Network request failed after bounded retries') from None
            time.sleep(5 * (attempt + 1))


def csv_rows(raw, required):
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    if not reader.fieldnames or not set(required).issubset(reader.fieldnames):
        raise ValueError('Response is not the expected CSV schema')
    return list(reader)


def airport_reference():
    # Same-run cache outside the checkout: never swept into a safety-net commit.
    run_id = os.environ.get('GITHUB_RUN_ID')
    cache = Path(os.environ.get('RUNNER_TEMP', tempfile.gettempdir())) / f'ti-airports-{run_id}.json'
    if run_id and cache.exists():
        return json.loads(cache.read_text())
    config = source_config('ourairports-airports')
    errors = []
    for url in [config['endpoint'], *config.get('fallbackEndpoints', [])]:
        try:
            rows = csv_rows(fetch(url), ['ident', 'iso_country', 'latitude_deg', 'longitude_deg', 'type', 'name', 'scheduled_service'])
            if not rows or not all(r.get('ident') and len(r.get('iso_country', '')) == 2 for r in rows):
                raise ValueError('Airport reference is empty or malformed')
            result = {'rows': rows, 'sourceUrl': url, 'fetchedAt': datetime.now(timezone.utc).isoformat()}
            if run_id:
                cache.write_text(json.dumps(result))
            return result
        except (RuntimeError, ValueError) as exc:
            errors.append(str(exc))
    raise RuntimeError('Airport reference unavailable: ' + '; '.join(errors))
