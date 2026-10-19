from __future__ import annotations

import unittest
from datetime import datetime, timezone

from official_bls_adapter import parse_release, ppi_table_components
from official_event_matcher import enrich_events


NOW = datetime(2026, 10, 9, 13, 0, tzinfo=timezone.utc)
URL = "https://www.bls.gov/news.release/test.nr0.htm"
HEADER = "Transmission of material in this release is embargoed until 8:30 a.m. (ET) Friday, October 9, 2026. "


class OfficialBlsAdapterTests(unittest.TestCase):
    def test_cpi_release_yields_separate_headline_and_core_facts(self) -> None:
        document = HEADER + """CONSUMER PRICE INDEX - SEPTEMBER 2026
        The Consumer Price Index for All Urban Consumers (CPI-U) increased 0.3 percent on a seasonally adjusted basis in September.
        Over the last 12 months, the all items index increased 2.8 percent.
        The index for all items less food and energy rose 0.2 percent in September.
        The index for all items less food and energy increased 3.0 percent over the last 12 months."""
        facts = {item["indicator"]: item for item in parse_release(document, "cpi", URL, NOW)}
        self.assertEqual(facts["cpi_mom"]["actual"], "0.3%")
        self.assertEqual(facts["cpi_yoy"]["actual"], "2.8%")
        self.assertEqual(facts["core_cpi_mom"]["actual"], "0.2%")
        self.assertEqual(facts["core_cpi_yoy"]["actual"], "3%")
        self.assertEqual(facts["cpi_mom"]["published_at_utc"], "2026-10-09T12:30:00+00:00")
        self.assertTrue(facts["cpi_mom"]["provenance"]["fact_permission"])

    def test_ppi_release_preserves_negative_sign(self) -> None:
        document = HEADER + """PRODUCER PRICE INDEX - SEPTEMBER 2026
        The Producer Price Index for final demand decreased 0.1 percent in September.
        Final demand prices advanced 2.4 percent for 12 months.
        Prices for final demand less foods and energy increased 0.2 percent in September.
        Prices for final demand less foods and energy advanced 2.6 percent for 12 months."""
        facts = {item["indicator"]: item["actual"] for item in parse_release(document, "ppi", URL, NOW)}
        self.assertEqual(facts["ppi_mom"], "-0.1%")
        self.assertEqual(facts["ppi_yoy"], "2.4%")
        self.assertEqual(facts["core_ppi_mom"], "0.2%")

    def test_real_ppi_wording_and_official_table_core_identity(self) -> None:
        document = HEADER.replace("October 9", "September 10") + """PRODUCER PRICE INDEXES - AUGUST 2026
        The Producer Price Index for final demand moved up 0.4 percent in August, seasonally adjusted.
        On an unadjusted basis, the index for final demand increased 5.4 percent for the 12 months ended in August."""
        table = """<table><tr><th>Final demand less foods and energy (5)</th><td>FD</td><td>49104</td>
        <td>89.487</td><td>4.6</td><td>0.7</td><td>0.0</td><td>0.4</td><td>0.3</td><td>0.2</td></tr></table>"""
        facts = {item["indicator"]: item["actual"] for item in parse_release(document, "ppi", URL, NOW, table)}
        self.assertEqual(facts, {"ppi_mom": "0.4%", "ppi_yoy": "5.4%", "core_ppi_mom": "0.2%", "core_ppi_yoy": "4.6%"})

    def test_core_ppi_rejects_wrong_official_series_identity(self) -> None:
        wrong = """<table><tr><th>Final demand less foods and energy</th><td>FD</td><td>49116</td>
        <td>89.487</td><td>4.7</td><td>0.5</td><td>0.8</td><td>0.2</td><td>0.4</td><td>0.3</td></tr></table>"""
        self.assertEqual(ppi_table_components(wrong), {})

    def test_real_employment_changed_little_wording(self) -> None:
        document = HEADER.replace("material in this release", "material in this news release").replace("October 9", "October 2") + """THE EMPLOYMENT SITUATION - SEPTEMBER 2026
        Both nonfarm payroll employment (+29,000) and the unemployment rate (4.2 percent) changed little in September.
        The unemployment rate, at 4.2 percent, changed little in September.
        Total nonfarm payroll employment changed little in September (+29,000).
        Average hourly earnings for all employees on private nonfarm payrolls edged up by 5 cents, or 0.1 percent."""
        facts = {item["indicator"]: item["actual"] for item in parse_release(document, "employment", URL, NOW)}
        self.assertEqual(facts["nonfarm_payrolls"], "29K")
        self.assertEqual(facts["unemployment_rate"], "4.2%")
        self.assertEqual(facts["average_hourly_earnings_mom"], "0.1%")

    def test_employment_release_yields_three_distinct_facts(self) -> None:
        document = HEADER + """THE EMPLOYMENT SITUATION - SEPTEMBER 2026
        Total nonfarm payroll employment rose by 180,000 in September, and the unemployment rate changed little at 4.2 percent.
        In September, average hourly earnings for all employees on private nonfarm payrolls rose by 8 cents, or 0.2 percent, to $36.00."""
        facts = {item["indicator"]: item["actual"] for item in parse_release(document, "employment", URL, NOW)}
        self.assertEqual(facts["nonfarm_payrolls"], "180K")
        self.assertEqual(facts["unemployment_rate"], "4.2%")
        self.assertEqual(facts["average_hourly_earnings_mom"], "0.2%")

    def test_missing_official_timestamp_rejects_entire_release(self) -> None:
        document = "CONSUMER PRICE INDEX - SEPTEMBER 2026 CPI-U increased 0.3 percent on a seasonally adjusted basis."
        self.assertEqual(parse_release(document, "cpi", URL, NOW), [])

    def test_calendar_core_cpi_matches_only_core_fact(self) -> None:
        document = HEADER + """CONSUMER PRICE INDEX - SEPTEMBER 2026
        CPI-U increased 0.3 percent on a seasonally adjusted basis in September.
        The index for all items less food and energy rose 0.2 percent in September."""
        facts = parse_release(document, "cpi", URL, NOW)
        calendar = {"events": [{
            "title": "Core CPI m/m", "event_time_utc": "2026-10-09T12:30:00+00:00",
            "currencies": ["USD"], "status": "scheduled", "actual": None, "forecast": "0.3%",
        }]}
        event = enrich_events(calendar, [{"facts": facts}], NOW)["events"][0]
        self.assertEqual(event["actual"], "0.2%")
        self.assertEqual(event["official_fact"]["indicator"], "core_cpi_mom")
        self.assertEqual(event["fact_timing_basis"], "official_timestamp")


if __name__ == "__main__":
    unittest.main(verbosity=2)
