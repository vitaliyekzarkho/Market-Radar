from __future__ import annotations

import unittest
from datetime import datetime, timezone

from news_driver_engine import build_payload


NOW = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)


def event(**overrides: object) -> dict:
    value = {
        "title": "US CPI",
        "event_time_utc": "2026-09-21T12:30:00+00:00",
        "importance": "high",
        "currencies": ["USD"],
        "status": "scheduled",
        "source": "Trading Economics",
        "source_url": "https://tradingeconomics.com/united-states/calendar",
    }
    value.update(overrides)
    return value


class NewsDriverEngineTests(unittest.TestCase):
    def test_upcoming_event_is_risk_only(self) -> None:
        result = build_payload({"events": [event()]}, NOW)["driver_context"]
        self.assertTrue(result["available"])
        self.assertEqual(result["effect"], "risk_only")
        self.assertFalse(result["scoring_applied"])

    def test_released_direction_is_preserved(self) -> None:
        result = build_payload({"events": [event(
            event_time_utc="2026-09-21T08:30:00+00:00",
            status="released",
            effect_xauusd="bullish",
            actual="2.7%",
            forecast="2.5%",
        )]}, NOW)["driver_context"]
        self.assertEqual(result["effect"], "bullish")
        self.assertEqual(result["status"], "released")

    def test_uninterpreted_release_remains_risk_only(self) -> None:
        result = build_payload({"events": [event(
            event_time_utc="2026-09-21T08:30:00+00:00",
            status="released",
        )]}, NOW)["driver_context"]
        self.assertEqual(result["effect"], "risk_only")

    def test_irrelevant_currency_is_ignored(self) -> None:
        result = build_payload({"events": [event(currencies=["NZD"])]}, NOW)["driver_context"]
        self.assertFalse(result["available"])

    def test_unverified_url_is_ignored(self) -> None:
        result = build_payload({"events": [event(source_url="")]} , NOW)["driver_context"]
        self.assertFalse(result["available"])

    def test_high_importance_wins_over_medium(self) -> None:
        result = build_payload({"events": [
            event(title="Medium", importance="medium", event_time_utc="2026-09-21T09:30:00+00:00"),
            event(title="High", importance="high", event_time_utc="2026-09-21T15:00:00+00:00"),
        ]}, NOW)["driver_context"]
        self.assertEqual(result["title"], "High")


if __name__ == "__main__":
    unittest.main(verbosity=2)
