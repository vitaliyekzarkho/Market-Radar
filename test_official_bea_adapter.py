from __future__ import annotations

import unittest
from datetime import datetime, timezone

from official_bea_adapter import discover_release_urls, parse_release
from official_event_matcher import enrich_events, expected_report_period


NOW = datetime(2026, 10, 9, 13, 0, tzinfo=timezone.utc)
PIO_URL = "https://www.bea.gov/news/2026/personal-income-and-outlays-august-2026"
GDP_URL = "https://www.bea.gov/news/2026/gdp-third-estimate-2nd-quarter-2026"
HEADER = "EMBARGOED UNTIL RELEASE AT 8:30 a.m. EDT, Wednesday, September 30, 2026 "


class OfficialBeaAdapterTests(unittest.TestCase):
    def test_discovers_exactly_one_release_of_each_family(self) -> None:
        index = '''<a href="/news/2026/personal-income-and-outlays-august-2026">Personal Income and Outlays, August 2026</a>
        <a href="/news/2026/gdp-third-estimate-2nd">GDP (Third Estimate), Industries, 2nd Quarter 2026</a>'''
        urls = discover_release_urls(index)
        self.assertEqual(urls["personal_outlays"], PIO_URL)
        self.assertTrue(urls["gdp"].endswith("/gdp-third-estimate-2nd"))

    def test_personal_outlays_release_yields_six_distinct_facts(self) -> None:
        document = HEADER + """Personal Income and Outlays, August 2026
        Personal income increased $66.6 billion (0.2 percent at a monthly rate) in August.
        Personal consumption expenditures (PCE) increased $190.8 billion (0.9 percent).
        From the preceding month, the PCE price index for August increased 0.3 percent. Excluding food and energy, the PCE price index increased 0.2 percent.
        From the same month one year ago, the PCE price index for August increased 3.4 percent. Excluding food and energy, the PCE price index increased 3.0 percent from one year ago."""
        facts = {item["indicator"]: item for item in parse_release(document, "personal_outlays", PIO_URL, NOW)}
        self.assertEqual({key: value["actual"] for key, value in facts.items()}, {
            "personal_income_mom": "0.2%", "personal_spending_mom": "0.9%",
            "pce_price_index_mom": "0.3%", "core_pce_price_index_mom": "0.2%",
            "pce_price_index_yoy": "3.4%", "core_pce_price_index_yoy": "3%",
        })
        self.assertTrue(all(item["provenance"]["fact_permission"] for item in facts.values()))

    def test_gdp_fact_preserves_quarter_and_estimate_stage(self) -> None:
        document = HEADER + """GDP (Third Estimate), Industries, Corporate Profits, 2nd Quarter 2026
        Real gross domestic product (GDP) increased at an annual rate of 2.2 percent in the second quarter of 2026,
        according to the third estimate released today by the U.S. Bureau of Economic Analysis."""
        fact = parse_release(document, "gdp", GDP_URL, NOW)[0]
        self.assertEqual(fact["actual"], "2.2%")
        self.assertEqual(fact["report_period"], "2026-Q2")
        self.assertEqual(fact["estimate_stage"], "third")
        self.assertTrue(fact["fact_id"].endswith(":third"))

    def test_missing_embargo_timestamp_rejects_release(self) -> None:
        document = "Personal Income and Outlays, August 2026 Personal income increased (0.2 percent at a monthly rate)."
        self.assertEqual(parse_release(document, "personal_outlays", PIO_URL, NOW), [])

    def test_gdp_calendar_requires_matching_estimate_stage(self) -> None:
        document = HEADER + """GDP (Third Estimate), 2nd Quarter 2026
        Real gross domestic product (GDP) increased at an annual rate of 2.2 percent."""
        facts = parse_release(document, "gdp", GDP_URL, NOW)
        calendar = {"events": [{
            "title": "Advance GDP q/q", "event_time_utc": "2026-09-30T12:30:00+00:00",
            "currencies": ["USD"], "status": "scheduled", "actual": None,
        }]}
        result = enrich_events(calendar, [{"facts": facts}], NOW)
        self.assertIsNone(result["events"][0]["actual"])
        self.assertEqual(result["official_match_audit"][0]["status"], "NO_MATCH")

    def test_quarter_period_mapping_matches_release_cycle(self) -> None:
        self.assertEqual(expected_report_period(datetime(2026, 9, 30, tzinfo=timezone.utc), "real_gdp_qoq_annualized"), "2026-Q2")
        self.assertEqual(expected_report_period(datetime(2026, 10, 29, tzinfo=timezone.utc), "real_gdp_qoq_annualized"), "2026-Q3")
        self.assertEqual(expected_report_period(datetime(2027, 1, 28, tzinfo=timezone.utc), "real_gdp_qoq_annualized"), "2026-Q4")


if __name__ == "__main__":
    unittest.main(verbosity=2)
