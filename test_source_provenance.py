from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from source_provenance import (
    canonical_title,
    classify_source,
    cluster_items,
    freshness_status,
)
from market_news_adapter import enrich_payload as enrich_headlines, parse_feed
from official_macro_adapter import enrich_payload as enrich_official


NOW = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)


class SourceProvenanceTests(unittest.TestCase):
    def test_official_domain_can_confirm_fact(self) -> None:
        result = classify_source(
            "https://www.federalreserve.gov/newsevents/pressreleases/test.htm",
            "official_primary", True,
        )
        self.assertEqual(result["kind"], "official_primary")
        self.assertTrue(result["fact_permission"])

    def test_google_news_is_discovery_only(self) -> None:
        result = classify_source("https://news.google.com/rss/articles/test", "aggregated_headline", False)
        self.assertTrue(result["discovery_only"])
        self.assertFalse(result["fact_permission"])

    def test_calendar_confirms_schedule_not_fact(self) -> None:
        result = classify_source("https://www.forexfactory.com/calendar")
        self.assertEqual(result["kind"], "licensed_calendar")
        self.assertTrue(result["schedule_permission"])
        self.assertFalse(result["fact_permission"])

    def test_declared_primary_on_unknown_domain_does_not_self_verify(self) -> None:
        result = classify_source("https://example.com/news", "official_primary", True)
        self.assertFalse(result["primary_verified"])
        self.assertFalse(result["fact_permission"])

    def test_verified_issuer_release_on_prnewswire_can_confirm_fact(self) -> None:
        result = classify_source(
            "https://www.prnewswire.com/news-releases/example.html",
            "official_syndicated_release", True,
        )
        self.assertEqual(result["kind"], "official_syndicated_release")
        self.assertTrue(result["fact_permission"])
        self.assertFalse(result["schedule_permission"])

    def test_prnewswire_article_without_explicit_issuer_verification_cannot_confirm_fact(self) -> None:
        result = classify_source(
            "https://www.prnewswire.com/news-releases/example.html",
            "official_syndicated_release", False,
        )
        self.assertFalse(result["fact_permission"])

    def test_freshness_distinguishes_live_cache_and_expired(self) -> None:
        fresh = freshness_status((NOW - timedelta(minutes=20)).isoformat(), NOW, "headline", "live")
        cached = freshness_status((NOW - timedelta(minutes=20)).isoformat(), NOW, "headline", "cache")
        expired = freshness_status((NOW - timedelta(hours=40)).isoformat(), NOW, "headline", "live")
        self.assertEqual(fresh["status"], "fresh")
        self.assertEqual(cached["status"], "recent")
        self.assertEqual(expired["status"], "expired")

    def test_google_publisher_suffix_is_removed_from_identity(self) -> None:
        self.assertEqual(
            canonical_title("Trump says Iran conflict will end soon - Reuters"),
            canonical_title("Trump: Iran conflict will end soon - CNBC"),
        )

    def test_duplicate_headlines_become_one_cluster(self) -> None:
        items = [
            {
                "title": "Trump says Iran conflict will end soon - Reuters",
                "theme": "iran_deescalation", "published_at_utc": "2026-10-06T09:00:00Z",
                "source_url": "https://news.google.com/story-1", "provenance": "aggregated_headline",
            },
            {
                "title": "Trump: Iran conflict will end soon - CNBC",
                "theme": "iran_deescalation", "published_at_utc": "2026-10-06T09:04:00Z",
                "source_url": "https://news.google.com/story-2", "provenance": "aggregated_headline",
            },
        ]
        clusters = cluster_items(items, NOW)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["member_count"], 2)

    def test_repeated_statement_on_another_day_is_not_merged(self) -> None:
        items = [
            {"title": "Trump says conflict will end soon", "theme": "iran_deescalation", "published_at_utc": "2026-10-05T09:00:00Z", "source_url": "https://news.google.com/1"},
            {"title": "Trump says conflict will end soon", "theme": "iran_deescalation", "published_at_utc": "2026-10-06T09:00:00Z", "source_url": "https://news.google.com/2"},
        ]
        self.assertEqual(len(cluster_items(items, NOW)), 2)

    def test_primary_source_wins_cluster_but_all_members_are_preserved(self) -> None:
        items = [
            {"title": "Federal Reserve issues FOMC statement", "published_at_utc": "2026-10-06T09:00:00Z", "source_url": "https://news.google.com/1", "provenance": "aggregated_headline"},
            {"title": "Federal Reserve issues FOMC statement", "published_at_utc": "2026-10-06T09:01:00Z", "source_url": "https://www.federalreserve.gov/test", "provenance": "official_primary", "verified_primary_source": True},
        ]
        cluster = cluster_items(items, NOW)[0]
        self.assertEqual(cluster["best_source"]["provenance"]["kind"], "official_primary")
        self.assertTrue(cluster["primary_fact_verified"])
        self.assertEqual(cluster["member_count"], 2)

    def test_google_rss_preserves_reporter_without_verifying_it(self) -> None:
        xml = b"""<rss><channel><item>
        <title>Trump says Iran conflict will end soon - Reuters</title>
        <link>https://news.google.com/story</link>
        <pubDate>Tue, 06 Oct 2026 09:30:00 GMT</pubDate>
        <source url="https://www.reuters.com">Reuters</source>
        </item></channel></rss>"""
        item = parse_feed(xml, "iran_deescalation", NOW)[0]
        self.assertEqual(item["reported_by"], "Reuters")
        payload = enrich_headlines({"mode": "live", "headlines": [item]}, NOW)
        self.assertTrue(payload["headlines"][0]["provenance"]["discovery_only"])
        self.assertFalse(payload["clusters"][0]["primary_fact_verified"])

    def test_official_payload_emits_verified_provenance_and_freshness(self) -> None:
        payload = enrich_official({
            "mode": "live",
            "recent_releases": [{
                "title": "Federal Reserve issues FOMC statement",
                "published_at_utc": "2026-10-06T09:00:00Z",
                "source_url": "https://www.federalreserve.gov/test",
                "verified_primary_source": True,
            }],
        }, NOW)
        release = payload["recent_releases"][0]
        self.assertTrue(release["provenance"]["fact_permission"])
        self.assertEqual(release["freshness"]["status"], "fresh")


if __name__ == "__main__":
    unittest.main(verbosity=2)
