from __future__ import annotations

import unittest

from market_reasoning_engine import (
    build_reasoning_state,
    effective_leg,
    location_cluster,
    hypothesis_controls,
    opportunity_state,
    ordered_route,
    normalize_structural_point_a,
    timeframe_context,
    decision_geometry,
)


def branch(direction: str, target: float) -> dict:
    return {
        "direction": direction,
        "status": "observe",
        "score": 99 if direction == "bullish" else 1,
        "point_b": {"name": "B", "price": target},
        "route_checkpoints": [{"name": "FTA", "price": 4358.0 if direction == "bullish" else 4320.0}],
        "conditions": {"confirmation": "confirm", "invalidation": "invalidate"},
    }


class MarketReasoningTests(unittest.TestCase):
    def test_approaching_local_support_creates_early_counter_watch(self):
        bearish = branch("bearish", 4110.0)
        bearish["point_a"] = {"name": "D1 source", "price": 4227.0}
        state = build_reasoning_state(
            objects=[
                {"name": "H1 bullish FVG", "kind": "fvg", "timeframe": "H1", "directional_role": "support", "low": 4155.0, "high": 4159.0, "state": "untouched"},
                {"name": "H1 bearish FVG", "kind": "fvg", "timeframe": "H1", "directional_role": "resistance", "low": 4180.0, "high": 4190.0, "state": "untouched"},
            ],
            session_data={},
            order_flow={"timeframes": {"H1": {
                "delivery_direction": "bearish", "delivery_state": "active",
                "active_leg": {"direction": "bearish", "confirmed": True},
            }}},
            branches=[bearish, branch("bullish", 4227.0)], primary=bearish,
            transition={"state": "active", "switch_confirmed": False},
            volume_context={}, news_context={}, price=4165.0,
        )
        candidate = state["counter_candidate"]
        self.assertEqual(candidate["direction"], "bullish")
        self.assertEqual(candidate["state"], "watching")
        self.assertEqual(candidate["opportunity_stage"], "approaching")
        self.assertEqual(candidate["point_a"]["price"], 4159.0)
        self.assertEqual(state["counter_candidate_opportunity"]["state"], "early_attention")
        self.assertIsNone(state["counter_candidate_opportunity"]["progress_to_fta"])

    def test_wide_htf_location_alone_does_not_create_early_counter_watch(self):
        bearish = branch("bearish", 4110.0)
        state = build_reasoning_state(
            objects=[{"name": "W1 bullish FVG", "kind": "fvg", "timeframe": "W1", "directional_role": "support", "low": 4120.0, "high": 4311.0, "state": "untouched"}],
            session_data={}, order_flow={"timeframes": {"H1": {"active_leg": {"direction": "bearish"}}}},
            branches=[bearish, branch("bullish", 4400.0)], primary=bearish,
            transition={"state": "active", "switch_confirmed": False},
            volume_context={}, news_context={}, price=4200.0,
        )
        self.assertIsNone(state["counter_candidate"])

    def test_early_counter_watch_requires_a_balanced_reaction_target(self):
        bearish = branch("bearish", 4110.0)
        state = build_reasoning_state(
            objects=[
                {"name": "H1 bullish FVG", "kind": "fvg", "timeframe": "H1", "directional_role": "support", "low": 4155.0, "high": 4159.0, "state": "untouched"},
            ],
            session_data={},
            order_flow={"timeframes": {"H1": {
                "delivery_direction": "bearish", "delivery_state": "active",
                "active_leg": {"direction": "bearish", "confirmed": True},
            }}},
            branches=[bearish, branch("bullish", 4227.0)], primary=bearish,
            transition={"state": "active", "switch_confirmed": False},
            volume_context={}, news_context={}, price=4165.0,
        )
        self.assertIsNone(state["counter_candidate"])

    def test_nested_wide_zones_form_ordered_reaction_geometry(self):
        cluster = location_cluster([
            {"name": "W1 bullish FVG", "kind": "fvg", "timeframe": "W1", "directional_role": "support", "low": 4120.37, "high": 4311.16},
            {"name": "D1 bullish FVG", "kind": "fvg", "timeframe": "D1", "directional_role": "support", "low": 4106.50, "high": 4223.51},
            {"name": "H4 bearish FVG", "kind": "fvg", "timeframe": "H4", "directional_role": "resistance", "low": 4149.90, "high": 4166.06},
            {"name": "H1 bullish FVG", "kind": "fvg", "timeframe": "H1", "directional_role": "support", "low": 4155.92, "high": 4159.13},
        ], {}, 4158.87)
        geometry = decision_geometry(cluster, "bearish", 4158.87)
        self.assertEqual(geometry["active_execution_zone"]["timeframe"], "H1")
        self.assertEqual(geometry["next_boundary"]["boundary_type"], "CE")
        self.assertAlmostEqual(geometry["next_boundary"]["price"], 4157.525)
        self.assertEqual(
            [(item["timeframe"], item["price"]) for item in geometry["htf_backstops"]],
            [("W1", 4120.37), ("D1", 4106.5)],
        )
        w1 = next(item for item in geometry["zones"] if item["timeframe"] == "W1")
        self.assertEqual(w1["ce_state"], "passed")
        self.assertEqual(w1["far_edge_state"], "ahead")
        self.assertEqual(geometry["reaction_status"], "location_only_pending_closed_confirmation")

    def test_broken_weekly_delivery_cannot_define_external_narrative(self):
        order_flow = {"timeframes": {
            "W1": {
                "delivery_direction": "bullish", "delivery_state": "broken",
                "current_phase": "delivery_broken",
                "structural_structure": {"bias": "bearish", "state": "protected"},
                "internal_structure": {"bias": "bullish", "state": "protected"},
            },
            "D1": {
                "delivery_direction": "bearish", "delivery_state": "active",
                "current_phase": "continuation",
                "structural_structure": {"bias": "bearish", "state": "protected"},
                "internal_structure": {"bias": "bearish", "state": "protected"},
            },
            "H4": {
                "delivery_direction": "bearish", "delivery_state": "active",
                "current_phase": "pullback_or_transition",
                "structural_structure": {"bias": "transition", "state": "unconfirmed"},
                "internal_structure": {"bias": "transition", "state": "unconfirmed"},
            },
            "H1": {
                "delivery_direction": "bearish", "delivery_state": "active",
                "current_phase": "pullback_or_transition",
                "structural_structure": {"bias": "bearish", "state": "broken"},
                "internal_structure": {"bias": "transition", "state": "unconfirmed"},
            },
        }}
        result = timeframe_context(order_flow, "bearish", {
            "direction": "bullish", "leg_role": "counter_candidate",
        })
        self.assertEqual(result["external_narrative"], "bearish")
        self.assertEqual(result["external_narrative_source"], "protected_structural_bias")
        self.assertEqual(result["frames"]["W1"]["delivery_state"], "broken")
        self.assertEqual(result["frames"]["W1"]["relation_to_primary"], "aligned")
        self.assertEqual(result["frames"]["W1"]["delivery_relation_to_primary"], "opposed")
        self.assertEqual(result["nested_counter"]["status"], "nested_not_reversal")
        self.assertIn("bullish counter_candidate", result["interpretation"])

    def test_structural_point_a_uses_rejection_extreme_not_zone_edge(self):
        point = normalize_structural_point_a({
            "name": "D1 bearish FVG", "timeframe": "D1",
            "low": 4185.39, "high": 4254.59, "price": 4254.59,
            "native_confirmation": {
                "confirmed": True,
                "rejection_candle": {"time": "2026-10-02T00:00:00+00:00", "high": 4227.42},
            },
        }, "bearish")
        self.assertEqual(point["price"], 4227.42)
        self.assertEqual(point["source_zone"]["low"], 4185.39)
        self.assertEqual(point["anchor_type"], "rejection_high")

    def test_h4_closed_rejection_becomes_causal_point_a(self):
        objects = [{
            "name": "H4 bearish FVG", "kind": "fvg", "timeframe": "H4",
            "low": 4357.0, "high": 4360.0,
            "native_confirmation": {
                "confirmed": True, "rejection_direction": "bearish",
                "rejection_candle": {"time": "2026-09-22T22:00:00+00:00", "high": 4361.0},
            },
        }]
        order_flow = {"timeframes": {"H1": {"active_leg": {
            "direction": "bullish", "confirmed": True, "start_time": "2026-09-22T10:00:00+00:00"
        }}}}
        leg = effective_leg(objects, order_flow)
        self.assertEqual(leg["kind"], "htf_reaction")
        self.assertEqual(leg["direction"], "bearish")
        self.assertEqual(leg["point_a"]["price"], 4361.0)

    def test_active_leg_does_not_replace_locked_primary(self):
        bearish, bullish = branch("bearish", 4291.0), branch("bullish", 4383.0)
        state = build_reasoning_state(
            objects=[], session_data={},
            order_flow={"timeframes": {"H1": {"active_leg": {"direction": "bullish"}}}},
            branches=[bullish, bearish], primary=bearish,
            transition={"state": "alternative_testing", "switch_confirmed": False},
            volume_context={}, news_context={}, price=4340.0,
        )
        self.assertEqual(state["primary"]["direction"], "bearish")

    def test_location_cluster_stays_local(self):
        objects = [
            {"name": "H1 FVG", "kind": "fvg", "timeframe": "H1", "low": 4323.0, "high": 4327.0},
            {"name": "PDL", "kind": "key_level", "timeframe": "D1", "low": 4322.8, "high": 4322.8},
            {"name": "wide unrelated", "kind": "fvg", "timeframe": "D1", "low": 4333.0, "high": 4355.0},
        ]
        cluster = location_cluster(objects, {}, 4325.0)
        self.assertIsNotNone(cluster)
        self.assertLess(cluster["high"] - cluster["low"], 16.0)
        self.assertIn("H1 FVG", cluster["name"])

    def test_wide_bullish_zone_reports_depth_ce_and_far_edge(self):
        cluster = location_cluster([{
            "name": "W1 bullish FVG", "kind": "fvg", "timeframe": "W1",
            "directional_role": "support", "low": 4120.0, "high": 4320.0,
        }], {}, 4150.0)
        position = cluster["zone_position"]
        self.assertEqual(position["ce_0_5"], 4220.0)
        self.assertEqual(position["far_edge"], 4120.0)
        self.assertEqual(position["depth_state"], "beyond_ce")
        self.assertAlmostEqual(position["penetration"], 0.85)

    def test_nested_daily_zone_remains_visible_inside_weekly_zone(self):
        cluster = location_cluster([
            {"name": "W1 bullish FVG", "kind": "fvg", "timeframe": "W1", "directional_role": "support", "low": 4120.0, "high": 4320.0},
            {"name": "D1 bullish FVG", "kind": "fvg", "timeframe": "D1", "directional_role": "support", "low": 4130.0, "high": 4170.0},
        ], {}, 4150.0)
        stack = cluster["zone_stack"]
        self.assertEqual([item["timeframe"] for item in stack], ["W1", "D1"])
        self.assertEqual(stack[0]["far_edge"], 4120.0)
        self.assertEqual(stack[1]["far_edge"], 4130.0)

    def test_timeframe_hierarchy_separates_structure_from_delivery(self):
        bearish = branch("bearish", 4110.91)
        bearish["point_a"] = {"name": "H4 source", "price": 4300.42}
        frames = {}
        for tf, structure, delivery in (
            ("W1", "bullish", "bearish"),
            ("D1", "bullish", "bearish"),
            ("H4", "bearish", "bearish"),
            ("H1", "bearish", "bearish"),
        ):
            frames[tf] = {
                "delivery_direction": delivery, "current_phase": "continuation",
                "structural_structure": {"bias": structure, "state": "protected"},
                "internal_structure": {"bias": delivery},
            }
        state = build_reasoning_state(
            [], {}, {"timeframes": frames, "timeframe_sync": {"weekly_narrative": "bullish"}},
            [bearish, branch("bullish", 4275.33)], bearish,
            {"state": "active", "switch_confirmed": False}, {}, {}, 4150.0,
        )
        hierarchy = state["timeframe_hierarchy"]
        self.assertEqual(hierarchy["external_narrative"], "bullish")
        self.assertEqual(hierarchy["dominant_delivery"], "bearish")
        self.assertEqual(hierarchy["frames"]["D1"]["structural_narrative"], "bullish")
        self.assertEqual(hierarchy["frames"]["D1"]["delivery_direction"], "bearish")

    def test_causality_does_not_call_pending_h1_confirmation_verified(self):
        bearish = branch("bearish", 4110.91)
        bearish["point_a"] = {
            "name": "H4 bearish FVG", "timeframe": "H4", "price": 4300.42,
            "confirmation": "native closed rejection", "confirmed_at": "2026-09-28T08:00:00+00:00",
        }
        bearish["evidence"] = [{"code": "OPEN_DRAW", "text": "PDL", "weight": 15}]
        state = build_reasoning_state(
            [], {}, {"timeframes": {"H1": {"delivery_direction": "bearish"}}},
            [bearish, branch("bullish", 4275.33)], bearish,
            {"state": "active", "switch_confirmed": False},
            {"relation_to_primary": "unconfirmed"}, {"role": "none"}, 4150.0,
        )
        cause = state["primary"]["causality"]
        self.assertEqual(cause["source"]["status"], "observed")
        self.assertEqual(cause["lower_tf_confirmation"]["status"], "pending")
        self.assertEqual(cause["status"], "forming")
        self.assertIn("volume=unconfirmed", cause["conflicts"])

    def test_current_wide_zone_is_not_future_fta(self):
        bearish = {
            "direction": "bearish",
            "route_checkpoints": [{
                "name": "W1 bullish FVG", "low": 4120.37, "high": 4311.16,
                "price": 4311.16, "status": "checkpoint",
            }],
            "point_b": {"name": "PWL", "price": 4235.32},
        }
        route = ordered_route(bearish, 4268.49)
        self.assertEqual(len(route), 1)
        self.assertEqual(route[0]["name"], "PWL")
        self.assertEqual(route[0]["route_role"], "POINT_B")

    def test_half_delivered_a_to_b_is_late_even_inside_htf_zone(self):
        result = opportunity_state(
            {"point_a": {"price": 4303.40}},
            "testing",
            [{"name": "PWL", "price": 4235.32, "route_role": "POINT_B"}],
            4268.49,
        )
        self.assertEqual(result["state"], "late")
        self.assertAlmostEqual(result["progress_to_fta"], 0.513, places=3)

    def test_target_inside_current_wide_zone_does_not_reuse_old_edge(self):
        primary = {
            "direction": "bearish", "point_b": {"name": "PWL", "price": 4235.32},
            "conditions": {
                "confirmation": "IF H1 accepts below 4311.16000, THEN continuation",
                "invalidation": "IF H1 accepts above 4300.98000, THEN downgrade",
            },
        }
        alternative = {"conditions": {"confirmation": "IF H1 accepts above 4308.47000, THEN continuation"}}
        controls = hypothesis_controls(
            primary, alternative,
            {"name": "W1 bullish FVG", "low": 4120.37, "high": 4311.16},
            4268.49,
        )
        self.assertNotIn("below 4311.16000", controls["confirmation"])
        self.assertEqual(controls["transition_band"]["low"], 4300.98)
        self.assertEqual(controls["transition_band"]["high"], 4308.47)

    def test_primary_structural_leg_and_counter_leg_are_separate(self):
        bearish = branch("bearish", 4110.91)
        bearish["point_a"] = {"name": "H4 bearish FVG", "price": 4300.42, "confirmed_at": "2026-09-28T08:00:00+00:00"}
        objects = [{
            "name": "H1 bearish FVG", "kind": "fvg", "timeframe": "H1",
            "directional_role": "resistance", "low": 4181.55, "high": 4188.0,
            "state": "untouched", "validity_status": "valid",
        }]
        order_flow = {"timeframes": {"H1": {
            "delivery_direction": "bearish",
            "current_phase": "counter_move",
            "active_leg": {
                "direction": "bearish", "confirmed": True,
                "start_time": "2026-09-28T08:00:00+00:00",
                "anchor": {"type": "high", "price": 4300.42, "time": "2026-09-28T08:00:00+00:00"},
            },
            "internal_structure": {
                "bias": "bullish",
                "recent_pivots": [{"type": "low", "price": 4110.91, "time": "2026-09-29T08:00:00+00:00"}],
            },
        }}}
        state = build_reasoning_state(
            objects=objects, session_data={}, order_flow=order_flow,
            branches=[bearish, branch("bullish", 4275.33)], primary=bearish,
            transition={"state": "active", "switch_confirmed": False},
            volume_context={}, news_context={}, price=4153.0,
        )
        self.assertEqual(state["primary_structural_leg"]["direction"], "bearish")
        self.assertEqual(state["primary_structural_leg"]["point_a"]["price"], 4300.42)
        self.assertEqual(state["active_counter_leg"]["direction"], "bullish")
        self.assertEqual(state["active_counter_leg"]["point_a"]["price"], 4110.91)
        self.assertEqual(state["active_counter_leg"]["minimum_target"]["name"], "H1 bearish FVG")

    def test_counter_leg_point_a_does_not_drift_to_new_internal_pivot(self):
        bearish = branch("bearish", 4110.91)
        bearish["point_a"] = {"name": "H4 bearish FVG", "price": 4300.42}
        objects = [{
            "name": "H1 bearish FVG", "kind": "fvg", "timeframe": "H1",
            "directional_role": "resistance", "low": 4181.55, "high": 4188.0,
            "state": "untouched", "validity_status": "valid",
        }]

        def flow(pivot: float, time: str) -> dict:
            return {"timeframes": {"H1": {
                "delivery_direction": "bearish", "current_phase": "counter_move",
                "active_leg": {"direction": "bearish", "confirmed": True},
                "internal_structure": {
                    "bias": "bullish",
                    "recent_pivots": [{"type": "low", "price": pivot, "time": time}],
                },
            }}}

        first = build_reasoning_state(
            objects, {}, flow(4110.91, "2026-09-29T08:00:00+00:00"),
            [bearish, branch("bullish", 4275.33)], bearish,
            {"state": "active", "switch_confirmed": False}, {}, {}, 4153.0,
        )
        second = build_reasoning_state(
            objects, {}, flow(4148.75, "2026-09-29T14:00:00+00:00"),
            [bearish, branch("bullish", 4275.33)], bearish,
            {"state": "active", "switch_confirmed": False}, {}, {}, 4160.0,
            previous_state=first,
        )
        self.assertEqual(second["active_counter_leg"]["point_a"]["price"], 4110.91)
        self.assertEqual(second["active_counter_leg"]["lifecycle_id"], first["active_counter_leg"]["lifecycle_id"])

    def test_point_b_beyond_price_is_marked_reached_not_untouched(self):
        bearish = {
            "direction": "bearish", "route_checkpoints": [],
            "point_b": {"name": "PDL", "price": 4110.91, "state": "untouched"},
        }
        route = ordered_route(bearish, 4105.0)
        self.assertEqual(route[-1]["route_role"], "POINT_B")
        self.assertEqual(route[-1]["status"], "reached")
        self.assertEqual(route[-1]["state"], "reached")

    def test_post_pullback_continuation_resets_execution_timing_not_structural_a(self):
        bearish = branch("bearish", 4110.91)
        bearish["point_a"] = {"name": "H4 bearish FVG", "price": 4300.42}
        previous = {
            "active_counter_leg": {
                "lifecycle_id": "bullish-reaction-1",
                "parent_lifecycle_id": "placeholder",
                "direction": "bullish",
                "state": "reaction_building",
                "point_a": {"name": "reaction low", "price": 4110.91},
            }
        }
        # Match the real parent id generated for the unchanged structural leg.
        initial = build_reasoning_state(
            [], {}, {"timeframes": {"H1": {
                "delivery_direction": "bearish", "current_phase": "counter_move",
                "active_leg": {"direction": "bearish", "confirmed": True},
                "internal_structure": {"bias": "bullish", "recent_pivots": [
                    {"type": "low", "price": 4110.91, "time": "2026-09-29T08:00:00+00:00"}
                ]},
            }}}, [bearish, branch("bullish", 4275.33)], bearish,
            {"state": "active", "switch_confirmed": False}, {}, {}, 4150.0,
        )
        previous["active_counter_leg"]["parent_lifecycle_id"] = initial["primary_structural_leg"]["lifecycle_id"]
        resumed = build_reasoning_state(
            [], {}, {"timeframes": {"H1": {
                "delivery_direction": "bearish", "current_phase": "delivery",
                "active_leg": {
                    "direction": "bearish", "confirmed": True,
                    "start_time": "2026-09-29T16:00:00+00:00",
                    "anchor": {"type": "high", "price": 4176.46, "time": "2026-09-29T16:00:00+00:00"},
                },
                "internal_structure": {"bias": "bearish", "recent_pivots": []},
            }}}, [bearish, branch("bullish", 4275.33)], bearish,
            {"state": "active", "switch_confirmed": False}, {}, {}, 4160.0,
            previous_state=previous,
        )
        self.assertEqual(resumed["primary_structural_leg"]["point_a"]["price"], 4300.42)
        self.assertIsNone(resumed["active_counter_leg"])
        self.assertEqual(resumed["continuation_setup"]["point_a"]["price"], 4176.46)
        self.assertEqual(resumed["opportunity"], resumed["continuation_opportunity"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
