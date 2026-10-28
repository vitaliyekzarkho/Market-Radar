import copy
import json
import unittest
from pathlib import Path
from C3_3_3_validate_diesel import validate

ROOT = Path(__file__).resolve().parent
class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = [json.loads((ROOT / f).read_text(encoding='utf-8')) for f in ('fast_news_stories.json', 'fast_news_interpretations.json', 'diesel_historical_reaction.json')]
    def test_real_window(self):
        r = validate(*self.data)
        self.assertEqual(len(r['mechanisms']), 2)
        self.assertEqual(r['provenance']['anchor_offset_seconds'], 14)
        self.assertFalse(r['permissions']['entry'])
        self.assertFalse(r['permissions']['confirm_cause'])
    def test_wrong_report_rejected(self):
        d = copy.deepcopy(self.data)
        d[2]['event_id'] = 'wrong'
        with self.assertRaises(ValueError): validate(*d)
    def test_future_anchor_rejected(self):
        d = copy.deepcopy(self.data)
        d[2]['event_time_utc'] = '2026-10-09T19:00:00+00:00'
        with self.assertRaises(ValueError): validate(*d)
    def test_incomplete_24h(self):
        r = validate(*self.data, window='post_24h')
        self.assertTrue(all(m['status'] == 'insufficient_data' for m in r['mechanisms']))
if __name__ == '__main__': unittest.main()
