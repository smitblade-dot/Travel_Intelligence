"""Offline recovery/regression checks. No credentials or external requests."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import feed_support as support
import collect_phase1_signals as collector
import build_transport_network as transport
import audit_feed_health as health

AIRPORT = {'ident': 'EGLL', 'iso_country': 'GB', 'name': 'Heathrow', 'type': 'large_airport',
           'latitude_deg': '51.47', 'longitude_deg': '-0.45', 'scheduled_service': 'yes'}


class FeedRecoveryTests(unittest.TestCase):
    def test_retry_transient_and_honour_retry_after(self):
        response = contextlib.nullcontext(io.BytesIO(b'valid'))
        error = HTTPError('https://example.org/private-key', 429, 'limited', {'Retry-After': '7'}, None)
        with patch.object(support, 'urlopen', side_effect=[error, response]) as request, patch.object(support.time, 'sleep') as sleep:
            self.assertEqual(support.fetch('https://example.org/private-key'), b'valid')
            self.assertEqual(request.call_count, 2)
            sleep.assert_called_once_with(7)

    def test_permanent_http_failure_is_not_retried_or_leaked(self):
        error = HTTPError('https://example.org/secret-token', 403, 'secret-token', {}, None)
        with patch.object(support, 'urlopen', side_effect=error) as request:
            with self.assertRaisesRegex(RuntimeError, '^HTTP 403$'):
                support.fetch('https://example.org/secret-token')
            self.assertEqual(request.call_count, 1)

    def test_long_retry_after_defers_without_hammering(self):
        error = HTTPError('https://example.org', 429, '', {'Retry-After': '120'}, None)
        with patch.object(support, 'urlopen', side_effect=error) as request, patch.object(support.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, 'later retry'):
                support.fetch('https://example.org')
            self.assertEqual(request.call_count, 1)
            sleep.assert_not_called()

    def test_reject_html_as_csv_and_json_error_payload(self):
        with self.assertRaises(ValueError):
            support.csv_rows(b'<html>Rate limited</html>', ['latitude', 'longitude'])
        with patch.object(collector, 'fetch', return_value=b'{"error":"rate limited"}'):
            self.assertFalse(collector.safe_json('https://example.org', 'articles')['ok'])

    def test_airport_fallback_requires_valid_csv(self):
        header = ','.join(AIRPORT) + '\n'
        raw = (header + ','.join(AIRPORT.values()) + '\n').encode()
        with patch.dict(os.environ, {}, clear=True), patch.object(support, 'source_config', return_value={
                'endpoint': 'https://primary', 'fallbackEndpoints': ['https://official-mirror']}), patch.object(support, 'fetch', side_effect=[b'<html>error</html>', raw]):
            result = support.airport_reference()
            self.assertEqual(result['sourceUrl'], 'https://official-mirror')
            self.assertEqual(result['rows'][0]['ident'], 'EGLL')

    def test_firms_works_without_key_using_public_csv(self):
        today = datetime.now(timezone.utc).date().isoformat()
        raw = f'latitude,longitude,acq_date,acq_time\n1,2,{today},1200\n'.encode()
        with patch.dict(os.environ, {}, clear=True), patch.object(collector, 'fetch', return_value=raw) as fetch:
            result = collector.collect_firms()
            self.assertTrue(result['ok'])
            self.assertEqual(result['accessMethod'], 'public-csv')
            self.assertIn('/data/active_fire/', fetch.call_args.args[0])

    def test_firms_key_order_and_fallback_do_not_publish_key(self):
        today = datetime.now(timezone.utc).date().isoformat()
        raw = f'latitude,longitude,acq_date,acq_time\n1,2,{today},1200\n'.encode()
        with patch.dict(os.environ, {'FIRMS_MAP_KEY': 'private-token'}), patch.object(collector, 'fetch', side_effect=[RuntimeError('HTTP 403'), raw]) as fetch:
            result = collector.collect_firms()
            self.assertIn('/private-token/VIIRS_SNPP_NRT/world/1', fetch.call_args_list[0].args[0])
            self.assertTrue(result['ok'])
            self.assertNotIn('private-token', json.dumps(result))

    def test_firms_rejects_stale_csv(self):
        raw = b'latitude,longitude,acq_date,acq_time\n1,2,2020-01-01,1200\n'
        with patch.dict(os.environ, {}, clear=True), patch.object(collector, 'fetch', return_value=raw):
            self.assertFalse(collector.collect_firms()['ok'])

    def test_reliefweb_needs_approved_configuration_and_uses_v2(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(collector, 'fetch') as fetch:
            self.assertEqual(collector.collect_reliefweb()['status'], 'blocked')
            fetch.assert_not_called()
        with patch.dict(os.environ, {'RELIEFWEB_APPNAME': 'approved-test'}), patch.object(collector, 'fetch', return_value=b'{"data":[]}') as fetch:
            self.assertTrue(collector.collect_reliefweb()['ok'])
            self.assertIn('/v2/reports?', fetch.call_args.args[0])
            self.assertIn('appname=approved-test', fetch.call_args.args[0])

    def test_adsb_is_explicit_geographic_sample_and_partial_is_degraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data.json').write_text(json.dumps({'countries': [{'id': 'gb'}, {'id': 'cy'}]}))
            cy = {**AIRPORT, 'ident': 'LCPH', 'iso_country': 'CY'}
            with patch.object(collector, 'ROOT', root), patch.object(collector, 'airport_reference', return_value={'rows': [AIRPORT, cy]}), patch.object(collector.time, 'sleep'), patch.object(collector, 'safe_json', side_effect=[
                    {'ok': True, 'data': {'ac': [{'hex': 'abc'}]}}, {'ok': False, 'error': 'HTTP 503'}]) as fetch:
                result = collector.collect_adsblol()
                self.assertTrue(result['ok'])
                self.assertEqual(result['status'], 'degraded')
                self.assertEqual(result['successfulAreas'], 1)
                self.assertEqual(result['requestedAreas'], 2)
                self.assertTrue(all('/v2/point/' in call.args[0] for call in fetch.call_args_list))

    def test_adsb_budget_marks_unrequested_areas_as_degraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data.json').write_text(json.dumps({'countries': [{'id': 'gb'}]}))
            with patch.object(collector, 'ROOT', root), patch.object(collector, 'airport_reference', return_value={'rows': [AIRPORT]}), patch.object(collector.time, 'monotonic', side_effect=[0, 601]), patch.object(collector, 'safe_json') as fetch:
                result = collector.collect_adsblol()
                fetch.assert_not_called()
                self.assertFalse(result['ok'])
                self.assertEqual(result['status'], 'degraded')
                self.assertIn('not requested', result['areas'][0]['error'])


class TransportAndHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'data').mkdir()
        (self.root / 'config').mkdir()
        self.data = self.root / 'data.json'
        self.out = self.root / 'data/transport_network.json'
        self.data.write_text(json.dumps({'countries': [{'id': 'gb', 'iso3': 'GBR'}], 'locations': []}))

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, reference):
        with patch.object(transport, 'DATA', self.data), patch.object(transport, 'OUT', self.out), patch.object(transport, 'airport_reference', **reference), contextlib.redirect_stdout(io.StringIO()):
            return transport.main()

    def test_two_letter_country_match_and_no_invented_routes(self):
        self.build({'return_value': {'rows': [AIRPORT], 'sourceUrl': 'https://official', 'fetchedAt': '2026-10-20T00:00:00Z'}})
        result = json.loads(self.out.read_text())
        self.assertEqual(result['nodes']['airports'][0]['countryId'], 'gb')
        self.assertEqual(result['edges']['scheduledRoutes'], [])
        self.assertEqual(result['airportReference']['status'], 'healthy')

    def test_outage_preserves_previous_nodes_and_original_fetch_time(self):
        self.test_two_letter_country_match_and_no_invented_routes()
        old = json.loads(self.out.read_text())
        self.build({'side_effect': RuntimeError('HTTP 503')})
        result = json.loads(self.out.read_text())
        self.assertEqual(result['nodes']['airports'], old['nodes']['airports'])
        self.assertEqual(result['airportReference']['status'], 'stale')
        self.assertEqual(result['airportReference']['fetchedAt'], old['airportReference']['fetchedAt'])

    def test_no_cache_or_empty_cache_fails_without_overwriting(self):
        self.out.write_text('{"nodes":{"airports":[]}}')
        original = self.out.read_bytes()
        self.assertEqual(self.build({'side_effect': RuntimeError('HTTP 500')}), 1)
        self.assertEqual(self.out.read_bytes(), original)

    def test_health_fails_for_missing_degraded_and_stale_evidence(self):
        (self.root / 'config/phase1_sources.json').write_text(json.dumps({'sources': [{'id': 'feed', 'name': 'Feed'}]}))
        signal = {'generated': datetime.now(timezone.utc).isoformat(), 'sources': {'feed': {'ok': True, 'status': 'healthy'}}}
        (self.root / 'data/phase1_signals.json').write_text(json.dumps(signal))
        self.out.write_text(json.dumps({'airportReference': {'status': 'healthy'}, 'nodes': {'airports': [AIRPORT]}}))
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(health.audit(self.root), 0)
            signal['sources']['feed']['status'] = 'degraded'
            (self.root / 'data/phase1_signals.json').write_text(json.dumps(signal))
            self.assertEqual(health.audit(self.root), 1)
            signal['sources'] = {}
            (self.root / 'data/phase1_signals.json').write_text(json.dumps(signal))
            self.assertEqual(health.audit(self.root), 1)
            signal['sources'] = {'feed': {'ok': True, 'status': 'healthy'}}
            signal['generated'] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            (self.root / 'data/phase1_signals.json').write_text(json.dumps(signal))
            self.assertEqual(health.audit(self.root), 1)


if __name__ == '__main__':
    unittest.main()
