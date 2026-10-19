from __future__ import annotations

import unittest
from datetime import datetime, timezone

from driver_contract import (
    build_driver_contract,
    confirmation_ready,
    transition_driver,
    validate_driver_contract,
)


NOW = datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc)


class DriverContractTests(unittest.TestCase):
    def test_scheduled_event_is_risk_only_and_has_no_trading_permissions(self) -> None:
        contract = build_driver_contract({
            "title": "US CPI",
            "status": "scheduled",
            "effect_xauusd": "bearish",
            "event_time_utc": "2026-10-06T12:30:00Z",
            "source": "BLS",
            "source_url": "https://www.bls.gov/cpi/",
            "primary_source": True,
        }, NOW)
        self.assertEqual(contract["lifecycle_state"], "scheduled_risk")
        self.assertEqual(contract["expected_effect"]["xauusd"], "risk_only")
        self.assertEqual(contract["expected_effect"]["raw_xauusd"], "bearish")
        self.assertTrue(contract["validation"]["valid"])
        self.assertFalse(any(contract["permissions"].values()))

    def test_reported_headline_is_not_a_confirmed_cause(self) -> None:
        contract = build_driver_contract({
            "title": "Trump says conflict will end soon",
            "status": "released",
            "source": "news aggregator",
            "source_url": "https://example.com/story",
            "effect_xauusd": "bullish",
        }, NOW)
        self.assertEqual(contract["lifecycle_state"], "reported_fact")
        self.assertFalse(confirmation_ready(contract))

    def test_verified_syndicated_issuer_release_has_fact_permission_without_becoming_primary(self) -> None:
        contract = build_driver_contract({
            "title": "ISM Services PMI", "status": "released", "fact_status": "verified",
            "source": "ISM via PR Newswire",
            "source_url": "https://www.prnewswire.com/news-releases/example.html",
            "source_verified": True, "authoritative_fact": True,
            "primary_source": False, "verified_primary_source": False,
            "source_transport": "official_syndicated_release",
        }, NOW)
        self.assertFalse(contract["source"]["primary"])
        self.assertTrue(contract["source"]["authoritative_fact"])
        self.assertTrue(contract["source"]["fact_permission"])
        self.assertEqual(contract["source"]["transport"], "official_syndicated_release")

    def test_confirmation_requires_all_four_evidence_gates(self) -> None:
        contract = build_driver_contract({
            "title": "US yields spike",
            "status": "released",
            "fact_status": "verified",
            "source": "market data",
            "source_url": "https://example.com/yields",
            "source_verified": True,
            "effect_xauusd": "bearish",
            "channel": "real_yield_opportunity_cost",
            "explanation": "Higher yields increase the opportunity cost of holding gold.",
            "market_reaction": {"observed": True, "gold": "bearish displacement"},
            "reaction_status": "aligned",
        }, NOW)
        candidate = transition_driver(contract, "driver_candidate", "mechanism_and_timing_match", NOW)
        confirmed = transition_driver(candidate, "confirmed_driver", "market_reaction_aligned", NOW)
        self.assertTrue(confirmation_ready(confirmed))
        self.assertEqual(confirmed["lifecycle_state"], "confirmed_driver")
        self.assertFalse(any(confirmed["permissions"].values()))

    def test_incomplete_candidate_cannot_be_confirmed(self) -> None:
        contract = build_driver_contract({
            "title": "Iran de-escalation headline",
            "status": "released",
            "effect_xauusd": "bullish",
        }, NOW)
        candidate = transition_driver(contract, "driver_candidate", "rotation_hypothesis", NOW)
        with self.assertRaisesRegex(ValueError, "evidence is incomplete"):
            transition_driver(candidate, "confirmed_driver", "too_early", NOW)

    def test_priced_in_is_a_distinct_terminal_interpretation(self) -> None:
        contract = build_driver_contract({"title": "Expected statement", "status": "released"}, NOW)
        priced_in = transition_driver(contract, "priced_in", "impulse_preceded_publication", NOW)
        self.assertEqual(priced_in["lifecycle_state"], "priced_in")
        with self.assertRaises(ValueError):
            transition_driver(priced_in, "confirmed_driver", "late_reclassification", NOW)

    def test_confirmed_driver_can_later_be_contradicted(self) -> None:
        contract = build_driver_contract({
            "title": "Verified rates shock", "status": "released", "fact_status": "verified",
            "source_verified": True, "channel": "yield_channel", "explanation": "Defined mechanism.",
            "market_reaction": {"observed": True}, "reaction_status": "aligned",
        }, NOW)
        candidate = transition_driver(contract, "driver_candidate", "candidate", NOW)
        confirmed = transition_driver(candidate, "confirmed_driver", "confirmed", NOW)
        contradicted = transition_driver(confirmed, "contradicted", "reaction_reversed", NOW)
        self.assertEqual(contradicted["lifecycle_state"], "contradicted")

    def test_driver_cannot_receive_entry_or_direction_switch_permission(self) -> None:
        contract = build_driver_contract({"title": "Headline", "status": "released"}, NOW)
        contract["permissions"]["entry"] = True
        contract["permissions"]["direction_switch"] = True
        result = validate_driver_contract(contract)
        self.assertFalse(result["valid"])
        self.assertIn("DRIVER_EXCEEDS_ADVISORY_PERMISSION", result["errors"])

    def test_stable_identity_deduplicates_same_event(self) -> None:
        raw = {
            "title": "Trump says conflict will end soon",
            "event_time_utc": "2026-06-12T14:00:00Z",
            "source": "White House",
            "status": "released",
        }
        self.assertEqual(
            build_driver_contract(raw, NOW)["driver_id"],
            build_driver_contract(raw, NOW)["driver_id"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
