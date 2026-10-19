from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from market_data_quality import bar_diagnostics, broker_utc_offset_hours, cache_freshness, daily_series_freshness, market_bar_freshness
from oil_context_engine import choose_symbol as choose_oil_symbol, observation_from_rows as oil_observation
from rates_context_engine import aggregate_observations, cached_context as cached_rates, choose_rate_symbols, daily_rate_structure, observation_from_rows as rate_observation, parse_fred_csv


NOW = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)


class NumericTimestamp:
    def __init__(self, value: float) -> None:
        self.value = value

    def __float__(self) -> float:
        return self.value


def rows(last_time: datetime, start: float, step: float) -> list[dict]:
    return [
        {"time": int((last_time - timedelta(hours=24-index)).timestamp()), "close": start + step * index}
        for index in range(25)
    ]


class MarketDataQualityTests(unittest.TestCase):
    def test_closed_h1_bar_freshness(self) -> None:
        result = market_bar_freshness((NOW - timedelta(hours=1)).timestamp(), NOW)
        self.assertEqual(result["status"], "fresh")
        self.assertTrue(result["usable"])

    def test_numpy_like_numeric_timestamp_is_supported(self) -> None:
        value = NumericTimestamp((NOW - timedelta(hours=1)).timestamp())
        result = market_bar_freshness(value, NOW)
        self.assertEqual(result["status"], "fresh")
        self.assertTrue(result["usable"])

    def test_stale_h1_bar_is_rejected(self) -> None:
        result = market_bar_freshness((NOW - timedelta(hours=8)).timestamp(), NOW)
        self.assertEqual(result["status"], "stale")
        self.assertFalse(result["usable"])

    def test_future_bar_is_rejected_instead_of_masked_as_zero_age(self) -> None:
        result = market_bar_freshness((NOW + timedelta(minutes=48)).timestamp(), NOW)
        self.assertEqual(result["status"], "future")
        self.assertFalse(result["usable"])

    def test_broker_offset_is_detected_from_tick_clock(self) -> None:
        broker_tick = (NOW + timedelta(hours=1)).timestamp()
        self.assertEqual(broker_utc_offset_hours(broker_tick, NOW), 1)

    def test_daily_context_covers_normal_weekend_but_is_not_intraday(self) -> None:
        result = daily_series_freshness((NOW - timedelta(days=3)).isoformat(), NOW)
        self.assertEqual(result["status"], "recent")
        self.assertTrue(result["usable"])

    def test_friday_daily_observation_is_recent_on_tuesday(self) -> None:
        friday = datetime(2026, 10, 2, 0, 0, tzinfo=timezone.utc)
        result = daily_series_freshness(friday, NOW)
        self.assertEqual(result["status"], "recent")
        self.assertTrue(result["usable"])

    def test_cache_expires_after_three_hours(self) -> None:
        self.assertTrue(cache_freshness((NOW - timedelta(hours=2)).isoformat(), NOW)["usable"])
        self.assertFalse(cache_freshness((NOW - timedelta(hours=4)).isoformat(), NOW)["usable"])

    def test_rate_aliases_find_broker_suffix_symbols(self) -> None:
        symbols = [SimpleNamespace(name="US10Y.cash", description="US Treasury 10Y Yield"), SimpleNamespace(name="UST2Y.pro", description="Treasury 2Y Yield")]
        selected = choose_rate_symbols(symbols)
        self.assertEqual(selected["US10Y"], "US10Y.cash")
        self.assertEqual(selected["US2Y"], "UST2Y.pro")

    def test_treasury_price_without_yield_label_is_not_misread_as_yield(self) -> None:
        symbols = [SimpleNamespace(name="US10.cash", description="US Treasury Note CFD")]
        self.assertEqual(choose_rate_symbols(symbols), {})

    def test_rate_observation_includes_bps_time_and_permission(self) -> None:
        observation = rate_observation("US10Y", "US10Y.cash", rows(NOW - timedelta(hours=1), 5.0, 0.002), NOW)
        self.assertIsNotNone(observation)
        self.assertIn("move_4h_bps", observation)
        self.assertEqual(observation["causal_permission"], "reaction_evidence")
        self.assertEqual(observation["freshness"]["status"], "fresh")

    def test_stale_rate_observation_is_not_returned(self) -> None:
        self.assertIsNone(rate_observation("US10Y", "US10Y", rows(NOW - timedelta(hours=9), 5.0, 0.002), NOW))

    def test_unordered_broker_rows_are_sorted_before_analysis(self) -> None:
        unordered = list(reversed(rows(NOW - timedelta(hours=1), 5.0, 0.002)))
        observation = rate_observation("US10Y", "US10Y", unordered, NOW)
        self.assertIsNotNone(observation)
        self.assertEqual(observation["freshness"]["status"], "fresh")

    def test_bar_diagnostics_reports_actual_newest_bar(self) -> None:
        unordered = list(reversed(rows(NOW - timedelta(hours=1), 100.0, -0.1)))
        diagnostics = bar_diagnostics(unordered, NOW)
        self.assertEqual(diagnostics["bar_count"], 25)
        self.assertEqual(diagnostics["freshness"]["status"], "fresh")

    def test_fred_parser_skips_missing_observations(self) -> None:
        data = b"observation_date,DGS10\n2026-10-01,4.10\n2026-10-02,.\n2026-10-05,4.25\n"
        parsed = parse_fred_csv(data, "DGS10")
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[-1][1], 4.25)

    def test_daily_fallback_is_explicitly_not_reaction_capable(self) -> None:
        context = aggregate_observations({"US10Y": {"normalized_4h": 1.0}}, "official_daily_fallback")
        self.assertFalse(context["intraday_reaction_capable"])

    def test_curve_and_real_yield_are_kept_separate_from_average_direction(self) -> None:
        structure = daily_rate_structure({
            "US2Y": {"value_percent": 4.83, "move_4d_bps": -9.0},
            "US10Y": {"value_percent": 5.28, "move_4d_bps": 4.0},
            "US10Y_REAL": {"value_percent": 2.92, "move_4d_bps": 2.0},
        })
        self.assertEqual(structure["yield_curve"]["spread_2s10s_bps"], 45.0)
        self.assertEqual(structure["yield_curve"]["dynamic"], "steepening")
        self.assertEqual(structure["gold_rate_channel"]["direction"], "mixed")
        self.assertEqual(structure["gold_rate_channel"]["causal_permission"], "context_only")

    def test_daily_cache_has_longer_ttl_but_never_reaction_permission(self) -> None:
        cache = {
            "generated_at_utc": (NOW - timedelta(hours=5)).isoformat(),
            "context": {"available": True, "direction": "rising", "data_mode": "official_daily_fallback"},
        }
        context = cached_rates(cache, NOW)
        self.assertIsNotNone(context)
        self.assertEqual(context["data_mode"], "official_daily_cache")
        self.assertFalse(context["intraday_reaction_capable"])

    def test_oil_alias_prefers_brent(self) -> None:
        symbols = [SimpleNamespace(name="USOIL.cash", description="WTI Oil"), SimpleNamespace(name="UKOIL.pro", description="Brent Crude")]
        self.assertEqual(choose_oil_symbol(symbols), "UKOIL.pro")

    def test_oil_observation_has_closed_bar_and_reaction_permission(self) -> None:
        observation = oil_observation("UKOIL.pro", rows(NOW - timedelta(hours=1), 100.0, -0.1), NOW)
        self.assertIsNotNone(observation)
        self.assertEqual(observation["direction"], "falling")
        self.assertEqual(observation["causal_permission"], "reaction_evidence")

    def test_broker_hour_is_converted_to_true_utc(self) -> None:
        broker_rows = rows(NOW, 100.0, -0.1)
        observation = oil_observation("OILBRNT.pro", broker_rows, NOW, broker_offset=1)
        self.assertIsNotNone(observation)
        self.assertEqual(observation["last_closed_bar_utc"], (NOW - timedelta(hours=1)).isoformat())
        self.assertEqual(observation["broker_utc_offset_hours"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
