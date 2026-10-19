from __future__ import annotations

import unittest
from datetime import datetime, timezone

from official_event_matcher import enrich_events
from official_fomc_adapter import (
    decision_components, latest_statement, parse_statement,
    press_conference_time, projections_verified, sep_rate_distribution_rows,
    sep_table_rows, split_press_conference, statement_history,
)


NOW = datetime(2026, 10, 9, 13, 0, tzinfo=timezone.utc)
URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm"
STATEMENT = """September 16, 2026 Federal Reserve issues FOMC statement For release at 2:00 p.m. EDT
The Committee decided to raise the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent."""


class OfficialFomcAdapterTests(unittest.TestCase):
    def test_selects_latest_exact_statement_not_minutes(self) -> None:
        feed = """<rss><channel>
        <item><title>Minutes of the Federal Open Market Committee, September 15-16, 2026</title><link>https://fed/minutes</link><pubDate>Wed, 7 Oct 2026 18:00:00 GMT</pubDate></item>
        <item><title>Federal Reserve issues FOMC statement</title><link>https://fed/statement</link><pubDate>Wed, 16 Sep 2026 18:00:00 GMT</pubDate></item>
        </channel></rss>"""
        selected = latest_statement(feed)
        self.assertEqual(selected[0], "https://fed/statement")
        self.assertEqual(selected[1].isoformat(), "2026-09-16T18:00:00+00:00")

    def test_statement_history_keeps_consecutive_decisions(self) -> None:
        feed = """<rss><channel>
        <item><title>Federal Reserve issues FOMC statement</title><link>https://fed/july</link><pubDate>Wed, 29 Jul 2026 18:00:00 GMT</pubDate></item>
        <item><title>Minutes of the Federal Open Market Committee</title><link>https://fed/minutes</link><pubDate>Wed, 7 Oct 2026 18:00:00 GMT</pubDate></item>
        <item><title>Federal Reserve issues FOMC statement</title><link>https://fed/september</link><pubDate>Wed, 16 Sep 2026 18:00:00 GMT</pubDate></item>
        </channel></rss>"""
        self.assertEqual([item[0] for item in statement_history(feed)], ["https://fed/september", "https://fed/july"])

    def test_parses_hike_range_and_derived_previous_range(self) -> None:
        decision = decision_components(STATEMENT)
        self.assertEqual(decision["action"], "hike")
        self.assertEqual(decision["change_bps"], 25)
        self.assertEqual(decision["target_lower_pct"], 3.75)
        self.assertEqual(decision["target_upper_pct"], 4.0)
        self.assertEqual(decision["previous_target_lower_pct"], 3.5)
        self.assertEqual(decision["previous_target_upper_pct"], 3.75)

    def test_parses_cut_and_hold_without_directional_interpretation(self) -> None:
        cut = decision_components("The Committee decided to lower the target range for the federal funds rate by 1/2 percentage point to 3 to 3-1/4 percent.")
        hold = decision_components("The Committee decided to maintain the target range for the federal funds rate at 3 to 3-1/4 percent.")
        self.assertEqual((cut["action"], cut["change_bps"]), ("cut", -50))
        self.assertEqual((hold["action"], hold["change_bps"]), ("hold", 0))

    def test_statement_produces_six_authoritative_facts(self) -> None:
        parsed = parse_statement(STATEMENT, URL, NOW)
        self.assertEqual(parsed["meeting_id"], "2026-09-16")
        self.assertEqual(len(parsed["facts"]), 6)
        facts = {item["indicator"]: item for item in parsed["facts"]}
        self.assertEqual(facts["fomc_target_upper_pct"]["actual"], "4%")
        self.assertEqual(facts["fomc_rate_change_bps"]["actual"], "+25bp")
        self.assertEqual(facts["fomc_previous_target_upper_pct"]["derivation"], "derived_from_official_change_and_new_range")
        self.assertTrue(all(item["provenance"]["fact_permission"] for item in facts.values()))

    def test_sep_and_press_conference_are_separate_verified_timepoints(self) -> None:
        meeting = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
        sep = "September 16, 2026: FOMC Projections materials For release at 2:00 p.m., EDT Summary of Economic Projections"
        calendar = "Time: Release Date(s): 2:30 p.m. FOMC Press Conference 16 2:00 p.m. FOMC Meeting"
        self.assertTrue(projections_verified(sep, meeting))
        self.assertEqual(press_conference_time(calendar, meeting).isoformat(), "2026-09-16T18:30:00+00:00")

    def test_press_conference_keeps_prepared_remarks_and_qa_separate(self) -> None:
        transcript = (
            "CHAIRMAN TEST. Inflation is too high. And, with that, I'll take a few of your questions. "
            "REPORTER. Is policy restrictive? CHAIRMAN TEST. We will evaluate prospectively."
        )
        sections = split_press_conference(transcript)
        self.assertIn("Inflation is too high", sections["prepared_remarks"])
        self.assertNotIn("REPORTER", sections["prepared_remarks"])
        self.assertIn("REPORTER", sections["qa"])

    def test_sep_table_rows_preserve_current_and_prior_projection(self) -> None:
        document = """<table><tr><th>Variable</th><th>Median<sup>1</sup></th></tr>
        <tr><th>2026</th><th>2027</th><th>2028</th><th>2029</th><th>Longer run</th></tr>
        <tr><th>Federal funds rate</th><td>4.1</td><td>4.1</td><td>3.9</td><td>3.6</td><td>3.2</td></tr>
        <tr><th>June projection</th><td>3.8</td><td>3.6</td><td>3.4</td><td></td><td>3.1</td></tr></table>"""
        rows = sep_table_rows(document)
        self.assertEqual(rows[-2][0], "Federal funds rate")
        self.assertEqual(rows[-1][0], "June projection")
        self.assertEqual(rows[-1][1:4], ["3.8", "3.6", "3.4"])

    def test_sep_distribution_rows_select_only_rate_histogram(self) -> None:
        document = """<table><tr><th>Percent Range</th><th>2026</th><th>2027</th><th>2028</th><th>2029</th><th>Longer Run</th></tr>
        <tr><th>June projections</th><th>September projections</th><th>June projections</th><th>September projections</th><th>June projections</th><th>September projections</th><th>September projections</th><th>June projections</th><th>September projections</th></tr>
        <tr><th>2.63- 2.87</th><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
        <tr><th>4.13- 4.37</th><td>5</td><td>12</td><td>2</td><td>6</td><td></td><td>4</td><td></td><td></td><td></td></tr></table>"""
        rows = sep_rate_distribution_rows(document)
        self.assertEqual(rows[0][0], "Percent Range")
        self.assertEqual(rows[-1][0], "4.13- 4.37")

    def test_federal_funds_calendar_matches_upper_bound_only(self) -> None:
        facts = parse_statement(STATEMENT, URL, NOW)["facts"]
        calendar = {"events": [{
            "title": "Federal Funds Rate", "event_time_utc": "2026-09-16T18:00:00+00:00",
            "currencies": ["USD"], "status": "scheduled", "actual": None, "forecast": "4.00%",
        }]}
        event = enrich_events(calendar, [{"facts": facts}], NOW)["events"][0]
        self.assertEqual(event["actual"], "4%")
        self.assertEqual(event["official_fact"]["indicator"], "fomc_target_upper_pct")


if __name__ == "__main__":
    unittest.main(verbosity=2)
