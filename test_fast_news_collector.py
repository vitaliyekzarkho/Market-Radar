from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fast_news_collector import append_new, is_fresh, parse_feed, topics_for


NOW = datetime(2026, 10, 9, 19, 0, tzinfo=timezone.utc)


class FastNewsCollectorTests(unittest.TestCase):
    def test_rss_is_normalized_as_unverified_discovery(self) -> None:
        xml = b"""<rss><channel><item><title>US Treasury yields jump and gold falls</title><link>https://news.test/a</link><pubDate>Fri, 09 Oct 2026 18:55:00 GMT</pubDate><description>Bond move</description></item></channel></rss>"""
        result = parse_feed(xml, "test", NOW)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["verification"], "unverified_report")
        self.assertIn("rates_bonds", result[0]["topics"])
        self.assertIn("gold_metals", result[0]["topics"])
        self.assertFalse(any(result[0]["permissions"].values()))

    def test_irrelevant_headline_is_excluded(self) -> None:
        xml = b"""<rss><channel><item><title>Celebrity attends film premiere</title><pubDate>Fri, 09 Oct 2026 18:55:00 GMT</pubDate></item></channel></rss>"""
        self.assertEqual(parse_feed(xml, "test", NOW), [])

    def test_atom_feed_is_supported(self) -> None:
        xml = b"""<feed xmlns='http://www.w3.org/2005/Atom'><entry><title>Iran ceasefire sends Brent oil lower</title><updated>2026-10-09T18:55:00Z</updated><link href='https://news.test/b'/></entry></feed>"""
        result = parse_feed(xml, "test", NOW)
        self.assertIn("geopolitics", result[0]["topics"])
        self.assertIn("oil_energy", result[0]["topics"])

    def test_archive_is_append_only_and_deduplicated(self) -> None:
        event = {"event_id": "one", "published_at_utc": NOW.isoformat(), "title": "Gold"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.jsonl"
            self.assertEqual(append_new(path, [event]), 1)
            self.assertEqual(append_new(path, [event]), 0)
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)

    def test_topic_classifier_keeps_policy_and_macro_separate(self) -> None:
        topics = topics_for("Trump comments on tariffs before CPI release")
        self.assertIn("us_policy", topics)
        self.assertIn("macro_data", topics)

    def test_future_timestamp_beyond_clock_skew_is_not_fresh(self) -> None:
        self.assertTrue(is_fresh(NOW + timedelta(minutes=5), NOW, 48))
        self.assertFalse(is_fresh(NOW + timedelta(minutes=6), NOW, 48))


if __name__ == "__main__":
    unittest.main(verbosity=2)
