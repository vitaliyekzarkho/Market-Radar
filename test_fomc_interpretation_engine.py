from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fomc_interpretation_engine import interpret_decision


NOW = datetime(2026, 9, 16, 18, 1, tzinfo=timezone.utc)


def official(action: str = "hike", change_bps: int = 25, upper: float = 4.0) -> dict:
    return {
        "meeting_id": "2026-09-16",
        "decision": {
            "action": action, "change_bps": change_bps,
            "target_lower_pct": upper - 0.25, "target_upper_pct": upper,
        },
        "facts": [{"verified": True}] * 6,
        "timepoints": {
            "statement": {"verified": True}, "sep": {"verified": True},
            "press_conference": {"verified": True},
        },
    }


def calendar(forecast: str, title: str = "Federal Funds Rate") -> dict:
    return {"events": [{
        "title": title, "event_time_utc": "2026-09-16T14:00:00-04:00",
        "forecast": forecast, "source": "Forex Factory",
        "source_url": "https://www.forexfactory.com/calendar", "provenance": "licensed_calendar",
    }]}


class FomcInterpretationEngineTests(unittest.TestCase):
    def test_expected_hike_is_in_line_not_hawkish(self) -> None:
        result = interpret_decision(official("hike", 25, 4.0), calendar("4.00%"), NOW)
        self.assertEqual(result["official_decision"]["action"], "hike")
        self.assertEqual(result["decision_surprise"]["classification"], "in_line")
        self.assertFalse(result["decision_surprise"]["directional"])
        self.assertEqual(result["mechanism_candidates"], [])

    def test_rate_above_expectation_is_hawkish_candidate(self) -> None:
        result = interpret_decision(official("hike", 50, 4.25), calendar("4.00%"), NOW)
        self.assertEqual(result["decision_surprise"]["classification"], "hawkish_surprise")
        self.assertEqual(result["decision_surprise"]["difference_bps"], 25)
        self.assertEqual(result["mechanism_candidates"][0]["expected"]["gold"], "bearish")

    def test_rate_below_expectation_is_dovish_candidate(self) -> None:
        result = interpret_decision(official("hold", 0, 4.0), calendar("4.25%"), NOW)
        self.assertEqual(result["decision_surprise"]["classification"], "dovish_surprise")
        self.assertEqual(result["decision_surprise"]["difference_bps"], -25)
        self.assertEqual(result["mechanism_candidates"][0]["expected"]["gold"], "bullish")

    def test_missing_expectation_does_not_invent_direction(self) -> None:
        result = interpret_decision(official(), {"events": []}, NOW)
        self.assertEqual(result["decision_surprise"]["classification"], "expectation_missing")
        self.assertFalse(result["decision_surprise"]["directional"])
        self.assertEqual(result["mechanism_candidates"], [])

    def test_minutes_cannot_supply_rate_expectation(self) -> None:
        result = interpret_decision(official(), calendar("4.00%", "FOMC Meeting Minutes"), NOW)
        self.assertEqual(result["decision_surprise"]["classification"], "expectation_missing")

    def test_missing_sep_timepoint_rejects_official_package(self) -> None:
        payload = official()
        payload["timepoints"].pop("sep")
        result = interpret_decision(payload, calendar("4.00%"), NOW)
        self.assertEqual(result["decision_surprise"]["classification"], "unavailable")
        self.assertFalse(result["official_decision"]["verified"])

    def test_interpretation_never_grants_trading_permissions(self) -> None:
        result = interpret_decision(official("hike", 50, 4.25), calendar("4.00%"), NOW)
        self.assertFalse(any(result["permissions"].values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
