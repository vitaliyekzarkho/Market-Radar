from __future__ import annotations

import unittest
from datetime import datetime, timezone

from official_event_matcher import enrich_events


NOW = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)


def calendar() -> dict:
    return {"events": [{
        "title": "ISM Services PMI", "event_time_utc": "2026-10-05T14:00:00+00:00",
        "status": "scheduled", "actual": None, "forecast": "55.1",
        "currencies": ["USD"],
        "source": "Forex Factory", "source_url": "https://www.forexfactory.com/calendar",
    }]}


def fact(**changes) -> dict:
    value = {
        "fact_id": "ism_services_pmi:2026-09", "indicator": "ism_services_pmi",
        "report_period": "2026-09", "actual": "54.9", "published_at_utc": "2026-10-05T14:00:00+00:00",
        "source": "Institute for Supply Management", "source_url": "https://www.ismworld.org/report",
        "provenance": {"fact_permission": True, "primary_verified": True, "kind": "official_primary"},
    }
    value.update(changes)
    return value


class OfficialEventMatcherTests(unittest.TestCase):
    def test_verified_fact_completes_calendar_event(self) -> None:
        result = enrich_events(calendar(), {"facts": [fact()]}, NOW)
        event = result["events"][0]
        self.assertEqual(event["actual"], "54.9")
        self.assertEqual(event["forecast"], "55.1")
        self.assertEqual(event["status"], "released")
        self.assertTrue(event["source_verified"])
        self.assertTrue(event["authoritative_fact"])
        self.assertTrue(event["primary_source"])
        self.assertEqual(result["official_match_audit"][0]["status"], "MATCHED:OFFICIAL_TIMESTAMP")

    def test_wrong_report_period_cannot_complete_event(self) -> None:
        result = enrich_events(calendar(), {"facts": [fact(report_period="2026-08")]}, NOW)
        self.assertIsNone(result["events"][0]["actual"])
        self.assertEqual(result["official_match_audit"][0]["status"], "NO_MATCH")

    def test_large_time_mismatch_cannot_complete_event(self) -> None:
        result = enrich_events(calendar(), {"facts": [fact(published_at_utc="2026-10-06T14:00:00+00:00")]}, NOW)
        self.assertIsNone(result["events"][0]["actual"])

    def test_missing_official_timestamp_uses_explicit_calendar_binding(self) -> None:
        result = enrich_events(calendar(), {"facts": [fact(published_at_utc=None)]}, NOW)
        event = result["events"][0]
        self.assertEqual(event["actual"], "54.9")
        self.assertEqual(event["fact_timing_basis"], "calendar_schedule_bound")
        self.assertEqual(result["official_match_audit"][0]["status"], "MATCHED:CALENDAR_SCHEDULE_BOUND")

    def test_duplicate_official_facts_are_rejected_as_ambiguous(self) -> None:
        result = enrich_events(calendar(), {"facts": [fact(), fact(fact_id="duplicate")]}, NOW)
        self.assertIsNone(result["events"][0]["actual"])
        self.assertEqual(result["official_match_audit"][0]["status"], "AMBIGUOUS_MATCH")

    def test_foreign_manufacturing_pmi_is_not_treated_as_ism(self) -> None:
        foreign = calendar()
        foreign["events"][0].update({"title": "German Flash Manufacturing PMI", "currencies": ["EUR"]})
        result = enrich_events(foreign, {"facts": [fact(indicator="ism_manufacturing_pmi")]}, NOW)
        self.assertEqual(result["official_match_audit"], [])
        self.assertIsNone(result["events"][0]["actual"])

    def test_syndicated_issuer_fact_is_authoritative_but_not_primary_domain(self) -> None:
        syndicated = fact(
            source="Institute for Supply Management via PR Newswire",
            source_url="https://www.prnewswire.com/news-releases/example.html",
            source_transport="official_syndicated_release",
            provenance={
                "kind": "official_syndicated_release", "fact_permission": True,
                "primary_verified": False,
            },
        )
        event = enrich_events(calendar(), {"facts": [syndicated]}, NOW)["events"][0]
        self.assertTrue(event["source_verified"])
        self.assertTrue(event["authoritative_fact"])
        self.assertFalse(event["primary_source"])
        self.assertFalse(event["verified_primary_source"])
        self.assertEqual(event["source_transport"], "official_syndicated_release")


if __name__ == "__main__":
    unittest.main(verbosity=2)
