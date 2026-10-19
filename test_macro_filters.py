from __future__ import annotations

import unittest
from datetime import datetime, timezone

from cot_context_adapter import parse_report
from official_macro_adapter import parse_feed
from rates_context_engine import normalized_move
from usd_breadth_engine import aggregate_components, contaminated_pairs


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


class MacroFilterTests(unittest.TestCase):
    def test_eur_event_contaminates_only_eurusd(self) -> None:
        events = [{
            "title": "ECB President Speaks", "importance": "high",
            "currencies": ["EUR"], "event_time_utc": "2026-09-21T12:15:00+00:00",
        }]
        blocked = contaminated_pairs(events, NOW)
        self.assertEqual(set(blocked), {"EURUSD"})

    def test_usd_event_does_not_remove_components(self) -> None:
        events = [{
            "title": "FOMC", "importance": "high", "currencies": ["USD"],
            "event_time_utc": "2026-09-21T12:00:00+00:00",
        }]
        self.assertEqual(contaminated_pairs(events, NOW), {})

    def test_contaminated_component_is_excluded(self) -> None:
        components = [{"pair": "EURUSD", "contribution": 2.0}, {"pair": "GBPUSD", "contribution": 1.0}, {"pair": "AUDUSD", "contribution": 1.0}, {"pair": "USDCAD", "contribution": 1.0}]
        result = aggregate_components(components, {"EURUSD": {"event": "ECB"}})
        self.assertEqual(result["agreement"]["total"], 3)
        self.assertEqual(len(result["excluded_components"]), 1)

    def test_fomc_feed_parser_keeps_relevant_release(self) -> None:
        xml = b"<rss><channel><item><title>Federal Open Market Committee statement</title><link>https://federalreserve.gov/test</link><pubDate>Mon, 21 Sep 2026 10:00:00 GMT</pubDate></item></channel></rss>"
        releases = parse_feed("FOMC", xml, NOW)
        self.assertEqual(len(releases), 1)
        self.assertTrue(releases[0]["verified_primary_source"])

    def test_rates_normalization_detects_rising_yield(self) -> None:
        result = normalized_move([4.0 + index * 0.01 for index in range(25)])
        self.assertGreater(result["normalized_4h"], 0)

    def test_cot_parser_extracts_managed_money(self) -> None:
        report = (
            "Market_and_Exchange_Names,Report_Date_as_YYYY-MM-DD,M_Money_Positions_Long_All,M_Money_Positions_Short_All\n"
            "GOLD - COMMODITY EXCHANGE INC.,2026-09-15,200000,50000\n"
        )
        result = parse_report(report)
        self.assertTrue(result["available"])
        self.assertEqual(result["managed_money_net"], 150000)
        self.assertEqual(result["direction"], "bullish")

    def test_headerless_cftc_report_is_supported(self) -> None:
        columns = ["GOLD - COMMODITY EXCHANGE INC.", "260915", "2026-09-15", "088691", "CMX", "01", "088", "409899", "16396", "44457", "14690", "248350", "25331", "142394", "9278"]
        result = parse_report(",".join(columns) + "\n")
        self.assertTrue(result["available"])
        self.assertEqual(result["managed_money_net"], 133116)


if __name__ == "__main__":
    unittest.main(verbosity=2)
