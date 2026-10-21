"""Publisher attribution, incident classification and incident dates in the monitor's early reports.

Inputs are a hand-written Google News-shaped RSS sample and the RECORDED live Haiku 5.5 decisions from the
approved evaluation (21 Oct 2026). Offline, no API key, no charge. The triage prompt is not changed.
"""
import contextlib
import importlib.util
import io
import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("monitor_provenance", ROOT / "scripts" / "continuous_intelligence_monitor.py")
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)

RSS = (ROOT / "tests" / "fixtures" / "google_news_rss_sample.xml").read_bytes()
RECORDED = json.loads((ROOT / "tests/fixtures/ops_director/monitor_recorded_haiku55.json").read_text())["decisions"]
NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)


def empty():
    return {"countries": [{"id": c} for c in ("gb", "us", "cy")], "records": [], "sources": [], "locations": [],
            "events": [], "sourceObservations": [], "meta": {"counts": {}, "currentIntelligence": {}}}


def publish(items, decisions):
    data = empty()
    with mock.patch.object(monitor, "now_utc", lambda: NOW), contextlib.redirect_stdout(io.StringIO()):
        monitor.publish(data, items, decisions)
    return data


class PublisherAttribution(unittest.TestCase):
    def setUp(self):
        self.items = monitor.parse_feed(RSS, "https://news.google.com/rss/search?q=x")

    def test_parser_keeps_publisher_and_article_link_separately(self):
        first = self.items[0]
        self.assertEqual((first["publisher"], first["publisherUrl"]), ("Reuters", "https://www.reuters.com"))
        self.assertTrue(first["url"].startswith("https://news.google.com/rss/articles/"))
        self.assertEqual(self.items[2]["publisher"], "")

    def test_event_credits_the_publisher_not_google_news(self):
        decision = dict(RECORDED["gb_drone_dated"][0], countryId="gb")
        data = publish(self.items[:1], [decision])
        src, obs, event = data["sources"][0], data["sourceObservations"][0], data["events"][0]
        self.assertEqual((src["sourceName"], src["organisation"], src["url"]), ("Reuters", "Reuters", "https://www.reuters.com"))
        self.assertEqual(src["discoveredVia"], "Google News RSS")
        self.assertEqual(obs["publisherName"], "Reuters")
        self.assertTrue(obs["sourceUrl"].startswith("https://news.google.com/rss/articles/"))   # link retained
        self.assertTrue(event["summary"].startswith("Reuters (found via Google News RSS) reports: "))
        self.assertNotIn("news.google.com", json.dumps([src["sourceName"], src["organisation"], src["url"]]))

    def test_missing_publisher_is_stated_not_guessed_from_the_redirect_host(self):
        decision = dict(RECORDED["cy_airport_suspension"][0])
        data = publish(self.items[2:3], [decision])
        src, obs, event = data["sources"][0], data["sourceObservations"][0], data["events"][0]
        self.assertEqual((src["sourceName"], src["organisation"], src["url"]), ("Original publisher not identified", None, None))
        self.assertIsNone(obs["publisherName"])
        self.assertIn("original publisher is not identified", event["summary"])
        self.assertNotEqual(src["sourceName"], "news.google.com")

    def test_direct_publisher_feed_is_credited_to_its_own_site(self):
        item = {"title": "Port of Limassol closed by strike", "url": "https://in-cyprus.philenews.com/a/1",
                "date": "Wed, 21 Oct 2026 06:00:00 GMT", "feed": "https://in-cyprus.philenews.com/rss"}
        _, who = monitor.attribution(item)
        self.assertEqual((who["sourceName"], who["url"], who["discoveredVia"]),
                         ("in-cyprus.philenews.com", "https://in-cyprus.philenews.com", None))

    def test_atom_source_title_is_the_publisher(self):
        atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Airspace closed over region</title>
        <link href="https://aggregator.example/r/1"/><published>2026-10-21T06:00:00Z</published>
        <source><title>Cyprus Mail</title><link href="https://cyprus-mail.com/"/></source></entry></feed>"""
        item = monitor.parse_feed(atom, "f")[0]
        self.assertEqual((item["publisher"], item["publisherUrl"]), ("Cyprus Mail", "https://cyprus-mail.com"))


class IncidentClassification(unittest.TestCase):
    def test_recorded_chemical_plant_fire_is_not_published_as_a_natural_hazard(self):
        items = monitor.parse_feed(RSS, "f")[1:2]
        self.assertEqual(RECORDED["us_local_hazmat"][0]["category"], "NATURAL_HAZARD")     # what Haiku 5.5 said
        event = publish(items, RECORDED["us_local_hazmat"])["events"][0]
        self.assertEqual((event["eventType"], event["eventSubtype"]), ("OTHER", "INDUSTRIAL_INCIDENT"))

    def test_genuine_natural_hazards_keep_the_label(self):
        for title in ("Wildfire near Limassol forces evacuation of three villages, B8 road closed",
                      "Riyadh flights delayed as sandstorm cuts visibility",
                      "Earthquake damages refinery near Izmit"):
            self.assertEqual(monitor.classify_event("NATURAL_HAZARD", title), ("NATURAL_HAZARD", None), title)

    def test_other_categories_are_left_alone(self):
        self.assertEqual(monitor.classify_event("TRANSPORT_DISRUPTION", "Tanker explosion closes expressway"),
                         ("TRANSPORT_DISRUPTION", None))
        self.assertEqual(monitor.classify_event("MADE_UP", "anything"), ("OTHER", None))


class IncidentDates(unittest.TestCase):
    def test_recorded_gatwick_date_is_taken_from_the_headline_not_today(self):
        items = monitor.parse_feed(RSS, "f")[:1]
        self.assertIsNone(RECORDED["gb_drone_dated"][0]["incidentDate"])                   # Haiku 5.5 missed it
        event = publish(items, RECORDED["gb_drone_dated"])["events"][0]
        self.assertEqual((event["eventDate"], event["incidentDateBasis"]), ("2026-10-19", "headline_text"))
        self.assertEqual(event["reportPublishedAt"], "2026-10-21T06:40:00+00:00")
        self.assertEqual(event["retrievedAt"], "2026-10-21T09:00:00Z")

    def test_no_stated_date_leaves_the_incident_date_empty(self):
        items = monitor.parse_feed(RSS, "f")[2:3]
        data = publish(items, RECORDED["cy_airport_suspension"])
        event, obs = data["events"][0], data["sourceObservations"][0]
        self.assertIsNone(event["eventDate"])                       # not silently today's date
        self.assertEqual(event["incidentDateBasis"], "not_stated")
        self.assertEqual(obs["publicationDate"], "2026-10-21")      # the report's own date, kept separately
        self.assertEqual(obs["dateChecked"], "2026-10-21")

    def test_model_supplied_date_is_kept(self):
        items = monitor.parse_feed(RSS, "f")[2:3]
        decision = dict(RECORDED["cy_airport_suspension"][0], incidentDate="2026-10-20")
        event = publish(items, [decision])["events"][0]
        self.assertEqual((event["eventDate"], event["incidentDateBasis"]), ("2026-10-20", "model_from_headline"))

    def test_headline_date_rules(self):
        ref = date(2026, 10, 21)
        cases = {"Flights halted since 19 October": date(2026, 10, 19),
                 "Gatwick flights suspended since 19 October after repeated drone sightings": date(2026, 10, 19),
                 "Heathrow Terminal 5 evacuated on 20 October": date(2026, 10, 20),
                 "Storm hit on Oct. 3, 2026": date(2026, 10, 3),
                 "Airport closed since 19 October; flights resume 22 October": date(2026, 10, 19),
                 "Airport shut on 30 December": None,                 # would be 10 months old: not guessed
                 "Flights resume December 30": None,                  # future reopening, not an incident date
                 "Flights resume 22 October": None,                   # resumption date, report of 21 October
                 "Flights resume on 21 October": None,                # resumption, even on the report's own day
                 "Airport expected to reopen on 20 October": None,    # planned reopening
                 "Strike planned on 20 October": None,                # planned, not occurred
                 "Airport closed 19 October": None,                   # meaning not shown ("since"/"on" absent)
                 "Closed from 2026-10-18 to 2026-10-20": None,        # range: not an incident date
                 "Riyadh flights delayed as sandstorm cuts visibility": None}
        for title, expected in cases.items():
            self.assertEqual(monitor.stated_date(title, ref), expected, title)

    def test_29_february_never_crashes(self):
        # Yearless 29 February in a non-leap reference year, and rolled back from a leap year into a non-leap one.
        self.assertIsNone(monitor.stated_date("Port shut since 29 February", date(2026, 10, 21)))
        self.assertIsNone(monitor.stated_date("Port shut since 29 February", date(2028, 2, 10)))
        self.assertEqual(monitor.stated_date("Port shut since 29 February", date(2028, 3, 5)), date(2028, 2, 29))
        item = {"title": "Port shut since 29 February after strike", "url": "https://example.org/feb",
                "date": "Thu, 10 Feb 2028 06:00:00 GMT", "feed": "f"}
        decision = dict(RECORDED["cy_airport_suspension"][0], continuingImpact=False)
        self.assertEqual(publish([item], [decision])["events"], [])          # undated, not ongoing: review

    def test_resumption_headline_never_supplies_the_incident_date(self):
        item = {"title": "Larnaca flights resume 22 October after fuel system repair", "url": "https://example.org/res",
                "date": "Wed, 21 Oct 2026 06:00:00 GMT", "feed": "f"}
        base = dict(RECORDED["cy_airport_suspension"][0])
        # Not ongoing and no incident date: held for review, nothing published.
        self.assertEqual(publish([item], [dict(base, continuingImpact=False)])["events"], [])
        # Ongoing: published, but the resumption date is not presented as the incident date.
        event = publish([item], [dict(base, continuingImpact=True)])["events"][0]
        self.assertEqual((event["eventDate"], event["incidentDateBasis"]), (None, "not_stated"))
        # Even if the model returns the future date itself, it is not used as the incident date.
        event = publish([item], [dict(base, continuingImpact=True, incidentDate="2026-10-22")])["events"][0]
        self.assertEqual((event["eventDate"], event["incidentDateBasis"]), (None, "not_stated"))
        self.assertEqual(publish([item], [dict(base, continuingImpact=False, incidentDate="2026-10-22")])["events"], [])

    def test_valid_since_date_still_publishes_with_its_date(self):
        item = {"title": "Airport closed since 19 October; flights resume 22 October", "url": "https://example.org/mix",
                "date": "Wed, 21 Oct 2026 06:00:00 GMT", "feed": "f"}
        event = publish([item], [dict(RECORDED["cy_airport_suspension"][0], continuingImpact=False)])["events"][0]
        self.assertEqual((event["eventDate"], event["incidentDateBasis"]), ("2026-10-19", "headline_text"))

    def test_old_stated_date_still_goes_to_review_unless_ongoing(self):
        item = {"title": "Airport closed on 2 October after fuel leak", "url": "https://example.org/old",
                "date": "Wed, 21 Oct 2026 06:00:00 GMT", "feed": "f"}
        decision = dict(RECORDED["cy_airport_suspension"][0], continuingImpact=False)
        self.assertEqual(publish([item], [decision])["events"], [])


class PromptUnchanged(unittest.TestCase):
    def test_triage_prompt_is_the_evaluated_one(self):
        """These fixes are post-processing only; the prompt Haiku 5.5 was evaluated on must not drift."""
        import hashlib
        src = (ROOT / "scripts" / "continuous_intelligence_monitor.py").read_text()
        body = src[src.index("def build_request"):src.index("def decisions_valid")]
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest()[:16], "5aec0a5ec23f8f9c")


if __name__ == "__main__":
    unittest.main()
