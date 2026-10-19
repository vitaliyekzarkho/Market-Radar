from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from trading_economics_adapter import api_url, normalized_payload, usable_cache


NOW = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)


def raw_event(**overrides: object) -> dict:
    value = {
        "CalendarId": "123",
        "Date": "2026-09-21T12:30:00",
        "Country": "United States",
        "Category": "Inflation Rate",
        "Event": "Inflation Rate YoY",
        "Source": "U.S. Bureau of Labor Statistics",
        "SourceURL": "https://www.bls.gov",
        "Actual": "",
        "Previous": "2.6%",
        "Forecast": "2.7%",
        "Importance": 3,
    }
    value.update(overrides)
    return value


class TradingEconomicsAdapterTests(unittest.TestCase):
    def test_url_has_country_dates_and_encoded_key(self) -> None:
        url = api_url("name:key+secret", NOW)
        self.assertIn("calendar/country/united%20states/2026-09-20/2026-09-22", url)
        self.assertIn("c=name%3Akey%2Bsecret", url)

    def test_snapshot_fallback_url_has_no_dates(self) -> None:
        url = api_url("name:key", NOW, dated=False)
        self.assertIn("calendar/country/united%20states?", url)
        self.assertNotIn("2026-09-20", url)

    def test_normalizes_high_impact_us_event(self) -> None:
        payload = normalized_payload([raw_event()], NOW)
        event = payload["events"][0]
        self.assertEqual(event["currencies"], ["USD"])
        self.assertEqual(event["assets"], ["XAUUSD"])
        self.assertEqual(event["status"], "scheduled")

    def test_actual_marks_event_released(self) -> None:
        payload = normalized_payload([raw_event(Actual="2.8%")], NOW)
        self.assertEqual(payload["events"][0]["status"], "released")

    def test_low_impact_event_is_filtered(self) -> None:
        payload = normalized_payload([raw_event(Importance=1)], NOW)
        self.assertEqual(payload["events"], [])

    def test_invalid_source_url_uses_calendar(self) -> None:
        payload = normalized_payload([raw_event(SourceURL="not-a-url")], NOW)
        self.assertTrue(payload["events"][0]["source_url"].startswith("https://"))

    def test_recent_cache_is_usable(self) -> None:
        cache = {"fetched_at_utc": (NOW - timedelta(hours=1)).isoformat(), "events": []}
        self.assertTrue(usable_cache(cache, NOW))

    def test_stale_cache_is_rejected(self) -> None:
        cache = {"fetched_at_utc": (NOW - timedelta(hours=7)).isoformat(), "events": []}
        self.assertFalse(usable_cache(cache, NOW))


if __name__ == "__main__":
    unittest.main(verbosity=2)
