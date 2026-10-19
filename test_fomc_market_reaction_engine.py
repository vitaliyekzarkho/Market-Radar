from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from fomc_market_reaction_engine import (
    build_market_reaction, channel_materiality, mt5_rows, without_synthetic_volume,
)
from fomc_phase_engine import build_phase_plan
from test_fomc_phase_engine import FACTS, INTERPRETATION


START = datetime(2026, 9, 16, 16, 0, tzinfo=timezone.utc)


def candles(direction: int, step: float = 0.10) -> list[dict]:
    price = 100.0
    rows = []
    for index in range(32):
        opened = START + timedelta(minutes=15 * index)
        close = price + direction * step
        rows.append({
            "utc_time": opened.isoformat(), "open": price, "high": max(price, close) + 0.02,
            "low": min(price, close) - 0.02, "close": close, "tick_volume": 100 + index,
            "is_closed": True,
        })
        price = close
    return rows


def assets(include_rates: bool = True) -> dict:
    result = {
        "gold": {"symbol": "GOLD.pro", "source": "test", "candles": candles(-1)},
        "usd": {"symbol": "USD_BREADTH", "source": "test", "candles": candles(1)},
    }
    if include_rates:
        result["rates_us10y"] = {"symbol": "US10Y", "source": "test", "channel": "nominal_yields", "candles": candles(1)}
    return result


class FomcMarketReactionEngineTests(unittest.TestCase):
    def test_mt5_epoch_remains_utc_and_is_not_shifted_by_broker_offset(self) -> None:
        epoch = int(datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc).timestamp())
        rows = mt5_rows([{"time": epoch, "open": 1, "high": 2, "low": 1, "close": 2, "tick_volume": 10}], 2)
        self.assertEqual(rows[0]["utc_time"], "2026-09-16T18:00:00+00:00")

    def test_synthetic_usd_never_exposes_fabricated_volume(self) -> None:
        source = [{"utc_time": START.isoformat(), "close": 101.0, "tick_volume": 1.0, "volume": 1.0}]
        cleaned = without_synthetic_volume(source)
        self.assertIsNone(cleaned[0]["tick_volume"])
        self.assertIsNone(cleaned[0]["volume"])
        self.assertEqual(source[0]["tick_volume"], 1.0)

    def test_synthetic_usd_requires_absolute_move_as_well_as_relative_atr(self) -> None:
        metrics = {
            "complete": True, "move_percent": 0.0229,
            "displacement_ratio_to_pre_atr": 6.48,
        }
        result = channel_materiality({"symbol": "USD_BREADTH"}, metrics)
        self.assertTrue(result["atr_gate_passed"])
        self.assertFalse(result["absolute_gate_passed"])
        self.assertFalse(result["material"])
        self.assertEqual(result["reason"], "synthetic_usd_below_absolute_noise_floor")

    def test_large_synthetic_usd_move_passes_both_materiality_gates(self) -> None:
        metrics = {
            "complete": True, "move_percent": 0.6006,
            "displacement_ratio_to_pre_atr": 169.74,
        }
        result = channel_materiality({"symbol": "USD_BREADTH"}, metrics)
        self.assertTrue(result["atr_gate_passed"])
        self.assertTrue(result["absolute_gate_passed"])
        self.assertTrue(result["material"])

    def test_direct_dxy_does_not_use_synthetic_absolute_floor(self) -> None:
        metrics = {
            "complete": True, "move_percent": 0.0229,
            "displacement_ratio_to_pre_atr": 6.48,
        }
        result = channel_materiality({"symbol": "DXY"}, metrics)
        self.assertTrue(result["material"])
        self.assertIsNone(result["absolute_floor_percent"])

    def test_clean_windows_measure_expected_and_observed_separately(self) -> None:
        result = build_market_reaction(build_phase_plan(FACTS, INTERPRETATION), assets())
        first = result["phases"][0]["windows"][0]
        self.assertEqual(first["observations"]["gold"]["hypothesized"], "bearish")
        self.assertEqual(first["observations"]["gold"]["observed"], "bearish")
        self.assertEqual(first["observations"]["usd"]["hypothesized"], "bullish")
        self.assertEqual(first["observations"]["usd"]["observed"], "bullish")
        self.assertEqual(first["alignment"], "market_aligned_candidate")

    def test_18_00_hour_window_is_never_measured(self) -> None:
        result = build_market_reaction(build_phase_plan(FACTS, INTERPRETATION), assets())
        window = result["phases"][0]["windows"][2]
        self.assertEqual(window["status"], "excluded_contaminated_window")
        self.assertEqual(window["contaminated_by"], "press_conference")

    def test_missing_yields_are_explicit_and_usd_can_supply_transmission_candidate(self) -> None:
        result = build_market_reaction(build_phase_plan(FACTS, INTERPRETATION), assets(False))
        window = result["phases"][0]["windows"][0]
        yields = window["observations"]["nominal_yields"]
        self.assertFalse(yields["available"])
        self.assertEqual(yields["reason"], "BROKER_INTRADAY_YIELDS_UNAVAILABLE")
        self.assertEqual(window["alignment"], "market_aligned_candidate")

    def test_aligned_but_immaterial_move_is_not_partially_confirmed(self) -> None:
        weak = assets(False)
        weak["gold"]["candles"] = candles(-1, step=0.001)
        weak["usd"]["candles"] = candles(1, step=0.001)
        result = build_market_reaction(build_phase_plan(FACTS, INTERPRETATION), weak)
        window = result["phases"][0]["windows"][0]
        self.assertEqual(window["alignment"], "directionally_aligned_but_immaterial")
        self.assertFalse(window["confirmation_eligible_next_gate"])

    def test_material_gold_conflict_blocks_alignment(self) -> None:
        conflicting = assets()
        conflicting["gold"]["candles"] = candles(1)
        result = build_market_reaction(build_phase_plan(FACTS, INTERPRETATION), conflicting)
        window = result["phases"][0]["windows"][0]
        self.assertEqual(window["alignment"], "contradicted")
        self.assertIn("gold", window["hard_conflicts"])

    def test_market_measurement_never_grants_permissions(self) -> None:
        result = build_market_reaction(build_phase_plan(FACTS, INTERPRETATION), assets())
        self.assertFalse(any(result["permissions"].values()))
        for phase in result["phases"]:
            for window in phase["windows"]:
                self.assertFalse(any(window["permissions"].values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
