from __future__ import annotations

import unittest

from scenario_state import find_route_checkpoints, hypothesis_conditions


class RouteCheckpointTests(unittest.TestCase):
    def test_groups_levels_at_same_price(self) -> None:
        objects = [
            {"kind": "key_level", "name": "DO", "low": 4379.95, "state": "accepted_below"},
            {"kind": "key_level", "name": "WO", "low": 4379.95, "state": "accepted_below"},
            {"kind": "key_level", "name": "PDH", "low": 4399.67, "state": "untouched"},
        ]
        checkpoints = find_route_checkpoints(objects, "bullish", 4363.97, 4399.67)
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0]["name"], "DO / WO")
        self.assertEqual(checkpoints[0]["status"], "decision")
        self.assertTrue(checkpoints[0]["requires_acceptance"])

    def test_passed_level_is_not_unresolved(self) -> None:
        objects = [{"kind": "key_level", "name": "DO", "low": 4379.95, "state": "accepted_above"}]
        checkpoint = find_route_checkpoints(objects, "bullish", 4363.97, 4399.67)[0]
        self.assertEqual(checkpoint["status"], "passed")
        self.assertFalse(checkpoint["requires_acceptance"])

    def test_checkpoint_builds_if_then_condition(self) -> None:
        checkpoint = {"name": "DO / WO", "price": 4379.95, "state": "accepted_below"}
        result = hypothesis_conditions(
            "bullish", {"name": "PDH", "price": 4399.67}, None, "none", None, checkpoint,
        )
        self.assertIn("DO / WO 4379.95000", result["expectation"])
        self.assertIn("accepts above 4379.95000", result["confirmation"])

    def test_full_route_labels_first_unresolved_as_fta(self) -> None:
        objects = [
            {"kind": "fvg", "name": "H1 bearish FVG", "timeframe": "H1", "low": 4370.0, "high": 4372.0, "directional_role": "resistance", "state": "untouched", "validity_status": "valid"},
            {"kind": "key_level", "name": "WO", "low": 4379.95, "high": 4379.95, "state": "accepted_below"},
            {"kind": "volume_imbalance", "name": "H4 bearish VI", "timeframe": "H4", "low": 4385.0, "high": 4387.0, "directional_role": "resistance", "state": "untouched", "validity_status": "valid"},
        ]
        checkpoints = find_route_checkpoints(objects, "bullish", 4360.0, 4399.67)
        self.assertEqual([item["route_role"] for item in checkpoints], ["FTA", "TA", "TA"])
        self.assertEqual(checkpoints[0]["kind"], "fvg")
        self.assertEqual(checkpoints[2]["kind"], "volume_imbalance")

    def test_supporting_fvg_is_not_a_bullish_trouble_area(self) -> None:
        objects = [
            {"kind": "fvg", "name": "H1 bullish FVG", "timeframe": "H1", "low": 4370.0, "high": 4372.0, "directional_role": "support", "state": "untouched", "validity_status": "valid"},
        ]
        self.assertEqual(find_route_checkpoints(objects, "bullish", 4360.0, 4399.67), [])

    def test_m15_fvg_is_hidden_from_structural_route(self) -> None:
        objects = [
            {"kind": "fvg", "name": "M15 bearish FVG", "timeframe": "M15", "low": 4370.0, "high": 4372.0, "directional_role": "resistance", "state": "untouched", "validity_status": "valid"},
            {"kind": "key_level", "name": "WO", "low": 4379.95, "high": 4379.95, "state": "accepted_below"},
        ]
        checkpoints = find_route_checkpoints(objects, "bullish", 4360.0, 4399.67)
        self.assertEqual([item["name"] for item in checkpoints], ["WO"])

    def test_h1_zone_nested_in_h4_uses_h4_boundary(self) -> None:
        objects = [
            {"kind": "fvg", "name": "H1 bearish FVG", "timeframe": "H1", "low": 4370.0, "high": 4372.0, "directional_role": "resistance", "state": "untouched", "validity_status": "valid"},
            {"kind": "fvg", "name": "H4 bearish FVG", "timeframe": "H4", "low": 4369.0, "high": 4373.0, "directional_role": "resistance", "state": "untouched", "validity_status": "valid"},
        ]
        checkpoints = find_route_checkpoints(objects, "bullish", 4360.0, 4399.67)
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0]["name"], "H4 bearish FVG")

    def test_tiny_h1_volume_imbalance_is_execution_detail(self) -> None:
        objects = [
            {"kind": "volume_imbalance", "name": "H1 bearish Volume Imbalance", "timeframe": "H1", "low": 4344.75, "high": 4344.85, "directional_role": "resistance", "state": "untouched", "validity_status": "valid"},
            {"kind": "key_level", "name": "DO", "low": 4348.31, "high": 4348.31, "state": "accepted_below"},
        ]
        checkpoints = find_route_checkpoints(objects, "bullish", 4319.16, 4383.39)
        self.assertEqual([item["name"] for item in checkpoints], ["DO"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
