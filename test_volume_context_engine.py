from __future__ import annotations

import unittest
from datetime import datetime, timezone

from volume_context_engine import build_payload, classify_timeframe


def sample(last_volume: float = 250.0, last_range: float = 3.0) -> list[dict[str, float]]:
    rows = []
    for _ in range(23):
        rows.append({"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.2, "tick_volume": 100.0})
    rows.append({"open": 100.0, "high": 100.0 + last_range, "low": 100.0, "close": 100.0 + last_range * 0.8, "tick_volume": last_volume})
    rows.append({"open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0, "tick_volume": 1.0})
    return rows


class VolumeContextTests(unittest.TestCase):
    def test_expansion_uses_tick_volume_proxy(self) -> None:
        result = classify_timeframe(sample(), "M15")
        self.assertEqual(result["state"], "expansion")
        self.assertEqual(result["proxy"], "broker_tick_volume")

    def test_volume_never_scores_or_alerts(self) -> None:
        payload = build_payload({name: sample() for name in ("M5", "M15", "H1", "H4")})
        context = payload["volume_context"]
        self.assertFalse(context["scoring_applied"])
        self.assertFalse(context["telegram_eligible"])

    def test_high_effort_small_spread_is_absorption_candidate(self) -> None:
        result = classify_timeframe(sample(last_volume=250.0, last_range=0.5), "H1")
        self.assertEqual(result["state"], "possible_absorption")
        self.assertEqual(result["effort_result"], "effort_without_result")

    def test_sequence_compares_push_with_recent_closed_bars(self) -> None:
        rows = sample()
        for item in rows[-10:-5]:
            item.update(open=100.0, close=101.0, high=101.1, low=99.9, tick_volume=200.0)
        for item in rows[-5:-2]:
            item.update(open=100.0, close=100.2, high=100.3, low=99.9, tick_volume=80.0)
        result = classify_timeframe(rows, "H1")
        self.assertEqual(result["sequence"], "fading_effort")
        self.assertLess(result["participation_change"], 0.75)

    def test_volume_observation_has_stable_timeframe_attribution_id(self) -> None:
        rows = sample()
        rows[-2]["time"] = "2026-09-28T12:00:00+00:00"
        result = classify_timeframe(rows, "H1")
        self.assertEqual(result["closed_bar_time"], "2026-09-28T12:00:00+00:00")
        self.assertEqual(result["observation_id"], "H1:2026-09-28T12:00:00+00:00")

    def test_payload_reads_structural_daily_and_weekly_volume(self) -> None:
        payload = build_payload({"D1": sample(), "W1": sample()})
        self.assertTrue(payload["timeframes"]["D1"]["available"])
        self.assertTrue(payload["timeframes"]["W1"]["available"])

    def test_stale_intraday_closed_bar_is_excluded(self) -> None:
        rows = sample()
        rows[-2]["time"] = "2026-10-05T08:00:00+00:00"
        payload = build_payload(
            {"H1": rows},
            now=datetime(2026, 10, 5, 15, 34, tzinfo=timezone.utc),
        )
        observation = payload["timeframes"]["H1"]
        self.assertFalse(observation["available"])
        self.assertEqual(observation["reason"], "STALE_CLOSED_BAR")
        self.assertEqual(observation["freshness_status"], "stale")

    def test_daily_bar_remains_fresh_across_market_weekend(self) -> None:
        rows = sample()
        rows[-2]["time"] = "2026-10-01T22:00:00+00:00"
        payload = build_payload(
            {"D1": rows},
            now=datetime(2026, 10, 5, 15, 34, tzinfo=timezone.utc),
        )
        self.assertTrue(payload["timeframes"]["D1"]["available"])
        self.assertEqual(payload["timeframes"]["D1"]["freshness_status"], "fresh")


if __name__ == "__main__":
    unittest.main(verbosity=2)
