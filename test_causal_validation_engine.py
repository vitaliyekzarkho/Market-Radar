from __future__ import annotations

import unittest

from causal_mechanism_engine import propose_mechanisms
from causal_validation_engine import candidate_contract, validate_candidates, validate_mechanism
from driver_contract import build_driver_contract


EVENT_TIME = "2026-06-12T14:00:00+00:00"


def contract(verified: bool = True, lifecycle: str = "reported_fact") -> dict:
    return build_driver_contract({
        "driver_id": "evt-1", "title": "US real yields spike", "status": "released",
        "lifecycle_state": lifecycle, "fact_status": "verified" if verified else "reported",
        "source": "Federal Reserve", "source_url": "https://www.federalreserve.gov/test",
        "source_verified": verified, "primary_source": verified,
        "event_time_utc": EVENT_TIME,
    })


def asset(direction: str, complete: bool = True, move: float = 1.0, displacement: float = 1.5) -> dict:
    window = {
        "available": True, "direction": direction, "complete": complete,
        "status": "complete" if complete else "developing", "move_percent": move,
        "displacement_ratio_to_pre_atr": displacement,
    }
    return {"post_event": {name: dict(window) for name in ("post_15m", "post_60m", "post_4h", "post_24h")}}


def reactions(**assets: dict) -> dict:
    return {"event_id": "evt-1", "event_time_utc": EVENT_TIME, "assets": assets}


class CausalValidationEngineTests(unittest.TestCase):
    def test_verified_yield_shock_with_aligned_channel_becomes_eligible_only(self) -> None:
        driver = contract()
        mechanism = propose_mechanisms({"event_id": "evt-1", "event_family": "yield_shock_up"})["candidates"][0]
        report = reactions(gold=asset("bearish"), real_yields=asset("rising"))
        result = validate_mechanism(driver, mechanism, report)
        self.assertTrue(result["confirmation_eligible"])
        self.assertEqual(result["alignment"], "eligible_after_priced_in_check")
        self.assertFalse(result["permissions"]["confirm_driver"])

    def test_aligned_market_cannot_rescue_unverified_headline(self) -> None:
        driver = contract(verified=False)
        mechanism = propose_mechanisms({"event_id": "evt-1", "event_family": "yield_shock_up"})["candidates"][0]
        result = validate_mechanism(driver, mechanism, reactions(gold=asset("bearish"), real_yields=asset("rising")))
        self.assertFalse(result["confirmation_eligible"])
        self.assertFalse(result["event_gates"]["fact_verified"])

    def test_gold_without_transmission_channel_is_not_enough(self) -> None:
        driver = contract()
        mechanism = propose_mechanisms({"event_id": "evt-1", "event_family": "yield_shock_up"})["candidates"][0]
        result = validate_mechanism(driver, mechanism, reactions(gold=asset("bearish")))
        self.assertFalse(result["confirmation_eligible"])
        self.assertIn("fresh_rate_move", result["missing_evidence"])

    def test_gold_opposite_reaction_contradicts_mechanism(self) -> None:
        driver = contract()
        mechanism = propose_mechanisms({"event_id": "evt-1", "event_family": "yield_shock_up"})["candidates"][0]
        result = validate_mechanism(driver, mechanism, reactions(gold=asset("bullish"), real_yields=asset("rising")))
        self.assertEqual(result["alignment"], "contradicted")
        self.assertIn("gold", result["hard_conflicts"])

    def test_developing_window_can_align_preliminarily_but_not_confirm(self) -> None:
        driver = contract()
        mechanism = propose_mechanisms({"event_id": "evt-1", "event_family": "yield_shock_up"})["candidates"][0]
        result = validate_mechanism(driver, mechanism, reactions(gold=asset("bearish", complete=False), real_yields=asset("rising", complete=False)))
        self.assertEqual(result["alignment"], "partially_aligned")
        self.assertFalse(result["confirmation_eligible"])

    def test_aligned_but_immaterial_gold_and_usd_are_not_confirmation(self) -> None:
        driver = contract()
        mechanism = propose_mechanisms({
            "event_id": "evt-1", "event_family": "growth_labor_downside_surprise",
        })["candidates"][0]
        result = validate_mechanism(
            driver, mechanism,
            reactions(gold=asset("bullish", displacement=0.05), usd=asset("bearish", displacement=0.24)),
        )
        self.assertEqual(result["alignment"], "directionally_aligned_but_immaterial")
        self.assertEqual(result["confirmation_blocker"], "IMMATERIAL_REACTION")
        self.assertFalse(result["confirmation_eligible"])
        self.assertNotIn("gold_bullish_reaction", result["satisfied_evidence"])
        self.assertNotIn("rates_or_usd_confirmation", result["satisfied_evidence"])

    def test_immaterial_opposite_tick_is_not_a_hard_conflict(self) -> None:
        driver = contract()
        mechanism = propose_mechanisms({"event_id": "evt-1", "event_family": "yield_shock_up"})["candidates"][0]
        result = validate_mechanism(
            driver, mechanism,
            reactions(gold=asset("bullish", displacement=0.1), real_yields=asset("rising")),
        )
        self.assertNotIn("gold", result["hard_conflicts"])

    def test_closed_immaterial_reaction_expires_instead_of_entering_priced_in_gate(self) -> None:
        driver = contract()
        mechanisms = propose_mechanisms({
            "event_id": "evt-1", "event_family": "growth_labor_downside_surprise",
        })
        report = reactions(
            gold=asset("bullish", displacement=0.05),
            usd=asset("bearish", displacement=0.24),
        )
        result = validate_candidates(driver, mechanisms, report)
        self.assertFalse(result["any_confirmation_eligible"])
        self.assertEqual(result["next_gate"], "reaction_window_expired")
        self.assertEqual(
            result["evaluations"][0]["alignment"],
            "directionally_aligned_but_immaterial",
        )

    def test_deescalation_oil_down_gold_up_does_not_confirm_primary_unwind(self) -> None:
        driver = contract()
        mechanism = propose_mechanisms({"event_id": "evt-1", "event_family": "geopolitical_deescalation"})["candidates"][0]
        result = validate_mechanism(driver, mechanism, reactions(gold=asset("bullish"), oil=asset("bearish"), equities=asset("bullish")))
        self.assertFalse(result["confirmation_eligible"])
        self.assertIn("gold", result["hard_conflicts"])

    def test_rotation_requires_external_pa_and_value_evidence(self) -> None:
        driver = contract()
        rotation = propose_mechanisms({"event_id": "evt-1", "event_family": "geopolitical_deescalation"})["candidates"][1]
        report = reactions(gold=asset("bullish"), oil=asset("bearish"), real_yields=asset("falling"), usd=asset("bearish"))
        result = validate_mechanism(driver, rotation, report)
        self.assertFalse(result["confirmation_eligible"])
        self.assertIn("gold_at_htf_value", result["missing_evidence"])
        self.assertIn("gold_bullish_pa", result["missing_evidence"])

    def test_rotation_can_become_eligible_only_with_all_conditional_evidence(self) -> None:
        driver = contract()
        rotation = propose_mechanisms({"event_id": "evt-1", "event_family": "geopolitical_deescalation"})["candidates"][1]
        report = reactions(gold=asset("bullish"), oil=asset("bearish"), real_yields=asset("falling"), usd=asset("bearish"))
        evidence = {"gold_at_htf_value": True, "gold_bullish_pa": True, "rates_and_usd_not_conflicting": True}
        result = validate_mechanism(driver, rotation, report, evidence)
        self.assertTrue(result["confirmation_eligible"])
        self.assertEqual(result["confirmation_blocker"], "PRICED_IN_CHECK_PENDING")

    def test_event_identity_and_time_must_match(self) -> None:
        driver = contract()
        mechanism = propose_mechanisms({"event_id": "evt-1", "event_family": "yield_shock_up"})["candidates"][0]
        report = reactions(gold=asset("bearish"), real_yields=asset("rising"))
        report["event_id"] = "different-event"
        result = validate_mechanism(driver, mechanism, report)
        self.assertFalse(result["event_gates"]["timing_match"])
        self.assertFalse(result["confirmation_eligible"])

    def test_validation_ranks_eligible_candidate_but_does_not_confirm_driver(self) -> None:
        driver = contract()
        mechanisms = propose_mechanisms({"event_id": "evt-1", "event_family": "geopolitical_escalation"})
        report = reactions(gold=asset("bullish"), oil=asset("bullish"), usd=asset("bullish"), equities=asset("bearish"))
        result = validate_candidates(driver, mechanisms, report, {"risk_on_confirmation": True})
        self.assertFalse(result["driver_confirmation_allowed"])
        self.assertEqual(result["next_gate"], "priced_in_detection")

    def test_candidate_contract_never_promotes_to_confirmed(self) -> None:
        driver = contract()
        mechanism = propose_mechanisms({"event_id": "evt-1", "event_family": "yield_shock_up"})["candidates"][0]
        evaluation = validate_mechanism(driver, mechanism, reactions(gold=asset("bearish"), real_yields=asset("rising")))
        updated = candidate_contract(driver, evaluation)
        self.assertEqual(updated["lifecycle_state"], "driver_candidate")
        self.assertNotEqual(updated["lifecycle_state"], "confirmed_driver")

    def test_verified_fact_only_satisfies_its_own_event_family(self) -> None:
        driver = contract()
        mechanism = propose_mechanisms({"event_id": "evt-1", "event_family": "geopolitical_deescalation"})["candidates"][0]
        result = validate_mechanism(
            driver, mechanism, reactions(gold=asset("bearish"), oil=asset("bearish"), equities=asset("bullish")),
            {"risk_on_confirmation": True},
        )
        self.assertIn("verified_deescalation_fact", result["satisfied_evidence"])
        self.assertNotIn("verified_escalation_fact", result["satisfied_evidence"])
        self.assertNotIn("verified_policy_fact", result["satisfied_evidence"])
        self.assertNotIn("verified_upside_surprise", result["satisfied_evidence"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
