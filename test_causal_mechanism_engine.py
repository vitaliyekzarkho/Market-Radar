from __future__ import annotations

import unittest

from causal_mechanism_engine import classify_event_family, macro_release_status, macro_surprise_templates, propose_mechanisms


class CausalMechanismEngineTests(unittest.TestCase):
    def test_yield_spike_builds_opportunity_cost_hypothesis(self) -> None:
        result = propose_mechanisms({"event_id": "rates-1", "title": "US real yields spike", "status": "released"})
        candidate = result["candidates"][0]
        self.assertEqual(result["event_family"], "yield_shock_up")
        self.assertEqual(candidate["expected"]["gold"], "bearish")
        self.assertIn("fresh_rate_move", candidate["required_evidence"])

    def test_deescalation_produces_primary_and_conditional_rotation(self) -> None:
        result = propose_mechanisms({"event_id": "iran-1", "title": "Iran agrees to suspend enrichment in return for sanctions relief", "status": "reported"})
        self.assertEqual(result["event_family"], "geopolitical_deescalation")
        self.assertEqual(len(result["candidates"]), 2)
        primary, rotation = result["candidates"]
        self.assertTrue(primary["primary"])
        self.assertEqual(primary["expected"]["oil"], "bearish")
        self.assertTrue(rotation["conditional"])
        self.assertIn("gold_bullish_pa", rotation["required_evidence"])

    def test_rotation_never_becomes_automatic_bullish_gold(self) -> None:
        rotation = propose_mechanisms({"event_id": "iran-2", "theme": "geopolitical_deescalation"})["candidates"][1]
        self.assertFalse(rotation["confirmation_allowed"])
        self.assertFalse(rotation["permissions"]["entry"])
        self.assertFalse(rotation["permissions"]["direction_switch"])

    def test_escalation_allows_gold_and_usd_to_rise_together(self) -> None:
        candidate = propose_mechanisms({"event_id": "war-1", "title": "Military escalation after missile strike"})["candidates"][0]
        self.assertEqual(candidate["expected"]["gold"], "bullish")
        self.assertEqual(candidate["expected"]["usd"], "bullish_or_mixed")

    def test_escalation_also_keeps_liquidity_sale_alternative(self) -> None:
        result = propose_mechanisms({"event_id": "war-2", "title": "Iran retaliation after attack on US base"})
        alternatives = [item for item in result["candidates"] if not item["primary"]]
        self.assertEqual(alternatives[0]["mechanism_id"], "liquidity_sale_during_panic")
        self.assertEqual(alternatives[0]["expected"]["gold"], "bearish")

    def test_liquidity_stress_explains_usd_up_gold_down(self) -> None:
        candidate = propose_mechanisms({"event_id": "liq-1", "title": "Forced liquidation and dash for cash"})["candidates"][0]
        self.assertEqual(candidate["expected"]["usd"], "bullish")
        self.assertEqual(candidate["expected"]["gold"], "bearish")

    def test_trump_end_conflict_statement_is_only_candidate(self) -> None:
        result = propose_mechanisms({"event_id": "trump-1", "title": "Trump says conflict will end soon", "status": "reported"})
        self.assertEqual(result["event_family"], "geopolitical_deescalation")
        self.assertEqual(result["candidates"][0]["state"], "mechanism_candidate")
        self.assertFalse(result["confirmation_allowed"])

    def test_scheduled_mechanism_remains_risk_template(self) -> None:
        result = propose_mechanisms({"event_id": "fed-1", "title": "Fed rate hike decision", "status": "scheduled"})
        self.assertEqual(result["candidates"][0]["state"], "risk_template")
        self.assertFalse(result["candidates"][0]["fact_verified"])

    def test_unknown_headline_does_not_invent_mechanism(self) -> None:
        result = propose_mechanisms({"event_id": "x", "title": "Officials meet on Tuesday"})
        self.assertFalse(result["recognized"])
        self.assertEqual(result["candidates"], [])

    def test_explicit_family_has_priority_over_vague_title(self) -> None:
        family = classify_event_family({"event_family": "liquidity_stress", "title": "Markets move"})
        self.assertEqual(family, "liquidity_stress")

    def test_macro_before_actual_has_two_symmetric_paths(self) -> None:
        result = macro_surprise_templates({"event_id": "cpi-1", "title": "US CPI"})
        self.assertEqual(len(result["candidates"]), 2)
        effects = {item["expected"]["gold"] for item in result["candidates"]}
        self.assertEqual(effects, {"bullish", "bearish"})
        self.assertTrue(all(item["state"] == "risk_template" for item in result["candidates"]))

    def test_all_inline_components_create_no_directional_mechanism(self) -> None:
        event = {
            "event_id": "cpi-inline", "event_family": "inflation_upside_surprise",
            "release_components": [
                {"name": "headline_mom", "actual": "0.3%", "forecast": "0.3%"},
                {"name": "core_mom", "actual": "0.2%", "forecast": "0.2%"},
            ],
        }
        self.assertEqual(macro_release_status(event), "in_line")
        result = propose_mechanisms(event)
        self.assertTrue(result["recognized"])
        self.assertFalse(result["directional_surprise"])
        self.assertEqual(result["candidates"], [])

    def test_mixed_macro_components_are_not_called_inline(self) -> None:
        event = {"release_components": [
            {"actual": "0.3%", "forecast": "0.2%"},
            {"actual": "2.4%", "forecast": "2.5%"},
        ]}
        self.assertEqual(macro_release_status(event), "mixed")

    def test_mixed_inflation_keeps_both_market_interpretations(self) -> None:
        result = propose_mechanisms({"event_id": "ppi-mixed", "event_family": "inflation_mixed_release"})
        self.assertEqual(len(result["candidates"]), 2)
        effects = {(item["expected"]["usd"], item["expected"]["gold"]) for item in result["candidates"]}
        self.assertEqual(effects, {("bullish", "bearish"), ("bearish", "bullish")})
        self.assertTrue(all(item["primary"] for item in result["candidates"]))

    def test_inflation_upside_separates_immediate_pressure_from_later_hedge(self) -> None:
        result = propose_mechanisms({"event_id": "cpi-hot", "event_family": "inflation_upside_surprise"})
        immediate, later = result["candidates"]
        self.assertEqual(immediate["expected"]["gold"], "bearish")
        self.assertEqual(immediate["horizon"], "immediate_to_intraday")
        self.assertEqual(later["expected"]["gold"], "bullish")
        self.assertTrue(later["conditional"])
        self.assertIn("real_yields_not_rising", later["required_evidence"])

    def test_weak_labor_keeps_orderly_dovish_and_liquidity_stress_paths(self) -> None:
        result = propose_mechanisms({"event_id": "nfp-weak", "event_family": "growth_labor_downside_surprise"})
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(result["candidates"][0]["expected"]["gold"], "bullish")
        self.assertEqual(result["candidates"][1]["expected"]["usd"], "bullish")
        self.assertTrue(result["candidates"][1]["conditional"])

    def test_every_candidate_is_advisory_only(self) -> None:
        families = [
            "yield_shock_up", "yield_shock_down", "monetary_hawkish", "monetary_dovish",
            "geopolitical_escalation", "geopolitical_deescalation", "liquidity_stress",
            "oil_supply_tightening", "oil_supply_relief",
            "inflation_upside_surprise", "inflation_downside_surprise",
            "growth_labor_upside_surprise", "growth_labor_downside_surprise",
        ]
        for family in families:
            with self.subTest(family=family):
                result = propose_mechanisms({"event_id": family, "event_family": family})
                for candidate in result["candidates"]:
                    self.assertFalse(any(candidate["permissions"].values()))
                    self.assertFalse(candidate["confirmation_allowed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
