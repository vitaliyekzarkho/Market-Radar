from __future__ import annotations

import unittest
from datetime import datetime, timezone

from official_ism_adapter import (
    is_verified_ism_syndicated_release, parse_report, report_urls,
    response_fingerprint, syndicated_release_url,
)


NOW = datetime(2026, 10, 5, 14, 5, tzinfo=timezone.utc)
URL = "https://www.ismworld.org/supply-management-news-and-reports/reports/test"


class OfficialIsmAdapterTests(unittest.TestCase):
    def test_uses_explicit_previous_report_month_url(self) -> None:
        urls = report_urls(NOW)
        self.assertTrue(urls["ism_services_pmi"].endswith("/services/september/"))
        self.assertTrue(urls["ism_manufacturing_pmi"].endswith("/pmi/september/"))

    def test_parses_unambiguous_official_services_fact(self) -> None:
        document = '''<html><head><script type="application/ld+json">{"datePublished":"2026-10-05T14:00:00Z"}</script></head>
        <body><h1>September 2026 ISM Services PMI Report</h1><p>The Services PMI registered 54.9 percent.</p></body></html>'''
        fact = parse_report(document, "ism_services_pmi", URL, NOW)
        self.assertEqual(fact["actual"], "54.9")
        self.assertEqual(fact["report_period"], "2026-09")
        self.assertTrue(fact["provenance"]["fact_permission"])

    def test_parses_trademark_split_by_html_tag(self) -> None:
        document = '''<html><body><h1>September 2026 ISM Services PMI Report</h1>
        <p>Services PMI<sup>®</sup> at 54.9%</p></body></html>'''
        fact = parse_report(document, "ism_services_pmi", URL, NOW)
        self.assertEqual(fact["actual"], "54.9")

    def test_preserves_fact_when_official_page_omits_publication_time(self) -> None:
        document = "<h1>September 2026 ISM Services PMI Report</h1><p>Services PMI registered 54.9 percent.</p>"
        fact = parse_report(document, "ism_services_pmi", URL, NOW)
        self.assertEqual(fact["actual"], "54.9")
        self.assertIsNone(fact["published_at_utc"])
        self.assertEqual(fact["publication_time_status"], "not_exposed_by_official_page")

    def test_rejects_ambiguous_values(self) -> None:
        document = '''<script>{"datePublished":"2026-10-05T14:00:00Z"}</script>
        <h1>September 2026</h1><p>Services PMI registered 54.9 percent.</p><p>Services PMI registered 55.1 percent.</p>'''
        self.assertIsNone(parse_report(document, "ism_services_pmi", URL, NOW))

    def test_diagnostic_fingerprint_is_small_visible_and_script_free(self) -> None:
        document = '''<html><head><title>September ISM report</title>
        <script>SECRET_TOKEN = "must-not-leak"; PMI 99.9%</script></head>
        <body><h1>September 2026</h1><p>Services PMI<sup>®</sup> at 54.9%</p></body></html>'''
        diagnostic = response_fingerprint(document, URL, URL + "/final", 200, "text/html; charset=utf-8")
        self.assertEqual(diagnostic["http_status"], 200)
        self.assertEqual(diagnostic["detected_report_periods"], ["2026-09"])
        self.assertTrue(diagnostic["signals"]["contains_pmi"])
        self.assertIn("54.9", " ".join(diagnostic["pmi_snippets"]))
        self.assertNotIn("SECRET_TOKEN", str(diagnostic))
        self.assertNotIn("must-not-leak", str(diagnostic))

    def test_selects_one_matching_syndicated_release(self) -> None:
        search = '''<a href="/news-releases/services-pmi-at-54-9-september-2026-ism-services-pmi-report-123.html">Sep</a>
        <a href="/news-releases/services-pmi-at-55-4-august-2026-ism-services-pmi-report-122.html">Aug</a>'''
        selected = syndicated_release_url(search, "ism_services_pmi", "2026-09")
        self.assertTrue(selected.endswith("september-2026-ism-services-pmi-report-123.html"))

    def test_rejects_ambiguous_syndicated_release_candidates(self) -> None:
        search = '''<a href="/news-releases/services-pmi-at-54-9-september-2026-ism-services-pmi-report-1.html">A</a>
        <a href="/news-releases/services-pmi-at-54-8-september-2026-ism-services-pmi-report-2.html">B</a>'''
        self.assertIsNone(syndicated_release_url(search, "ism_services_pmi", "2026-09"))

    def test_requires_ism_as_syndicated_issuer(self) -> None:
        valid = "<p>News provided by Institute for Supply Management</p><p>SOURCE Institute for Supply Management</p>"
        invalid = "<p>News provided by Example Media</p><p>SOURCE Example Media</p>"
        self.assertTrue(is_verified_ism_syndicated_release(valid))
        self.assertFalse(is_verified_ism_syndicated_release(invalid))


if __name__ == "__main__":
    unittest.main(verbosity=2)
