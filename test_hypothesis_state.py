import unittest

import scenario_state as engine


def branch(direction, score, codes=(), status="observe", point_a=True, point_b=True):
    return {
        "direction": direction,
        "score": score,
        "status": status,
        "point_a": {"name": "origin"} if point_a else None,
        "point_b": {"name": "target", "price": 100.0} if point_b else None,
        "evidence": [
            {"code": code, "weight": 1, "direction": direction}
            for code in codes
        ],
    }


class HypothesisStateTests(unittest.TestCase):
    def test_score_leader_cannot_flip_live_primary(self):
        bearish = branch("bearish", 38)
        bullish = branch("bullish", 45, ("H1_OPPOSING_FVG_TEST",))

        primary, transition = engine.select_primary_stateful(
            [bullish, bearish], "bearish", "bearish"
        )

        self.assertEqual(primary["direction"], "bearish")
        self.assertEqual(transition["state"], "alternative_testing")
        self.assertFalse(transition["switch_confirmed"])

    def test_equal_scores_do_not_depend_on_list_order(self):
        bearish = branch("bearish", 38)
        bullish = branch("bullish", 38)

        for order in ([bullish, bearish], [bearish, bullish]):
            primary, _ = engine.select_primary_stateful(order, "bearish", "bullish")
            self.assertEqual(primary["direction"], "bullish")

    def test_closed_h1_transition_and_invalidation_can_switch(self):
        bearish = branch("bearish", 38, ("H1_COUNTER_TRANSITION",))
        bullish = branch("bullish", 60, ("H1_OPPOSING_FVG_LOST",))

        primary, transition = engine.select_primary_stateful(
            [bullish, bearish], "bearish", "bearish"
        )

        self.assertEqual(primary["direction"], "bullish")
        self.assertEqual(transition["state"], "confirmed_transition")
        self.assertTrue(transition["switch_confirmed"])

    def test_target_inside_wide_bearish_zone_uses_entry_edge(self):
        conditions = engine.hypothesis_conditions(
            "bearish",
            {"name": "PWL", "price": 4235.32},
            {"name": "W1 bullish FVG", "low": 4120.37, "high": 4311.16},
            "approaching",
            None,
        )

        self.assertIn("below 4311.16000", conditions["confirmation"])
        self.assertNotIn("below 4120.37000", conditions["confirmation"])

    def test_low_volume_counter_move_supports_primary(self):
        context = engine.volume_hypothesis_context({
            "source": "broker tick volume",
            "timeframes": {
                "H1": {
                    "available": True,
                    "direction": "bullish",
                    "state": "low_volume_pullback",
                    "tick_volume_ratio": 0.45,
                    "spread_ratio": 0.74,
                    "effort_result": "neutral",
                }
            },
        }, "bearish")

        self.assertEqual(context["relation_to_primary"], "supports")
        self.assertFalse(context["scoring_applied"])

    def test_h1_volume_cannot_confirm_h4_source_by_itself(self):
        context = engine.volume_hypothesis_context({
            "source": "broker tick volume",
            "timeframes": {"H1": {
                "available": True, "direction": "bearish", "state": "expansion",
                "tick_volume_ratio": 1.8, "spread_ratio": 1.5, "effort_result": "aligned",
                "observation_id": "H1:2026-09-28T12:00:00Z",
            }},
        }, "bearish", {"point_a": {"timeframe": "H4", "price": 4300.42}})
        self.assertEqual(context["relation_to_primary"], "supports_execution")
        self.assertEqual(context["observations"][0]["leg_attribution"], "primary_structural_leg")
        self.assertEqual(context["observations"][0]["confirmation_scope"], "execution_confirmation")

    def test_volume_is_attributed_separately_to_structural_and_counter_legs(self):
        base = {
            "available": True,
            "source_timeframe": "H4",
            "observations": [
                {
                    "timeframe": "H4", "direction": "bearish", "state": "expansion",
                    "effort_result": "aligned", "sequence": "expanding_participation",
                },
                {
                    "timeframe": "H1", "direction": "bullish", "state": "expansion",
                    "effort_result": "aligned", "sequence": "expanding_participation",
                },
            ],
        }
        market = {
            "primary": {"direction": "bearish"},
            "primary_structural_leg": {
                "direction": "bearish", "point_a": {"timeframe": "H4"},
            },
            "counter_candidate": {"direction": "bullish"},
        }
        result = engine.volume_leg_attribution(base, market)

        self.assertEqual(result["relation_to_primary"], "supports")
        self.assertEqual(result["relation_to_counter"], "supports")
        self.assertEqual(
            result["leg_evaluations"]["primary_structural_leg"]["source_timeframe"],
            "H4",
        )
        h1 = next(item for item in result["observations"] if item["timeframe"] == "H1")
        self.assertEqual(h1["leg_attribution"], "active_counter_leg")
        self.assertFalse(result["entry_permission"])
        self.assertFalse(result["direction_switch_permission"])
        self.assertFalse(result["telegram_eligible"])

    def test_stalled_primary_effort_supports_counter_watch_not_entry(self):
        base = {
            "available": True,
            "source_timeframe": "H1",
            "observations": [{
                "timeframe": "H1", "direction": "bearish",
                "state": "possible_absorption", "effort_result": "effort_without_result",
                "sequence": "absorption_building",
            }],
        }
        market = {
            "primary": {"direction": "bearish"},
            "primary_structural_leg": {
                "direction": "bearish", "point_a": {"timeframe": "H1"},
            },
            "counter_candidate": {"direction": "bullish"},
        }
        result = engine.volume_leg_attribution(base, market)

        self.assertEqual(result["relation_to_primary"], "warning")
        self.assertEqual(result["relation_to_counter"], "supports_candidate")
        self.assertFalse(result["entry_permission"])

    def test_h4_volume_is_context_only_for_d1_structural_source(self):
        base = {
            "available": True,
            "source_timeframe": "D1",
            "observations": [{
                "timeframe": "H4", "direction": "bearish", "state": "expansion",
                "effort_result": "aligned", "sequence": "expanding_participation",
            }],
        }
        market = {
            "primary": {"direction": "bearish"},
            "primary_structural_leg": {
                "direction": "bearish", "point_a": {"timeframe": "D1"},
            },
        }
        result = engine.volume_leg_attribution(base, market)
        self.assertEqual(result["relation_to_primary"], "context_only")
        self.assertEqual(
            result["leg_evaluations"]["primary_structural_leg"]["source_timeframe"],
            "D1",
        )

    def test_balanced_opposite_candle_is_not_a_volume_conflict(self):
        result = engine.volume_leg_attribution({
            "available": True,
            "source_timeframe": "D1",
            "observations": [{
                "timeframe": "D1", "direction": "bullish", "state": "balanced",
                "effort_result": "neutral", "sequence": "stable",
            }],
        }, {
            "primary": {"direction": "bearish"},
            "primary_structural_leg": {
                "direction": "bearish", "point_a": {"timeframe": "D1"},
            },
        })
        self.assertEqual(result["relation_to_primary"], "unconfirmed")

    def test_stale_volume_frames_are_disclosed_and_excluded(self):
        context = engine.volume_hypothesis_context({
            "timeframes": {
                "H1": {"available": False, "reason": "STALE_CLOSED_BAR"},
                "D1": {
                    "available": True, "direction": "bearish", "state": "balanced",
                    "effort_result": "neutral", "sequence": "stable",
                },
            },
        }, "bearish", {"point_a": {"timeframe": "D1"}})
        self.assertEqual(context["data_quality"], "partial_stale")
        self.assertEqual(context["stale_timeframes"], ["H1"])
        self.assertEqual(len(context["observations"]), 1)

    def test_structural_invalidation_does_not_trail_intrabar_fvg(self):
        previous = branch("bearish", 45)
        previous["conditions"] = {
            "invalidation": "IF H1 accepts above 4161.95000, THEN downgrade the bearish branch"
        }
        current = branch("bearish", 45)
        current["conditions"] = {
            "invalidation": "IF H1 accepts above 4179.07000, THEN downgrade the bearish branch"
        }

        engine.freeze_structural_invalidation(
            current, previous, {"switch_confirmed": False}
        )

        self.assertEqual(
            current["conditions"]["invalidation"],
            previous["conditions"]["invalidation"],
        )
        self.assertEqual(
            current["conditions"]["dynamic_execution_invalidation"],
            "IF H1 accepts above 4179.07000, THEN downgrade the bearish branch",
        )
        self.assertTrue(current["structural_invalidation_locked"])

    def test_confirmed_transition_may_replace_invalidation(self):
        previous = branch("bearish", 45)
        previous["conditions"] = {
            "invalidation": "IF H1 accepts above 4161.95000, THEN downgrade the bearish branch"
        }
        current = branch("bearish", 45)
        current["conditions"] = {
            "invalidation": "IF H1 accepts above 4179.07000, THEN downgrade the bearish branch"
        }

        engine.freeze_structural_invalidation(
            current, previous, {"switch_confirmed": True}
        )

        self.assertIn("4179.07000", current["conditions"]["invalidation"])

    def test_structural_point_a_and_b_stay_fixed_before_target(self):
        previous = branch("bearish", 45)
        previous["point_a"] = {"name": "H4 bearish FVG", "price": 4300.42}
        previous["point_b"] = {"name": "PDL", "price": 4110.91}
        current = branch("bearish", 55)
        current["point_a"] = {"name": "H1 internal pivot", "price": 4148.75}
        current["point_b"] = {"name": "PML", "price": 4019.30}

        engine.freeze_structural_identity(
            current, previous, {"switch_confirmed": False}, price=4151.0,
        )

        self.assertEqual(current["point_a"], previous["point_a"])
        self.assertEqual(current["point_b"], previous["point_b"])
        self.assertTrue(current["structural_point_a_locked"])
        self.assertTrue(current["structural_point_b_locked"])

    def test_reached_point_b_allows_next_structural_leg(self):
        previous = branch("bearish", 45)
        previous["point_a"] = {"price": 4300.42}
        previous["point_b"] = {"name": "PDL", "price": 4110.91}
        current = branch("bearish", 55)
        current["point_a"] = {"price": 4148.75}
        current["point_b"] = {"name": "PML", "price": 4019.30}

        engine.freeze_structural_identity(
            current, previous, {"switch_confirmed": False}, price=4105.0,
        )

        self.assertEqual(current["point_a"]["price"], 4148.75)
        self.assertEqual(current["point_b"]["name"], "PML")


if __name__ == "__main__":
    unittest.main()
