from __future__ import annotations

import unittest

from trade_idea_engine import build_ideas, usd_relation


class TradeIdeaEngineTests(unittest.TestCase):
    def scenario(self) -> dict:
        return {
            "current_price": 4268.49,
            "usd_context": {"available": True, "direction": "bearish", "strength": -20, "agreement": {"agreeing": 3, "total": 4}},
            "market_state": {
                "primary": {
                    "direction": "bearish", "point_b": {"name": "PWL", "price": 4235.32},
                    "route": [], "confirmation": "H1 bearish holds", "invalidation": "above 4300",
                },
                "alternative": {"direction": "bullish", "trigger": "above 4308", "point_b": {"name": "PDH", "price": 4369}},
                "active_leg": {"confirmation": "H4 rejection closed"},
                "opportunity": {"state": "late"},
                "current_location": {"name": "W1 bullish FVG", "interaction": "inside"},
                "volume": {"explanation": "volume unconfirmed", "relation_to_primary": "unconfirmed"},
                "news": {"role": "scheduled_risk"},
            },
        }

    def test_builds_continuation_pullback_and_reversal(self) -> None:
        states = {"objects": [{
            "kind": "fvg", "name": "H1 bearish FVG", "timeframe": "H1",
            "directional_role": "resistance", "low": 4298.0, "high": 4301.0,
            "state": "untouched",
        }]}
        ranges = {"timeframes": {"H1": {"selected_ranges": [{
            "status": "active", "fibonacci": {"0.5": 4288.0}, "end": {"time": "2026-09-24T08:00:00+00:00"},
        }]}}}
        ideas = build_ideas(self.scenario(), states, ranges)
        self.assertEqual([item["type"] for item in ideas], [
            "primary_continuation", "nested_counter_move", "conditional_reversal",
        ])
        self.assertEqual(ideas[1]["direction"], "bullish")
        self.assertEqual(ideas[1]["state"], "forming")
        self.assertTrue(ideas[1]["objectives"])

    def test_usd_breadth_is_not_claimed_as_direct_dxy_read(self) -> None:
        relation = usd_relation({"available": True, "direction": "bearish", "strength": -20}, "bullish")
        self.assertEqual(relation["role"], "supports")
        self.assertFalse(relation["direct_dxy_zone_read"])
        self.assertEqual(relation["source_type"], "FX breadth proxy")

    def test_ideas_keep_structural_and_counter_leg_attribution_separate(self) -> None:
        scenario = self.scenario()
        scenario["market_state"].update({
            "primary_structural_leg": {
                "lifecycle_id": "primary-1",
                "confirmation": "H4 bearish rejection initiated structural delivery",
            },
            "active_counter_leg": {
                "lifecycle_id": "counter-1",
                "confirmation": "H1 bullish displacement confirmed the reaction",
                "invalidation": "below 4110.91",
                "route": [{"name": "H1 bearish FVG", "price": 4181.55, "route_role": "FTA"}],
            },
        })
        ideas = build_ideas(scenario, {"objects": []}, {"timeframes": {}})
        self.assertEqual(ideas[0]["source_leg_id"], "primary-1")
        self.assertIn("H4 bearish rejection", " ".join(ideas[0]["because"]))
        self.assertNotIn("H1 bullish displacement", " ".join(ideas[0]["because"]))
        self.assertEqual(ideas[1]["source_leg_id"], "counter-1")
        self.assertIn("H1 bullish displacement", ideas[1]["confirmation"])
        self.assertEqual(ideas[1]["objectives"][0]["name"], "H1 bearish FVG")


if __name__ == "__main__":
    unittest.main(verbosity=2)
