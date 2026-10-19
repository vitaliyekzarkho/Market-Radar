from __future__ import annotations

import unittest

from priced_in_engine import apply_priced_in_gate, evaluate_priced_in, no_material_reaction_evaluation


def driver(state: str = "driver_candidate") -> dict:
    return {"driver_id": "evt-1", "lifecycle_state": state}


def window(direction: str, move: float, complete: bool = True, displacement: float | None = None) -> dict:
    value = {"available": True, "direction": direction, "move_percent": move, "complete": complete}
    if displacement is not None:
        value["displacement_ratio_to_pre_atr"] = displacement
    return value


def report(pre_direction: str, pre_move: float) -> dict:
    return {"assets": {"gold": {"pre_event": {
        "pre_60m": window(pre_direction, pre_move),
        "pre_4h": window(pre_direction, pre_move),
        "pre_24h": window(pre_direction, pre_move),
    }}}}


def evaluation(post_direction: str, post_move: float, displacement: float = 1.2, ready: bool = True) -> dict:
    return {
        "candidate_id": "mech-1", "preferred_window": "post_60m",
        "confirmation_eligible": ready,
        "channels": {"gold": {
            "expected": "bearish", "observed_direction": post_direction,
            "relation": "aligned" if post_direction == "bearish" else "contradicted",
            "window": "post_60m", "window_complete": True,
            "move_percent": post_move, "displacement_ratio": displacement,
        }},
    }


class PricedInEngineTests(unittest.TestCase):
    def test_no_material_reaction_is_terminal_context_only(self) -> None:
        result = no_material_reaction_evaluation(driver(), {
            "candidate_id": "mech-1", "confirmation_blocker": "IMMATERIAL_REACTION",
        })
        self.assertEqual(result["classification"], "no_material_reaction")
        self.assertEqual(result["causal_role"], "context_only")
        self.assertTrue(result["terminal"])
        self.assertFalse(result["driver_confirmation_allowed"])
        self.assertFalse(any(result["permissions"].values()))

    def test_new_release_with_weak_pre_move_and_strong_post_is_new_catalyst(self) -> None:
        result = evaluate_priced_in(driver(), evaluation("bearish", -1.2), report("bullish", 0.1), {
            "scheduled_actual_release": True,
        })
        self.assertEqual(result["classification"], "new_catalyst")
        self.assertTrue(result["driver_confirmation_allowed"])
        self.assertTrue(result["origin_claim_allowed"])

    def test_large_aligned_move_before_headline_without_acceleration_preceded_it(self) -> None:
        result = evaluate_priced_in(driver(), evaluation("bearish", -0.2, 0.3), report("bearish", -2.0), {
            "first_verified_publication": True,
        })
        self.assertEqual(result["classification"], "reaction_preceded_headline")
        self.assertFalse(result["driver_confirmation_allowed"])

    def test_repeated_statement_after_pre_move_is_priced_in(self) -> None:
        result = evaluate_priced_in(driver(), evaluation("bearish", -0.3, 0.4), report("bearish", -1.5), {
            "repeated_statement": True, "prior_verified_reports": 2,
        })
        self.assertEqual(result["classification"], "priced_in")
        self.assertEqual(result["causal_role"], "explanation_only")

    def test_new_fact_that_accelerates_existing_move_is_amplifier_not_origin(self) -> None:
        result = evaluate_priced_in(driver(), evaluation("bearish", -1.0, 1.4), report("bearish", -1.2), {
            "first_verified_publication": True,
        })
        self.assertEqual(result["classification"], "amplifier")
        self.assertTrue(result["driver_confirmation_allowed"])
        self.assertFalse(result["origin_claim_allowed"])

    def test_unknown_novelty_never_confirms_driver(self) -> None:
        result = evaluate_priced_in(driver(), evaluation("bearish", -1.0), report("bullish", 0.1))
        self.assertEqual(result["classification"], "inconclusive")
        self.assertFalse(result["driver_confirmation_allowed"])

    def test_incomplete_causal_validation_cannot_pass(self) -> None:
        result = evaluate_priced_in(driver(), evaluation("bearish", -1.0, ready=False), report("bullish", 0.1), {
            "scheduled_actual_release": True,
        })
        self.assertEqual(result["classification"], "inconclusive")

    def test_incomplete_pre_window_cannot_pass(self) -> None:
        reaction = report("bullish", 0.1)
        reaction["assets"]["gold"]["pre_event"]["pre_60m"]["complete"] = False
        result = evaluate_priced_in(driver(), evaluation("bearish", -1.0), reaction, {
            "scheduled_actual_release": True,
        })
        self.assertEqual(result["classification"], "inconclusive")

    def test_priced_in_contract_enters_priced_in_lifecycle(self) -> None:
        result = evaluate_priced_in(driver(), evaluation("bearish", -0.2, 0.2), report("bearish", -1.5), {
            "repeated_statement": True,
        })
        updated = apply_priced_in_gate(driver(), result)
        self.assertEqual(updated["lifecycle_state"], "priced_in")

    def test_new_catalyst_can_confirm_driver_but_never_authorize_entry(self) -> None:
        result = evaluate_priced_in(driver(), evaluation("bearish", -1.2), report("bullish", 0.1), {
            "scheduled_actual_release": True,
        })
        updated = apply_priced_in_gate(driver(), result)
        self.assertEqual(updated["lifecycle_state"], "confirmed_driver")
        self.assertFalse(result["permissions"]["entry"])
        self.assertFalse(result["permissions"]["direction_switch"])

    def test_amplifier_is_confirmed_only_as_amplifier(self) -> None:
        result = evaluate_priced_in(driver(), evaluation("bearish", -1.0, 1.4), report("bearish", -1.2), {
            "first_verified_publication": True,
        })
        updated = apply_priced_in_gate(driver(), result)
        self.assertEqual(updated["lifecycle_state"], "confirmed_driver")
        self.assertEqual(updated["causal_role"], "amplifier_not_origin")

    def test_repeated_headline_cannot_be_rescued_by_strong_post_move(self) -> None:
        result = evaluate_priced_in(driver(), evaluation("bearish", -2.0, 2.0), report("bullish", 0.1), {
            "repeated_statement": True,
        })
        self.assertEqual(result["classification"], "priced_in")
        self.assertFalse(result["driver_confirmation_allowed"])

    def test_repeated_fact_is_priced_in_even_when_mechanism_is_contradicted(self) -> None:
        contradicted = evaluation("bullish", 1.0, ready=False)
        result = evaluate_priced_in(driver(), contradicted, report("bullish", 0.5), {
            "repeated_statement": True, "prior_verified_reports": 1,
        })
        self.assertEqual(result["classification"], "priced_in")
        self.assertEqual(result["causal_role"], "explanation_only")


if __name__ == "__main__":
    unittest.main(verbosity=2)
