from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from forex_factory_adapter import normalize_event, normalized_payload, usable_cache


NOW = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)


def raw_event(**overrides: object) -> dict:
    value = {
        "title": "Unemployment Claims",
        "country": "USD",
        "date": "2026-09-24T08:30:00-04:00",
        "impact": "Medium",
        "forecast": "201K",
        "previous": "196K",
    }
    value.update(overrides)
    return value


class ForexFactoryAdapterTests(unittest.TestCase):
    def test_actual_is_preserved_and_marks_release(self) -> None:
        event = normalize_event(raw_event(actual="0.4%"))
        self.assertEqual(event["actual"], "0.4%")
        self.assertEqual(event["status"], "released")

    def test_normalizes_medium_usd_event(self) -> None:
        event = normalize_event(raw_event())
        self.assertIsNotNone(event)
        self.assertEqual(event["currencies"], ["USD"])
        self.assertEqual(event["assets"], ["XAUUSD"])
        self.assertEqual(event["importance"], "medium")

    def test_high_usd_event_is_kept(self) -> None:
        self.assertIsNotNone(normalize_event(raw_event(impact="High")))

    def test_low_event_is_filtered(self) -> None:
        self.assertIsNone(normalize_event(raw_event(impact="Low")))

    def test_eur_event_is_kept_for_contamination_filter(self) -> None:
        event = normalize_event(raw_event(country="EUR"))
        self.assertEqual(event["currencies"], ["EUR"])
        self.assertEqual(event["assets"], [])

    def test_currency_outside_breadth_basket_is_filtered(self) -> None:
        self.assertIsNone(normalize_event(raw_event(country="JPY")))

    def test_feed_timezone_is_preserved_for_engine_conversion(self) -> None:
        event = normalize_event(raw_event())
        self.assertTrue(event["event_time_utc"].endswith("-04:00"))

    def test_payload_has_public_calendar_source(self) -> None:
        payload = normalized_payload([raw_event()], NOW)
        self.assertEqual(payload["provider"], "Forex Factory")
        self.assertTrue(payload["events"][0]["source_url"].startswith("https://"))

    def test_recent_cache_is_usable(self) -> None:
        cache = {"fetched_at_utc": (NOW - timedelta(hours=1)).isoformat(), "events": []}
        self.assertTrue(usable_cache(cache, NOW))

    def test_stale_cache_is_rejected(self) -> None:
        cache = {"fetched_at_utc": (NOW - timedelta(hours=7)).isoformat(), "events": []}
        self.assertFalse(usable_cache(cache, NOW))


if __name__ == "__main__":
    unittest.main(verbosity=2)
