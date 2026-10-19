from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from live_reaction_engine import build_payload, select_recent_event


NOW = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)


def candles(direction: int) -> list[dict]:
    rows = []
    price = 100.0
    start = NOW - timedelta(hours=7)
    for index in range(28):
        opened = start + timedelta(minutes=15 * index)
        close = price + direction * 0.1
        rows.append({"utc_time": opened.isoformat(), "open": price, "high": max(price, close), "low": min(price, close), "close": close, "tick_volume": 100, "is_closed": True})
        price = close
    return rows


class LiveReactionEngineTests(unittest.TestCase):
    def test_selects_latest_recent_released_usd_event(self) -> None:
        events = [
            {"title": "Old", "currencies": ["USD"], "actual": "1", "event_time_utc": (NOW - timedelta(hours=7)).isoformat()},
            {"title": "Recent", "currencies": ["USD"], "actual": "1", "event_time_utc": (NOW - timedelta(hours=1)).isoformat()},
            {"title": "Scheduled", "currencies": ["USD"], "actual": None, "event_time_utc": NOW.isoformat()},
        ]
        self.assertEqual(select_recent_event(events, NOW)["title"], "Recent")

    def test_reaction_windows_are_tied_to_selected_event(self) -> None:
        event_time = NOW - timedelta(hours=2)
        raw = {"events": [{"title": "US CPI", "currencies": ["USD"], "actual": "0.3%", "forecast": "0.2%", "event_time_utc": event_time.isoformat()}]}
        assets = {name: {"symbol": name, "source": "test", "timeframe": "M15", "candles": candles(direction)} for name, direction in (("gold", -1), ("oil", 1), ("usd", 1))}
        payload = build_payload(raw, NOW, assets)
        self.assertTrue(payload["available"])
        self.assertEqual(payload["event"]["event_id"], payload["reaction_report"]["event_id"])
        self.assertEqual(payload["reaction_report"]["event_time_utc"], event_time.isoformat())
        self.assertTrue(payload["reaction_report"]["assets"]["gold"]["post_event"]["post_60m"]["complete"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
