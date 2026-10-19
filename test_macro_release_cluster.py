from __future__ import annotations

import unittest

from macro_release_cluster import cluster_release


def event(title: str, actual: str, forecast: str, minute: int = 30) -> dict:
    return {
        "title": title, "actual": actual, "forecast": forecast, "status": "released",
        "currencies": ["USD"], "event_time_utc": f"2026-09-10T12:{minute:02d}:00+00:00",
    }


class MacroReleaseClusterTests(unittest.TestCase):
    def test_mixed_ppi_components_stay_mixed(self) -> None:
        events = [
            event("PPI m/m", "0.3%", "0.3%"),
            event("Core PPI m/m", "0.1%", "0.2%"),
            event("PPI y/y", "3.1%", "3.0%"),
        ]
        result = cluster_release(events[0], events)
        self.assertEqual(result["event_family"], "inflation_mixed_release")
        self.assertEqual(len(result["release_components"]), 3)

    def test_higher_claims_are_labor_downside(self) -> None:
        item = event("Unemployment Claims", "260K", "240K")
        result = cluster_release(item, [item])
        self.assertEqual(result["event_family"], "growth_labor_downside_surprise")

    def test_simultaneous_cpi_components_form_one_release(self) -> None:
        events = [event("CPI m/m", "0.4%", "0.3%"), event("Core CPI m/m", "0.3%", "0.2%")]
        result = cluster_release(events[0], events)
        self.assertEqual(result["event_family"], "inflation_upside_surprise")

    def test_unrelated_release_is_not_merged(self) -> None:
        events = [event("CPI m/m", "0.4%", "0.3%"), event("Non-Farm Payrolls", "180K", "170K")]
        result = cluster_release(events[0], events)
        self.assertEqual(len(result["release_components"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
