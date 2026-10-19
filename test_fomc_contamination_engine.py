from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fomc_contamination_engine import classify_window, coverage_contract, normalize_events, planned_fomc_events


START = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
END = datetime(2026, 9, 16, 19, 0, tzinfo=timezone.utc)
ONE_DOMAIN_COMPLETE = {"status": "complete", "start_utc": "2026-09-16T00:00:00+00:00", "end_utc": "2026-09-17T00:00:00+00:00"}
COMPLETE = {"status": "complete", "domains": {"scheduled_macro": ONE_DOMAIN_COMPLETE, "unscheduled_news": ONE_DOMAIN_COMPLETE}}


class FomcContaminationEngineTests(unittest.TestCase):
    def test_empty_legacy_cache_is_not_called_clean(self) -> None:
        coverage = coverage_contract({"provider": "legacy", "events": []})
        result = classify_window(START, END, "2026-09-16", [], coverage)
        self.assertEqual(result["status"], "insufficient_coverage")
        self.assertFalse(result["causal_promotion_allowed"])

    def test_complete_coverage_without_competitor_is_clean(self) -> None:
        result = classify_window(START, END, "2026-09-16", [], COMPLETE)
        self.assertEqual(result["status"], "clean")
        self.assertTrue(result["causal_promotion_allowed"])

    def test_macro_calendar_alone_cannot_prove_window_clean(self) -> None:
        coverage = coverage_contract({}, ONE_DOMAIN_COMPLETE)
        result = classify_window(START, END, "2026-09-16", [], coverage)
        self.assertEqual(result["status"], "insufficient_coverage")

    def test_own_fomc_release_is_expected_not_contamination(self) -> None:
        events = [{
            "title": "Federal Reserve issues FOMC statement", "time_utc": START.isoformat(),
            "verified_primary_source": True, "currencies": [], "importance": "unknown",
        }]
        result = classify_window(START, END, "2026-09-16", events, COMPLETE)
        self.assertEqual(result["status"], "clean")
        self.assertEqual(len(result["expected_fomc_events"]), 1)

    def test_verified_competing_event_contaminates_window(self) -> None:
        events = [{
            "title": "US Treasury announces emergency measure", "time_utc": "2026-09-16T18:20:00+00:00",
            "verified_primary_source": True, "currencies": ["USD"], "importance": "high",
        }]
        result = classify_window(START, END, "2026-09-16", events, COMPLETE)
        self.assertEqual(result["status"], "contaminated")
        self.assertFalse(result["causal_promotion_allowed"])

    def test_unverified_high_impact_event_makes_window_mixed(self) -> None:
        events = [{
            "title": "President Trump Speaks", "time_utc": "2026-09-16T18:20:00+00:00",
            "verified_primary_source": False, "currencies": ["USD"], "importance": "high",
        }]
        result = classify_window(START, END, "2026-09-16", events, COMPLETE)
        self.assertEqual(result["status"], "mixed")
        self.assertFalse(result["causal_promotion_allowed"])

    def test_event_at_end_boundary_belongs_to_next_phase(self) -> None:
        events = [{
            "title": "President Trump Speaks", "time_utc": END.isoformat(),
            "verified_primary_source": False, "currencies": ["USD"], "importance": "high",
        }]
        result = classify_window(START, END, "2026-09-16", events, COMPLETE)
        self.assertEqual(result["status"], "clean")

    def test_normalization_keeps_calendar_and_official_sources(self) -> None:
        calendar = {"events": [{"title": "CPI", "event_time_utc": START.isoformat(), "currencies": ["USD"]}]}
        official = {"recent_releases": [{"title": "FOMC", "published_at_utc": END.isoformat(), "verified_primary_source": True}]}
        result = normalize_events(calendar, official)
        self.assertEqual([item["source_kind"] for item in result], ["calendar", "official_release"])

    def test_phase_plan_restores_expected_fomc_anchors(self) -> None:
        plan = {"phases": [{"phase_id": "fomc_18_00_package", "anchor_time_utc": START.isoformat()}]}
        result = planned_fomc_events(plan)
        self.assertEqual(result[0]["source_kind"], "verified_phase_plan")
        self.assertIn("FOMC", result[0]["title"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
