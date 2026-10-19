from __future__ import annotations

import unittest

from market_pulse_engine import build_payload, classify_state


def rows(direction: int = 1, count: int = 60) -> list[dict]:
    output = []
    price = 4300.0
    for index in range(count):
        step = 0.15 * direction if index >= count - 6 else 0.02 * (1 if index % 2 else -1)
        opened = price
        price += step
        output.append({
            "time": str(index), "open": opened, "high": max(opened, price) + 0.04,
            "low": min(opened, price) - 0.04, "close": price,
            "tick_volume": 220.0 if index >= count - 6 else 100.0,
        })
    return output


class MarketPulseTests(unittest.TestCase):
    def test_persistent_bullish_pulse_is_context_only(self) -> None:
        payload = build_payload(rows(1))
        pulse = payload["pulse"]
        self.assertEqual(pulse["direction"], "bullish")
        self.assertIn(pulse["state"], {"building", "persistent"})
        self.assertFalse(pulse["scoring_applied"])
        self.assertFalse(pulse["telegram_eligible"])

    def test_direction_flip_is_failed_state(self) -> None:
        horizons = {
            "5m": {"available": True, "direction": "bearish", "normalized_move": -1.0},
            "15m": {"available": True, "direction": "bearish", "normalized_move": -1.0},
            "30m": {"available": True, "direction": "bearish", "normalized_move": -1.0},
        }
        state, direction = classify_state(horizons, {"direction": "bullish"})
        self.assertEqual(direction, "bearish")
        self.assertEqual(state, "failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
