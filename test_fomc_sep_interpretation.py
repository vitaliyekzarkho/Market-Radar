from __future__ import annotations

import unittest

from fomc_interpretation_engine import interpret_sep, interpret_sep_distribution, sep_medians, sep_rate_distribution


ROWS = [
    ["Variable", "Median1", "Central Tendency2", "Range3"],
    ["2026", "2027", "2028", "2029", "Longer run"] * 3,
    ["Change in real GDP", "2.3", "2.4", "2.2", "2.1", "2.0"],
    ["June projection", "2.2", "2.3", "2.2", "", "2.0"],
    ["Unemployment rate", "4.1", "4.1", "4.1", "4.1", "4.2"],
    ["June projection", "4.3", "4.3", "4.2", "", "4.2"],
    ["PCE inflation", "3.7", "2.3", "2.1", "2.0", "2.0"],
    ["June projection", "3.6", "2.3", "2.0", "", "2.0"],
    ["Core PCE inflation4", "3.4", "2.5", "2.2", "2.0", ""],
    ["June projection", "3.3", "2.5", "2.1", "", ""],
    ["Memo: Projected appropriate policy path"],
    ["Federal funds rate", "4.1", "4.1", "3.9", "3.6", "3.2"],
    ["June projection", "3.8", "3.6", "3.4", "", "3.1"],
]

DIST_ROWS = [
    ["Percent Range", "2026", "2027", "2028", "2029", "Longer Run"],
    ["June projections", "September projections", "June projections", "September projections", "June projections", "September projections", "September projections", "June projections", "September projections"],
    ["2.63- 2.87", "", "", "", "", "", "", "", "", ""],
    ["2.88- 3.12", "", "", "1", "", "1", "", "1", "9", "7"],
    ["3.13- 3.37", "", "", "4", "1", "6", "4", "4", "3", "3"],
    ["3.38- 3.62", "1", "", "3", "", "2", "1", "2", "3", "3"],
    ["3.63- 3.87", "8", "", "2", "3", "5", "3", "7", "2", "3"],
    ["3.88- 4.12", "3", "2", "5", "", "3", "5", "3", "1", "2"],
    ["4.13- 4.37", "5", "12", "2", "6", "", "4", "", "", ""],
    ["4.38- 4.62", "1", "4", "1", "8", "", "", "", "", ""],
]


def official(rows=ROWS, verified=True):
    return {"sep_document": {"verified": verified, "source_url": "https://fed/sep", "table_rows": rows, "rate_distribution_rows": DIST_ROWS}}


class FomcSepInterpretationTests(unittest.TestCase):
    def test_parses_current_and_previous_medians(self) -> None:
        parsed = sep_medians(ROWS)
        self.assertEqual(parsed["variables"]["federal_funds_rate"]["current"]["2027"], 4.1)
        self.assertEqual(parsed["variables"]["federal_funds_rate"]["previous"]["2027"], 3.6)
        self.assertEqual(parsed["variables"]["core_pce_inflation"]["current"]["2026"], 3.4)

    def test_real_september_medians_create_hawkish_candidate(self) -> None:
        result = interpret_sep(official())
        self.assertEqual(result["classification"], "hawkish_tilt_candidate")
        self.assertEqual(result["mechanism_candidates"][0]["origin"], "sep_median_delta")
        self.assertEqual(result["distribution_analysis"]["classification"], "broad_hawkish_shift")

    def test_policy_minus_pce_is_labeled_proxy_not_real_yield(self) -> None:
        result = interpret_sep(official())
        proxy = result["policy_minus_pce_proxy"]
        self.assertIn("not a market real yield", proxy["definition"])
        values = {item["period"]: item for item in proxy["values"]}
        self.assertAlmostEqual(values["2026"]["current"], 0.4)
        self.assertAlmostEqual(values["2026"]["previous"], 0.2)
        self.assertAlmostEqual(values["2026"]["delta_pp"], 0.2)

    def test_new_year_without_previous_is_not_fabricated_as_delta(self) -> None:
        result = interpret_sep(official())
        self.assertFalse(any(item["period"] == "2029" for item in result["median_changes"]))

    def test_sep_never_grants_trading_permissions(self) -> None:
        result = interpret_sep(official())
        self.assertFalse(any(result["permissions"].values()))

    def test_unverified_sep_disables_comparison(self) -> None:
        result = interpret_sep(official(verified=False))
        self.assertEqual(result["classification"], "unavailable")
        self.assertEqual(result["mechanism_candidates"], [])

    def test_rate_distribution_moves_broadly_higher_not_just_extremes(self) -> None:
        result = interpret_sep_distribution(DIST_ROWS)
        self.assertEqual(result["classification"], "broad_hawkish_shift")
        self.assertTrue(result["confirms_median_direction"])
        periods = {item["period"]: item for item in result["periods"]}
        self.assertGreater(periods["2026"]["weighted_mean_delta_pp"], 0)
        self.assertGreater(periods["2027"]["median_bin_delta_pp"], 0)
        self.assertGreater(periods["2028"]["weighted_mean_delta_pp"], 0)

    def test_new_2029_distribution_has_no_fake_previous_comparison(self) -> None:
        parsed = sep_rate_distribution(DIST_ROWS)
        self.assertTrue(parsed["2029"]["current"]["available"])
        self.assertFalse(parsed["2029"]["previous"]["available"])
        result = interpret_sep_distribution(DIST_ROWS)
        self.assertNotIn("2029", {item["period"] for item in result["periods"]})

    def test_distribution_does_not_grant_permissions(self) -> None:
        result = interpret_sep_distribution(DIST_ROWS)
        self.assertFalse(any(result["permissions"].values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
