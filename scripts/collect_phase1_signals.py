#!/usr/bin/env python3
"""Collect discovery evidence; never publish events or infer operational status."""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode

from feed_support import airport_reference, csv_rows, fetch, source_config

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'phase1_signals.json'


def failure(error, status='unavailable'):
    return {'ok': False, 'status': status, 'error': str(error)}


def safe_json(url, field, attempts=3):
    try:
        data = json.loads(fetch(url, attempts=attempts))
        if not isinstance(data, dict) or not isinstance(data.get(field), list):
            raise ValueError(f'Response lacks expected {field} array')
        return {'ok': True, 'status': 'healthy', 'sourceUrl': url, 'data': data}
    except (RuntimeError, ValueError) as exc:
        return failure(exc)


def collect_ourairports():
    reference = airport_reference()
    return {'ok': True, 'status': 'healthy', 'sourceUrl': reference['sourceUrl'],
            'fetchedAt': reference['fetchedAt'], 'count': len(reference['rows']),
            'sample': reference['rows'][:10]}


def collect_gdelt():
    query = ('("airport closed" OR "airport closure" OR "airspace closed" OR '
             '"border closed" OR "border crossing closed" OR "flight cancelled" '
             'OR "drone ban" OR "drone restrictions")')
    # Bound the query to the recent refresh window, not the default long search.
    url = ('https://api.gdeltproject.org/api/v2/doc/doc?query=' + quote(query) +
           '&mode=artlist&format=json&maxrecords=50&sort=datedesc&timespan=24h')
    return safe_json(url, 'articles')


def collect_gdacs():
    return safe_json(source_config('gdacs-alerts')['endpoint'], 'features')


def collect_adsblol():
    config = source_config('adsblol-aircraft')
    countries = {c['id'].upper(): c['id'] for c in json.loads((ROOT / 'data.json').read_text())['countries']}
    points = {}
    rows = sorted(airport_reference()['rows'], key=lambda r: (r['type'] != 'large_airport', r['ident']))
    for row in rows:
        country = countries.get(row['iso_country'])
        if (country and country not in points and row.get('scheduled_service') == 'yes'
                and row['type'] in ('large_airport', 'medium_airport')):
            try:
                lat, lon = float(row['latitude_deg']), float(row['longitude_deg'])
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    continue
            except (ValueError, TypeError):
                continue
            points[country] = {'countryId': country, 'airportId': row['ident'], 'lat': lat, 'lon': lon}
    if not points:
        return failure('No validated airport points available for ADS-B sampling')
    areas, aircraft = [], {}
    deadline = time.monotonic() + 120
    for index, point in enumerate(points.values()):
        if time.monotonic() >= deadline:
            areas.append({**point, 'ok': False, 'error': 'Sampling time budget exhausted; not requested'})
            continue
        if index:
            time.sleep(1)  # Free community service: no concurrent/burst requests.
        url = config['endpoint'].format(lat=point['lat'], lon=point['lon'], radius=config['radiusNm'])
        result = safe_json(url, 'ac', attempts=1)
        areas.append({**point, 'sourceUrl': url, 'ok': result['ok'], 'error': result.get('error')})
        if result['ok']:
            for item in result['data']['ac']:
                if isinstance(item, dict) and item.get('hex'):
                    aircraft[item['hex']] = item
    successes = sum(a['ok'] for a in areas)
    return {'ok': successes > 0, 'status': 'healthy' if successes == len(areas) else 'degraded',
            'coverage': '100 nm samples around one scheduled airport per available tracked country; not national/global coverage. No aircraft is not evidence of closure.',
            'requestedAreas': len(areas), 'successfulAreas': successes, 'areas': areas,
            'unrepresentedCountryIds': sorted(set(countries.values()) - set(points)),
            'count': len(aircraft), 'data': {'ac': list(aircraft.values())[:200]},
            'storedSampleLimit': 200, 'attribution': 'ADSB.lol / ODbL 1.0'}


def collect_reliefweb():
    config = source_config('reliefweb-disasters')
    appname = os.environ.get(config['appnameEnvironment'], '').strip()
    if not appname:
        return failure('RELIEFWEB_APPNAME is not configured; obtain a provider-approved appname', 'blocked')
    url = config['endpoint'] + '?' + urlencode({'appname': appname, 'limit': 50, 'sort[]': 'date:desc'})
    return safe_json(url, 'data')


def collect_firms():
    config = source_config('nasa-firms')
    key = os.environ.get('FIRMS_MAP_KEY', '').strip()
    # Correct keyed API order is key / sensor / area / days.
    urls = ([config['endpoint'] + quote(key, safe='') + '/VIIRS_SNPP_NRT/world/1'] if key else [])
    urls.append(config['publicEndpoint'])
    for url in urls:
        try:
            rows = csv_rows(fetch(url), ['latitude', 'longitude', 'acq_date', 'acq_time'])
            dates = [datetime.strptime(r['acq_date'], '%Y-%m-%d').replace(tzinfo=timezone.utc) for r in rows]
            if dates and max(dates) < datetime.now(timezone.utc) - timedelta(days=2):
                raise ValueError('FIRMS response is stale')
            return {'ok': True, 'status': 'healthy', 'count': len(rows), 'sample': rows[:25],
                    'sourceUrl': config['publicEndpoint'] if url == config['publicEndpoint'] else config['endpoint'],
                    'accessMethod': 'public-csv' if url == config['publicEndpoint'] else 'api-key',
                    'coverage': 'Global S-NPP VIIRS near-real-time 24-hour file; stored observations are a bounded sample.',
                    'latestAcquisitionDate': max(dates).date().isoformat() if dates else None}
        except (RuntimeError, ValueError):
            continue
    return failure('FIRMS returned no usable current CSV (public download and any configured API key)')


def main():
    started = datetime.now(timezone.utc).isoformat()
    output = {'schemaVersion': '1.0', 'generated': started,
              'purpose': 'Reviewable Phase 1 signals; not published TI events.', 'sources': {}}
    collectors = {'ourairports-airports': collect_ourairports, 'gdelt-events': collect_gdelt,
                  'gdacs-alerts': collect_gdacs, 'adsblol-aircraft': collect_adsblol,
                  'reliefweb-disasters': collect_reliefweb, 'nasa-firms': collect_firms}
    for source_id, collector in collectors.items():
        try:
            item = collector()
        except Exception:
            # Generic exception handling must not expose credential-bearing URLs.
            item = failure('Collector failed; response could not be validated')
        item['attemptedAt'] = datetime.now(timezone.utc).isoformat()
        output['sources'][source_id] = item
        print(f"{source_id}: {item['status']}" + (f" — {item['error']}" if item.get('error') else ''), flush=True)
        if item['status'] != 'healthy':
            print(f"::warning title=Discovery coverage degraded::{source_id}: {item.get('error', 'partial response')}")
    output['health'] = 'healthy' if all(v['status'] == 'healthy' for v in output['sources'].values()) else 'degraded'
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    print(f'Wrote {OUT}; coverage={output["health"]}')
    # Keep independent intelligence verification running; strict health audit is
    # the final workflow step, after structural validation and safe publication.
    return 0


if __name__ == '__main__':
    sys.exit(main())
