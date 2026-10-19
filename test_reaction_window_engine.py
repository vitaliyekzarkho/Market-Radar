from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from reaction_window_engine import build_reaction_report, normalize_candles


EVENT = datetime(2026, 6, 12, 12, 32, tzinfo=timezone.utc)


def raw_candles(start: datetime, count: int, minutes: int = 5) -> list[dict]:
    rows = []
    price = 100.0
    for index in range(count):
        opened = start + timedelta(minutes=minutes * index)
        close = price + 0.1
        rows.append({
            "utc_time": opened.isoformat(), "open": price, "high": close + 0.05,
            "low": price - 0.05, "close": close, "tick_volume": 100 + index,
            "is_closed": True,
        })
        price = close
    return rows


def report(as_of: datetime, timeframe: str = "M5") -> dict:
    rows = raw_candles(EVENT - timedelta(hours=25, minutes=2), 350, 5)
    return build_reaction_report(
        {"title": "Trump says conflict will end soon", "event_time_utc": EVENT.isoformat(), "source": "White House"},
        {"gold": {"symbol": "GOLD.pro", "source": "test", "timeframe": timeframe, "candles": rows}},
        as_of,
    )["assets"]["gold"]


class ReactionWindowEngineTests(unittest.TestCase):
    def test_event_inside_bar_starts_reaction_at_next_clean_bar(self) -> None:
        result = report(EVENT + timedelta(hours=1))
        self.assertEqual(result["reaction_start_utc"], "2026-06-12T12:35:00+00:00")
        self.assertEqual(result["measurement_delay_seconds"], 180)

    def test_future_bars_are_not_visible_in_historical_as_of(self) -> None:
        result = report(EVENT + timedelta(minutes=20))
        post60 = result["post_event"]["post_60m"]
        self.assertEqual(post60["status"], "developing")
        self.assertLessEqual(post60["last_bar_close_utc"], (EVENT + timedelta(minutes=20)).isoformat())

    def test_completed_window_requires_clock_and_data_coverage(self) -> None:
        result = report(EVENT + timedelta(hours=2))
        self.assertEqual(result["post_event"]["post_60m"]["status"], "complete")
        self.assertTrue(result["post_event"]["post_60m"]["complete"])
        self.assertLess(result["post_event"]["post_60m"]["coverage_gap_seconds"], 300)
        self.assertEqual(result["post_event"]["post_4h"]["status"], "developing")

    def test_pre_event_windows_measure_move_before_headline(self) -> None:
        result = report(EVENT + timedelta(hours=1))
        pre60 = result["pre_event"]["pre_60m"]
        self.assertTrue(pre60["available"])
        self.assertGreater(pre60["move_percent"], 0)

    def test_post_window_uses_last_pre_event_close_as_anchor(self) -> None:
        result = report(EVENT + timedelta(hours=1))
        anchor = result["anchor"]["price"]
        post15 = result["post_event"]["post_15m"]
        self.assertEqual(post15["start_price"], anchor)

    def test_volume_and_displacement_are_measurements_not_causal_labels(self) -> None:
        result = report(EVENT + timedelta(hours=1))
        post15 = result["post_event"]["post_15m"]
        self.assertIsNotNone(post15["volume_ratio_to_pre60"])
        self.assertIsNotNone(post15["displacement_ratio_to_pre_atr"])
        self.assertFalse(result["permissions"]["causal_interpretation"])
        self.assertFalse(result["permissions"]["entry"])

    def test_unclosed_bar_is_excluded(self) -> None:
        rows = raw_candles(EVENT - timedelta(minutes=10), 3)
        rows[-1]["is_closed"] = False
        normalized = normalize_candles(rows, "M5")
        self.assertEqual(len(normalized), 2)

    def test_m15_fallback_reports_medium_precision(self) -> None:
        rows = raw_candles(EVENT - timedelta(hours=2, minutes=2), 20, 15)
        result = build_reaction_report(
            {"title": "Event", "event_time_utc": EVENT.isoformat()},
            {"gold": {"timeframe": "M15", "candles": rows}},
            EVENT + timedelta(hours=2),
        )["assets"]["gold"]
        self.assertEqual(result["precision"], "medium")
        self.assertGreaterEqual(result["measurement_delay_seconds"], 0)

    def test_h1_fallback_is_explicitly_coarse(self) -> None:
        rows = raw_candles(EVENT - timedelta(hours=6, minutes=32), 10, 60)
        result = build_reaction_report(
            {"title": "Event", "event_time_utc": EVENT.isoformat()},
            {"oil": {"timeframe": "H1", "candles": rows}},
            EVENT + timedelta(hours=5),
        )["assets"]["oil"]
        self.assertEqual(result["precision"], "coarse")

    def test_report_identity_is_stable(self) -> None:
        first = build_reaction_report(
            {"title": "Event", "event_time_utc": EVENT.isoformat(), "source": "Source"}, {}, EVENT,
        )
        second = build_reaction_report(
            {"title": "Event", "event_time_utc": EVENT.isoformat(), "source": "Source"}, {}, EVENT,
        )
        self.assertEqual(first["event_id"], second["event_id"])

    def test_report_itself_cannot_claim_causality(self) -> None:
        result = build_reaction_report(
            {"title": "Event", "event_time_utc": EVENT.isoformat()}, {}, EVENT,
        )
        self.assertFalse(result["causal_interpretation_allowed"])

    def test_rollover_window_starts_at_first_tradable_bar(self) -> None:
        before = raw_candles(EVENT - timedelta(hours=2, minutes=2), 20)
        resumed = raw_candles(EVENT + timedelta(minutes=58), 30)
        result = build_reaction_report(
            {"title": "Rollover event", "event_time_utc": EVENT.isoformat()},
            {"gold": {"timeframe": "M5", "candles": before + resumed}},
            EVENT + timedelta(hours=4),
        )["assets"]["gold"]
        self.assertTrue(result["market_closed_at_event"])
        self.assertEqual(result["session_delay_class"], "rollover_or_session_break")
        self.assertEqual(result["post_event"]["post_60m"]["window_start_utc"], result["reaction_start_utc"])
        self.assertTrue(result["post_event"]["post_60m"]["complete"])

    def test_weekend_event_records_extended_closure_and_opening_gap(self) -> None:
        friday = raw_candles(EVENT - timedelta(hours=2, minutes=2), 20)
        sunday = raw_candles(EVENT + timedelta(hours=40, minutes=3), 30)
        for item in sunday:
            for key in ("open", "high", "low", "close"):
                item[key] += 3.0
        result = build_reaction_report(
            {"title": "Weekend event", "event_time_utc": EVENT.isoformat()},
            {"gold": {"timeframe": "M5", "candles": friday + sunday}},
            EVENT + timedelta(hours=44),
        )["assets"]["gold"]
        self.assertEqual(result["session_delay_class"], "weekend_or_extended_closure")
        self.assertGreater(result["opening_gap_percent"], 0)
        self.assertTrue(result["post_event"]["post_60m"]["complete"])

    def test_no_tradable_bar_yet_keeps_window_pending(self) -> None:
        before = raw_candles(EVENT - timedelta(hours=2, minutes=2), 20)
        result = build_reaction_report(
            {"title": "Closed market event", "event_time_utc": EVENT.isoformat()},
            {"gold": {"timeframe": "M5", "candles": before}},
            EVENT + timedelta(hours=2),
        )["assets"]["gold"]
        self.assertEqual(result["session_delay_class"], "no_tradable_data_yet")
        self.assertEqual(result["post_event"]["post_60m"]["status"], "pending")


if __name__ == "__main__":
    unittest.main(verbosity=2)
