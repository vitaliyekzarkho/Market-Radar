from __future__ import annotations

import unittest

from fomc_interpretation_engine import interpret_statement, statement_snapshot


JULY = """July 29, 2026 Federal Reserve issues FOMC statement
The Federal Open Market Committee approved the following statement for release by a 9 – 3 vote:
Economic activity is expanding at a solid pace despite elevated uncertainty that owes, in part, to the conflict in the Middle East.
Productivity growth and capital investment are strong. Job gains have kept pace with the workforce, and the unemployment rate has changed little.
Inflation remains elevated relative to the Committee's 2 percent goal, in part reflecting supply shocks that have driven price increases in certain sectors, including energy.
The Committee will deliver price stability."""

SEPTEMBER = """September 16, 2026 Federal Reserve issues FOMC statement
The Federal Open Market Committee approved the following statement for release by a 12 – 0 vote:
Economic activity is expanding at a solid pace. While uncertainty remains elevated owing, in part, to geopolitical developments, domestic spending has been resilient.
Productivity growth is strong, and capital investment is robust. Job gains have kept pace with the workforce, and the unemployment rate has changed little.
Inflation remains elevated. Today's policy action will support a timelier return to the Committee's 2 percent goal. The Committee will deliver price stability."""


def official(current: str = SEPTEMBER, previous: str = JULY) -> dict:
    return {"statement_documents": {
        "current": {"text": current, "verified": True, "source_url": "https://fed/sep"},
        "previous": {"text": previous, "verified": True, "source_url": "https://fed/jul"},
    }}


class FomcStatementInterpretationTests(unittest.TestCase):
    def test_snapshot_extracts_semantic_features_and_vote(self) -> None:
        snap = statement_snapshot(SEPTEMBER)
        self.assertTrue(snap["features"]["domestic_spending_resilient"])
        self.assertTrue(snap["features"]["timelier_return_to_target"])
        self.assertEqual(snap["vote"], {"for": 12, "against": 0, "available": True})

    def test_real_july_to_september_delta_is_hawkish_candidate(self) -> None:
        result = interpret_statement(official())
        changes = {item["feature"]: item for item in result["changes"]}
        self.assertEqual(result["classification"], "hawkish_tilt_candidate")
        self.assertEqual(changes["domestic_spending_resilient"]["tilt"], "hawkish")
        self.assertEqual(changes["timelier_return_to_target"]["tilt"], "hawkish")
        self.assertIn("изменение формулировок statement", result["mechanism_candidates"][0]["description_ru"])
        self.assertNotIn("ставка", result["mechanism_candidates"][0]["description_ru"].lower())

    def test_removed_supply_and_energy_attribution_stays_ambiguous(self) -> None:
        result = interpret_statement(official())
        changes = {item["feature"]: item for item in result["changes"]}
        self.assertEqual(changes["supply_shock_attribution"]["tilt"], "ambiguous")
        self.assertEqual(changes["energy_price_attribution"]["causal_permission"], "context_only")

    def test_unchanged_activity_labor_and_inflation_are_not_new_drivers(self) -> None:
        result = interpret_statement(official())
        unchanged = set(result["unchanged_features"])
        self.assertTrue({"activity_solid", "job_gains_keep_pace", "inflation_elevated"}.issubset(unchanged))
        changed = {item["feature"] for item in result["changes"]}
        self.assertNotIn("inflation_elevated", changed)

    def test_vote_consensus_has_no_directional_tilt(self) -> None:
        result = interpret_statement(official())
        self.assertEqual(result["vote_consensus"]["classification"], "stronger_consensus_for_current_action")
        self.assertEqual(result["vote_consensus"]["directional_tilt"], "none")

    def test_same_statement_has_no_directional_change(self) -> None:
        result = interpret_statement(official(SEPTEMBER, SEPTEMBER))
        self.assertEqual(result["classification"], "no_directional_change")
        self.assertEqual(result["changes"], [])
        self.assertEqual(result["mechanism_candidates"], [])

    def test_missing_previous_statement_disables_comparison(self) -> None:
        result = interpret_statement({"statement_documents": {"current": {"text": SEPTEMBER, "verified": True}}})
        self.assertEqual(result["status"], "comparison_unavailable")
        self.assertFalse(result["directional"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
