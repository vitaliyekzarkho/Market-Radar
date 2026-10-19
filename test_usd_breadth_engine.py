from __future__ import annotations

import unittest

from usd_breadth_engine import aggregate_components, choose_symbol, component_from_closes


def rising_closes(step: float = 0.001) -> list[float]:
    return [1.0 + step * index for index in range(25)]


class UsdBreadthEngineTests(unittest.TestCase):
    def test_prefers_pro_symbol(self) -> None:
        self.assertEqual(choose_symbol("EURUSD", ["EURUSD", "EURUSD.pro"]), "EURUSD.pro")

    def test_quote_usd_pair_inverts_rising_move(self) -> None:
        item = component_from_closes("EURUSD", "EURUSD.pro", -1, rising_closes())
        self.assertLess(item["contribution"], 0)

    def test_base_usd_pair_keeps_rising_move(self) -> None:
        item = component_from_closes("USDCAD", "USDCAD.pro", 1, rising_closes())
        self.assertGreater(item["contribution"], 0)

    def test_bullish_breadth_requires_three_components(self) -> None:
        result = aggregate_components([
            {"contribution": 1.2}, {"contribution": 0.8}, {"contribution": 0.5},
        ])
        self.assertTrue(result["available"])
        self.assertEqual(result["direction"], "bullish")
        self.assertEqual(result["agreement"], {"agreeing": 3, "total": 3})

    def test_insufficient_components_is_no_data(self) -> None:
        result = aggregate_components([{"contribution": 1.0}, {"contribution": 0.5}])
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "INSUFFICIENT_COMPONENTS")

    def test_mixed_breadth_has_neutral_direction(self) -> None:
        result = aggregate_components([
            {"contribution": 0.2}, {"contribution": -0.1}, {"contribution": 0.0},
        ])
        self.assertEqual(result["direction"], "mixed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
