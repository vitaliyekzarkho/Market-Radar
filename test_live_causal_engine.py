from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from live_causal_engine import build_live_causal
from reaction_window_engine import build_reaction_report


NOW = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)
EVENT_TIME = NOW - timedelta(hours=2)


def candles(direction: int) -> list[dict]:
    rows, price = [], 100.0
    for index in range(32):
        opened = NOW - timedelta(hours=7) + timedelta(minutes=15 * index)
        close = price + direction * 0.15
        rows.append({"utc_time": opened.isoformat(), "open": price, "high": max(price, close), "low": min(price, close), "close": close, "tick_volume": 100, "is_closed": True})
        price = close
    return rows


def payload(event: dict) -> dict:
    assets = {name: {"symbol": name, "source": "test", "timeframe": "M15", "candles": candles(direction)} for name, direction in (("gold", -1), ("oil", 1), ("usd", 1))}
    return {"available": True, "event": event, "reaction_report": build_reaction_report(event, assets, NOW)}


class LiveCausalEngineTests(unittest.TestCase):
    def test_calendar_fact_cannot_self_verify_from_market_move(self) -> None:
        event = {
            "event_id": "ppi-live", "title": "US PPI", "event_time_utc": EVENT_TIME.isoformat(),
            "event_family": "inflation_upside_surprise", "actual": "0.4%", "forecast": "0.2%",
            "status": "released", "source": "Forex Factory", "source_verified": False,
        }
        result = build_live_causal(payload(event), NOW)
        self.assertTrue(result["available"])
        self.assertFalse(result["source_gate"]["fact_permission"])
        self.assertFalse(result["summary"]["driver_confirmation_allowed"])
        self.assertNotEqual(result["final_contract"]["lifecycle_state"], "confirmed_driver")

    def test_inline_release_is_context_only(self) -> None:
        event = {
            "event_id": "cpi-inline-live", "title": "US CPI", "event_time_utc": EVENT_TIME.isoformat(),
            "event_family": "inflation_in_line", "status": "released", "source": "BLS",
            "source_verified": True, "primary_source": True, "fact_status": "verified",
            "release_components": [{"name": "CPI m/m", "actual": "0.3%", "forecast": "0.3%"}],
        }
        result = build_live_causal(payload(event), NOW)
        self.assertEqual(result["summary"]["classification"], "no_directional_surprise")
        self.assertEqual(result["summary"]["causal_role"], "context_only")
        self.assertFalse(result["summary"]["driver_confirmation_allowed"])

    def test_event_identity_survives_full_live_chain(self) -> None:
        event = {
            "event_id": "cpi-live", "title": "US CPI", "event_time_utc": EVENT_TIME.isoformat(),
            "event_family": "inflation_upside_surprise", "status": "released", "source": "BLS",
            "source_verified": True, "primary_source": True, "fact_status": "verified",
        }
        result = build_live_causal(payload(event), NOW)
        self.assertEqual(result["event_id"], result["validation"]["event_id"])
        for evaluation in result["validation"]["evaluations"]:
            self.assertEqual(evaluation["event_id"], result["event_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
