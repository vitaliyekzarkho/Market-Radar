from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fast_news_story_engine import build_payload, cluster_events, same_story, source_lineage


NOW = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)


def event(event_id: str, title: str, minute: int, source: str = "financialjuice", topics=None) -> dict:
    return {
        "event_id": event_id, "title": title, "published_at_utc": f"2026-10-09T18:{minute:02d}:00+00:00",
        "source": source, "topics": topics or ["oil_energy", "us_policy"],
        "verification": "unverified_report",
    }


class FastNewsStoryEngineTests(unittest.TestCase):
    def test_same_diesel_development_becomes_one_story(self) -> None:
        a = event("a", "Trump: Russia to supply 300k tons of diesel - Truth Social", 1)
        b = event("b", "Trump says Russia will immediately supply diesel with millions more to follow", 5, "sky_us")
        self.assertTrue(same_story(a, b))
        self.assertEqual(len(cluster_events([a, b])), 1)

    def test_unrelated_trump_comments_do_not_merge(self) -> None:
        a = event("a", "Trump announces Russian diesel supply", 1)
        b = event("b", "Trump comments on Federal Reserve interest rates", 5, topics=["monetary_policy", "us_policy"])
        self.assertFalse(same_story(a, b))

    def test_unrelated_oil_production_stories_do_not_merge(self) -> None:
        a = event("a", "Venezuela expects oil production to reach 1.5 million BPD", 1, topics=["oil_energy"])
        b = event("b", "US Gulf shuts in 1.5 million b/d of oil production", 5, topics=["oil_energy"])
        self.assertFalse(same_story(a, b))

    def test_same_country_and_specific_subject_survive_different_wording(self) -> None:
        a = event("a", "US Treasury license allows Russian diesel into global market", 1, topics=["rates_bonds"])
        b = event("b", "Trump says Russia will immediately supply diesel", 5, topics=["us_policy"])
        self.assertTrue(same_story(a, b))

    def test_domestic_diesel_directive_stays_separate_from_russian_supply(self) -> None:
        a = event("a", "Trump directive seeks to control the cost of diesel", 1)
        b = event("b", "Trump says Russia will immediately supply diesel", 5)
        self.assertFalse(same_story(a, b))

    def test_many_outlets_do_not_fake_independent_origins(self) -> None:
        a = event("a", "Trump: Russia to supply diesel - Truth Social", 1)
        b = event("b", "Trump says Russia will immediately supply diesel with millions more to follow", 5, "sky_us")
        story = cluster_events([a, b])[0]
        self.assertEqual(story["status"], "multi_outlet_same_origin")
        self.assertEqual(story["independent_origin_count"], 1)
        self.assertFalse(story["permissions"]["confirm_driver"])

    def test_independent_attribution_is_corroboration_not_verification(self) -> None:
        a = event("a", "Trump says Russia will supply diesel - Axios", 1)
        b = event("b", "Trump confirms Russia diesel supply - Reuters", 5, "sky_us")
        story = cluster_events([a, b])[0]
        self.assertEqual(story["status"], "independently_corroborated")
        self.assertFalse(story["permissions"]["confirm_driver"])

    def test_official_fact_can_confirm_driver_but_never_entry(self) -> None:
        a = event("a", "US Treasury: license permits Russian diesel supply", 1)
        a.update({"verification": "official_verified", "verified_primary_source": True})
        story = cluster_events([a])[0]
        self.assertEqual(story["status"], "officially_verified")
        self.assertTrue(story["permissions"]["confirm_driver"])
        self.assertFalse(story["permissions"]["entry"])

    def test_relationship_distinguishes_repeat_and_development(self) -> None:
        rows = [
            event("a", "Trump: Russia to supply diesel - Truth Social", 1),
            event("b", "Trump: Russia to supply diesel - Truth Social", 2),
            event("c", "Trump: Russia to supply three million tons diesel - Truth Social", 3),
        ]
        members = cluster_events(rows)[0]["members"]
        self.assertEqual(members[0]["story_relationship"], "initial_report")
        self.assertEqual(members[1]["story_relationship"], "repeat_same_origin")
        self.assertEqual(members[2]["story_relationship"], "story_development")

    def test_different_numeric_components_are_not_called_repeats(self) -> None:
        rows = [
            event("a", "University Michigan 5 Yr Inflation Actual 3.5%", 1, topics=["macro_data"]),
            event("b", "University Michigan 1 Yr Inflation Actual 4.7%", 1, topics=["macro_data"]),
        ]
        members = cluster_events(rows)[0]["members"]
        self.assertEqual(members[1]["story_relationship"], "story_development")

    def test_payload_preserves_all_events(self) -> None:
        rows = [event("a", "Gold falls as Treasury yields jump", 1, topics=["gold_metals", "rates_bonds"])]
        payload = build_payload({"generated_at_utc": NOW.isoformat(), "events": rows}, NOW)
        self.assertEqual(payload["event_count"], 1)
        self.assertEqual(payload["story_count"], 1)

    def test_bridge_update_merges_two_fragments_of_one_story(self) -> None:
        rows = [
            event("a", "Trump says a big diesel announcement is coming", 1),
            event("b", "Russia will supply diesel to the global market", 5),
            event("c", "Trump announcement confirms Russian diesel supply", 9),
        ]
        stories = cluster_events(rows)
        self.assertEqual(len(stories), 1)
        self.assertEqual(stories[0]["member_count"], 3)

    def test_source_lineage_reads_named_origin(self) -> None:
        self.assertEqual(source_lineage(event("a", "Iran deal expected - Reuters", 1))["reported_origin"], "reuters")


if __name__ == "__main__":
    unittest.main(verbosity=2)
