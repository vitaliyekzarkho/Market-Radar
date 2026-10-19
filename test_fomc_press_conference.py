from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fomc_interpretation_engine import interpret_press_conference, press_conference_snapshot


EVENT = "2026-09-16T18:30:00+00:00"


def official(opening: str, qa: str, verified: bool = True) -> dict:
    return {
        "timepoints": {"press_conference": {"event_time_utc": EVENT, "verified": True}},
        "press_conference_document": {
            "verified": verified, "status": "verified" if verified else "not_published_or_unavailable",
            "source_url": "https://www.federalreserve.gov/mediacenter/files/FOMCpresconf20260916.pdf",
            "prepared_remarks": opening, "qa": qa,
        },
    }


HAWKISH_STATEMENT = {"classification": "hawkish_tilt_candidate"}
HAWKISH_SEP = {"classification": "broad_hawkish_shift"}


class FomcPressConferenceTests(unittest.TestCase):
    def test_explicit_claims_not_generic_words_create_tilt(self) -> None:
        snapshot = press_conference_snapshot("Inflation is too high. We removed a dose of accommodation.")
        self.assertEqual(snapshot["classification"], "hawkish")
        self.assertEqual({item["claim"] for item in snapshot["claims"]}, {"inflation_too_high", "removed_accommodation"})

    def test_hawkish_opening_and_qa_confirm_prior_package(self) -> None:
        result = interpret_press_conference(
            official(
                "Inflation is too high. Our predominant focus is on the price-stability side.",
                "We are hard pressed to describe financial conditions as restrictive.",
            ), HAWKISH_STATEMENT, HAWKISH_SEP, datetime(2026, 9, 16, 20, tzinfo=timezone.utc),
        )
        self.assertEqual(result["classification"], "hawkish")
        self.assertEqual(result["relation_to_18_00_package"], "confirms_prior_package")
        self.assertEqual(result["qa_effect"], "reinforces_opening")
        self.assertFalse(any(result["permissions"].values()))

    def test_dovish_qa_can_reverse_hawkish_opening_and_complicate_package(self) -> None:
        result = interpret_press_conference(
            official(
                "Inflation is too high.",
                "Downside risks to the labor market have risen. Policy remains restrictive. There is scope to lower rates when appropriate.",
            ), HAWKISH_STATEMENT, HAWKISH_SEP, datetime(2026, 9, 16, 20, tzinfo=timezone.utc),
        )
        self.assertEqual(result["classification"], "dovish")
        self.assertEqual(result["relation_to_18_00_package"], "reverses_prior_package")
        self.assertEqual(result["qa_effect"], "changes_or_complicates_opening")

    def test_future_phase_and_missing_transcript_do_not_invent_interpretation(self) -> None:
        future = interpret_press_conference(
            official("Inflation is too high.", ""), HAWKISH_STATEMENT, HAWKISH_SEP,
            datetime(2026, 9, 16, 18, 10, tzinfo=timezone.utc),
        )
        missing = interpret_press_conference(
            official("", "", verified=False), HAWKISH_STATEMENT, HAWKISH_SEP,
            datetime(2026, 9, 16, 20, tzinfo=timezone.utc),
        )
        self.assertEqual(future["classification"], "unavailable_before_event")
        self.assertEqual(missing["classification"], "unavailable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
