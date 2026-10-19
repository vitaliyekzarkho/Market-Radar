from __future__ import annotations

import unittest

from intermarket_context_engine import build_context
from scenario_state import news_hypothesis_context


class IntermarketContextTests(unittest.TestCase):
    def test_rising_yields_with_bearish_gold_is_correlated_not_verified_cause(self) -> None:
        context = build_context({"context": {
            "available": True, "direction": "rising",
            "observations": {"US10Y": {"normalized_4h": 1.4}},
        }}, {"gold_reaction": "bearish"})
        driver = context["drivers"][0]
        self.assertEqual(driver["effect_xauusd"], "bearish")
        self.assertEqual(driver["causal_status"], "correlated")
        self.assertFalse(driver["verified"])

    def test_iran_oil_rotation_stays_hypothesis_until_gold_confirms(self) -> None:
        inputs = {
            "gold_reaction": "unknown",
            "oil": {"symbol": "BRENT", "direction": "falling", "change_percent": -3.0},
            "headlines": [{
                "title": "Iran agrees to suspend enrichment", "theme": "iran_deescalation",
                "source_url": "https://example.com/source", "published_at_utc": "2026-09-29T08:00:00Z",
            }],
        }
        driver = build_context({}, inputs)["drivers"][0]
        self.assertEqual(driver["causal_status"], "inferred")
        self.assertEqual(driver["effect_xauusd"], "neutral")

    def test_intermarket_driver_is_used_when_calendar_has_no_driver(self) -> None:
        news = news_hypothesis_context(
            {"available": False}, "bearish",
            {"intermarket": {"drivers": [{
                "title": "US yields rising", "effect_xauusd": "bearish", "causal_status": "correlated",
            }]}},
        )
        self.assertTrue(news["available"])
        self.assertEqual(news["role"], "correlated_driver")


if __name__ == "__main__":
    unittest.main(verbosity=2)
