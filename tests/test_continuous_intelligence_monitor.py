#!/usr/bin/env python3
"""Offline smoke tests for the continuous monitoring agent."""
import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "continuous_intelligence_monitor.py"
spec = importlib.util.spec_from_file_location("monitor", SCRIPT)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class MonitorTests(unittest.TestCase):
    def test_rss_parser(self):
        xml = b'<rss><channel><item><title>Airport closure reported</title><link>https://example.org/a</link><pubDate>Wed, 21 Oct 2026 07:00:00 GMT</pubDate></item></channel></rss>'
        items = monitor.parse_feed(xml, "https://example.org/feed")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://example.org/a")

    def test_publication_attributed_unverified(self):
        data = {"countries": [{"id": "gb"}], "records": [], "sources": [],
                "locations": [], "events": [], "sourceObservations": [],
                "meta": {"counts": {}, "currentIntelligence": {}}}
        items = [{"title": "Airport incident reported", "url": "https://example.org/story",
                  "date": "Wed, 21 Oct 2026 07:00:00 GMT"}]
        decisions = [{"index": 0, "countryId": "gb", "publish": True,
                      "existingId": None, "category": "TRANSPORT_DISRUPTION"}]
        self.assertEqual(monitor.publish(data, items, decisions), 1)
        self.assertEqual(data["events"][0]["publicationStatus"], "PUBLISHED")
        self.assertEqual(data["events"][0]["eventStatus"], "EARLY_REPORT")
        self.assertEqual(data["events"][0]["confidence"], "LOW")
        self.assertEqual(len(data["sourceObservations"]), 1)
        self.assertEqual(monitor.publish(data, items, decisions), 0)

    def test_reject_unknown_country(self):
        data = {"countries": [{"id": "gb"}], "records": [], "sources": [],
                "locations": [], "events": [], "sourceObservations": [],
                "meta": {"counts": {}, "currentIntelligence": {}}}
        items = [{"title": "Incident", "url": "https://example.org/x"}]
        self.assertEqual(monitor.publish(data, items, [{"index": 0, "countryId": "xx",
                          "publish": True}]), 0)


if __name__ == "__main__":
    unittest.main()
