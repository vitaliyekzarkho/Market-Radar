from __future__ import annotations

import unittest

from fomc_phase_engine import build_phase_plan, package_tilt


FACTS = {
    "meeting_id": "2026-09-16",
    "timepoints": {
        "statement": {"event_time_utc": "2026-09-16T18:00:00+00:00", "verified": True},
        "sep": {"event_time_utc": "2026-09-16T18:00:00+00:00", "verified": True},
        "press_conference": {"event_time_utc": "2026-09-16T18:30:00+00:00", "verified": True},
    },
}


INTERPRETATION = {
    "decision_surprise": {"classification": "expectation_missing"},
    "statement_analysis": {"classification": "hawkish_tilt_candidate"},
    "sep_analysis": {"classification": "hawkish_tilt_candidate"},
    "press_conference_analysis": {
        "status": "compared", "classification": "hawkish",
        "relation_to_18_00_package": "confirms_prior_package", "qa_effect": "reinforces_opening",
    },
}


class FomcPhaseEngineTests(unittest.TestCase):
    def test_missing_expectation_does_not_erase_aligned_statement_and_sep(self) -> None:
        result = package_tilt(INTERPRETATION)
        self.assertEqual(result["classification"], "tightening_candidate")
        self.assertEqual(result["confidence"], "multi_component")
        self.assertIsNone(result["components"]["decision"])

    def test_conflicting_components_produce_mixed_package(self) -> None:
        changed = {**INTERPRETATION, "sep_analysis": {"classification": "dovish_tilt_candidate"}}
        self.assertEqual(package_tilt(changed)["classification"], "mixed")

    def test_18_00_and_18_30_are_separate_anchors(self) -> None:
        result = build_phase_plan(FACTS, INTERPRETATION)
        first, second = result["phases"]
        self.assertEqual(first["anchor_time_utc"], "2026-09-16T18:00:00+00:00")
        self.assertEqual(second["anchor_time_utc"], "2026-09-16T18:30:00+00:00")
        self.assertEqual(result["phase_separation_minutes"], 30)

    def test_hour_from_statement_is_contaminated_by_press_conference(self) -> None:
        first = build_phase_plan(FACTS, INTERPRETATION)["phases"][0]
        windows = {item["name"]: item for item in first["windows"]}
        self.assertTrue(windows["immediate_15m"]["measurement_allowed"])
        self.assertTrue(windows["pre_conference"]["measurement_allowed"])
        self.assertFalse(windows["headline_60m"]["measurement_allowed"])
        self.assertEqual(windows["headline_60m"]["contaminated_by"], "press_conference")

    def test_each_phase_has_expected_channels_but_no_trading_permission(self) -> None:
        phases = build_phase_plan(FACTS, INTERPRETATION)["phases"]
        for phase in phases:
            self.assertEqual(phase["policy_bias_candidate"], "tightening_candidate")
            self.assertEqual(phase["confirmation"]["hypothesized_transmission"]["gold"], "bearish")
            self.assertEqual(phase["confirmation"]["hypothesized_transmission"]["usd"], "bullish")
            self.assertIn("ужесточения", phase["policy_bias_ru"])
            self.assertFalse(any(phase["confirmation"]["permissions"].values()))

    def test_invalid_timing_disables_phase_plan(self) -> None:
        bad = {"timepoints": {"statement": {"event_time_utc": "2026-09-16T18:00:00Z"}}}
        self.assertFalse(build_phase_plan(bad, INTERPRETATION)["available"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
