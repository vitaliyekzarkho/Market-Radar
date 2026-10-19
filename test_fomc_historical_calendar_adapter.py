from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fomc_historical_calendar_adapter import build_payload, historical_url


class FomcHistoricalCalendarAdapterTests(unittest.TestCase):
    def test_url_uses_exact_meeting_date_without_snapshot_fallback(self) -> None:
        url = historical_url("secret", "2026-09-16")
        self.assertIn("/united%20states/2026-09-16/2026-09-16?", url)
        self.assertIn("c=secret", url)

    def test_successful_dated_response_creates_explicit_daily_coverage(self) -> None:
        payload = build_payload("2026-09-16", [{
            "CalendarId": "1", "Date": "2026-09-16T18:00:00", "Importance": 3,
            "Event": "Fed Interest Rate Decision", "Actual": "4%", "Source": "Federal Reserve",
        }], datetime(2026, 10, 9, tzinfo=timezone.utc))
        self.assertTrue(payload["available"])
        self.assertEqual(payload["coverage"]["status"], "complete")
        self.assertEqual(payload["coverage"]["end_utc"], "2026-09-17T00:00:00+00:00")
        self.assertEqual(len(payload["events"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
