"""Keep restored sensitive Hot Topics behind existing publication controls."""
import json
import unittest
from pathlib import Path

class HotTopicPublicationTests(unittest.TestCase):
    def test_restored_topics_retain_review_pending_draft_status(self):
        data = json.loads((Path(__file__).resolve().parents[1] / 'data.json').read_text())
        events = {e['id']: e for e in data['events']}
        for eid in ('gb-fairford-bomber-redeployment-2026-10-04', 'ru-sochi-aframax-rio-2026-10-06'):
            with self.subTest(event=eid):
                event = events[eid]
                self.assertEqual(event['lifecycleStage'], 'HOT_TOPIC')
                self.assertTrue(event['sensitiveAllegation'])
                self.assertEqual(event['legalReviewStatus'], 'REVIEW_RECOMMENDED')
                self.assertEqual(event['publicationStatus'], 'DRAFT')
                self.assertTrue(event['sourceObservationIds'])
                self.assertTrue(any(c['eventStatus'] in ('UNVERIFIED', 'DISPUTED') for c in event['claimAssessments']))

if __name__ == '__main__':
    unittest.main()
