from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from alert_policy import fingerprint, policy_decision, policy_snapshot


def scenario(
    direction: str = "bullish",
    status: str = "building",
    score: int = 68,
    target: float = 4442.98,
    decision_state: str = "approaching",
    reaction_state: str = "swept",
    driver: dict | None = None,
    checkpoints: list[dict] | None = None,
    opportunity: str | None = None,
) -> dict:
    primary = {
        "direction": direction,
        "status": status,
        "score": score,
        "point_b": {"name": "PWH", "price": target},
        "next_decision_zone": {"name": "H4 bearish FVG", "low": 4400.25, "high": 4405.73},
        "next_decision_state": decision_state,
        "current_reaction_zone": {
            "name": "H1 bullish FVG", "low": 4358.10, "high": 4381.69,
            "h1_state": reaction_state,
        },
        "pullback_zone": {"name": "H1 bullish FVG", "low": 4325.06, "high": 4343.68},
        "route_checkpoints": checkpoints or [],
    }
    result = {"symbol": "GOLD.pro", "primary_scenario": primary, "driver_context": driver or {"available": False}}
    if opportunity is not None:
        result["market_state"] = {"opportunity": {"state": opportunity}}
    return result


def with_counter(value: dict, direction: str = "bullish") -> dict:
    market = value.setdefault("market_state", {})
    market["active_counter_leg"] = {
        "lifecycle_id": "counter-1", "leg_role": "active_counter",
        "direction": direction,
    }
    return value


def state(value: dict, last_active: dict | None = None, fingerprint: str | None = None) -> dict:
    snap = policy_snapshot(value)
    return {
        "snapshot": snap,
        "last_active_snapshot": last_active if last_active is not None else (snap if snap["active"] else None),
        "last_notified_fingerprint": fingerprint,
    }


def raw(*types: str) -> dict:
    return {"events": [{"type": item, "severity": "important", "text": item} for item in types]}


class AlertPolicyTests(unittest.TestCase):
    def decide(self, previous: dict, current: dict, *events: str) -> dict:
        return policy_decision(previous, current, raw(*events))[0]

    def test_first_run_builds_baseline_without_alert(self) -> None:
        result = self.decide({}, scenario(), "PRIMARY_STATUS_CHANGED")
        self.assertFalse(result["should_notify"])
        self.assertEqual(result["reason_codes"], ["POLICY_INITIALIZED"])

    def test_unchanged_scenario_is_silent(self) -> None:
        current = scenario()
        result = self.decide(state(current), current)
        self.assertFalse(result["should_notify"])

    def test_small_score_change_is_context_only(self) -> None:
        result = self.decide(state(scenario(score=68)), scenario(score=72))
        self.assertFalse(result["should_notify"])

    def test_direction_change_is_critical(self) -> None:
        result = self.decide(
            state(scenario(direction="bullish")), scenario(direction="bearish"),
            "PRIMARY_DIRECTION_CHANGED", "POINT_B_CHANGED",
        )
        self.assertTrue(result["should_notify"])
        self.assertEqual(result["notification_type"], "DIRECTION_CHANGED")
        self.assertEqual(result["priority"], "critical")

    def test_status_promotion_notifies(self) -> None:
        result = self.decide(
            state(scenario(status="observe")), scenario(status="building"),
            "PRIMARY_STATUS_CHANGED",
        )
        self.assertTrue(result["should_notify"])
        self.assertEqual(result["notification_type"], "STATUS_PROMOTED")

    def test_status_downgrade_does_not_notify(self) -> None:
        result = self.decide(
            state(scenario(status="building")), scenario(status="observe"),
            "PRIMARY_STATUS_CHANGED",
        )
        self.assertFalse(result["should_notify"])

    def test_decision_zone_testing_notifies(self) -> None:
        result = self.decide(
            state(scenario(decision_state="approaching")), scenario(decision_state="testing"),
            "DECISION_STATE_CHANGED",
        )
        self.assertTrue(result["should_notify"])
        self.assertEqual(result["notification_type"], "DECISION_EVENT")

    def test_target_change_notifies(self) -> None:
        result = self.decide(
            state(scenario(target=4442.98)), scenario(target=4455.63), "POINT_B_CHANGED",
        )
        self.assertTrue(result["should_notify"])
        self.assertEqual(result["notification_type"], "TARGET_CHANGED")

    def test_new_reaction_notifies_without_raw_monitor_event(self) -> None:
        result = self.decide(
            state(with_counter(scenario(reaction_state="untouched"))),
            with_counter(scenario(reaction_state="swept")),
        )
        self.assertTrue(result["should_notify"])
        self.assertEqual(result["notification_type"], "REACTION_EVENT")

    def test_active_to_silent_collapses_to_one_pause(self) -> None:
        previous = state(scenario())
        current = scenario(status="silent", score=0)
        result = self.decide(
            previous, current, "PRIMARY_STATUS_CHANGED", "PRIMARY_SCORE_SHIFT",
            "POINT_B_CHANGED", "DECISION_ZONE_CHANGED", "PULLBACK_ZONE_CHANGED",
        )
        self.assertTrue(result["should_notify"])
        self.assertEqual(result["notification_type"], "SCENARIO_PAUSED")

    def test_same_scenario_resume_is_suppressed_as_flapping(self) -> None:
        active = scenario()
        active_snapshot = policy_snapshot(active)
        silent = scenario(status="silent", score=0)
        previous = state(silent, last_active=active_snapshot)
        result = self.decide(previous, active, "PRIMARY_STATUS_CHANGED", "POINT_B_CHANGED")
        self.assertFalse(result["should_notify"])
        self.assertIn("FLAP_RESUME_SUPPRESSED", result["reason_codes"])

    def test_changed_scenario_resume_notifies(self) -> None:
        old_active = scenario(target=4442.98)
        silent = scenario(status="silent", score=0)
        previous = state(silent, last_active=policy_snapshot(old_active))
        result = self.decide(previous, scenario(target=4455.63), "PRIMARY_STATUS_CHANGED")
        self.assertTrue(result["should_notify"])
        self.assertEqual(result["notification_type"], "SCENARIO_RESUMED")

    def test_news_release_notifies_when_technical_scenario_is_unchanged(self) -> None:
        old = scenario()
        driver = {
            "available": True, "title": "Core CPI m/m", "status": "released",
            "effect": "bearish", "reaction_status": "confirmed", "actual": "0.4%",
            "importance": "high", "event_time_utc": "2026-09-21T12:30:00+00:00",
        }
        current = scenario(driver=driver)
        result = self.decide(state(old), current, "NEWS_RELEASED")
        self.assertTrue(result["should_notify"])
        self.assertEqual(result["notification_type"], "NEWS_DRIVER")

    def test_status_change_inside_same_route_cluster_is_silent(self) -> None:
        old = scenario(checkpoints=[{"name": "DO / WO", "price": 4379.95, "status": "decision"}])
        current = scenario(checkpoints=[{"name": "DO / WO", "price": 4379.95, "status": "passed"}])
        result = self.decide(state(old), current, "ROUTE_CHECKPOINTS_CHANGED")
        self.assertFalse(result["should_notify"])

    def test_reordering_levels_inside_same_cluster_is_silent(self) -> None:
        levels_a = [
            {"name": "PDL", "price": 4322.81, "low": 4322.81, "high": 4322.81, "status": "checkpoint", "route_role": "FTA"},
            {"name": "H1 bullish FVG", "low": 4323.52, "high": 4327.77, "price": 4327.77, "status": "checkpoint", "route_role": "TA"},
        ]
        levels_b = [
            {"name": "H1 bullish FVG", "low": 4323.52, "high": 4327.77, "price": 4327.77, "status": "checkpoint", "route_role": "FTA"},
            {"name": "PDL", "price": 4322.81, "low": 4322.81, "high": 4322.81, "status": "checkpoint", "route_role": "TA"},
        ]
        result = self.decide(state(scenario(checkpoints=levels_a)), scenario(checkpoints=levels_b), "ROUTE_CHECKPOINTS_CHANGED")
        self.assertFalse(result["should_notify"])

    def test_same_first_cluster_is_not_notified_twice(self) -> None:
        first = scenario(checkpoints=[{"name": "PDL", "price": 4322.81, "low": 4322.81, "high": 4322.81}])
        second = scenario(checkpoints=[{"name": "H1 bullish FVG", "price": 4327.77, "low": 4323.52, "high": 4327.77}])
        prior = state(first)
        prior["last_notified_route_context"] = {
            "direction": "bullish", "target": ("PWH", 4442.98),
            "first_cluster": {"low": 4322.81, "high": 4322.81, "members": ["PDL"]},
        }
        prior["last_route_alert_at_utc"] = "2026-09-22T12:00:00+00:00"
        result = policy_decision(prior, second, raw("ROUTE_CHECKPOINTS_CHANGED"), now=datetime(2026, 9, 22, 12, 20, tzinfo=timezone.utc))[0]
        self.assertFalse(result["should_notify"])
        self.assertIn("SAME_ROUTE_CLUSTER_SUPPRESSED", result["reason_codes"])

    def test_different_route_cluster_is_held_by_cooldown(self) -> None:
        first = scenario(checkpoints=[{"name": "PDL", "price": 4322.81, "low": 4322.81, "high": 4322.81}])
        second = scenario(checkpoints=[{"name": "H4 FVG", "price": 4380.0, "low": 4380.0, "high": 4385.0}])
        prior = state(first)
        prior["last_notified_route_context"] = {
            "direction": "bullish", "target": ("PWH", 4442.98),
            "first_cluster": {"low": 4322.81, "high": 4322.81, "members": ["PDL"]},
        }
        prior["last_route_alert_at_utc"] = "2026-09-22T12:00:00+00:00"
        result = policy_decision(prior, second, raw("ROUTE_CHECKPOINTS_CHANGED"), now=datetime(2026, 9, 22, 12, 5, tzinfo=timezone.utc))[0]
        self.assertFalse(result["should_notify"])
        self.assertIn("ROUTE_COOLDOWN_SUPPRESSED", result["reason_codes"])

    def test_intrabar_trade_idea_target_churn_is_silent(self) -> None:
        current = scenario(direction="bearish", reaction_state="testing")
        result = self.decide(
            state(current), current, "TRADE_IDEA_TARGETS_CHANGED",
        )
        self.assertFalse(result["should_notify"])
        self.assertEqual(result["notification_type"], "NONE")

    def test_reaction_direction_is_opposite_primary_delivery(self) -> None:
        old = with_counter(scenario(direction="bearish", reaction_state="untouched"))
        current = with_counter(scenario(direction="bearish", reaction_state="reaction_building"))
        result = self.decide(state(old), current)
        self.assertTrue(result["should_notify"])
        self.assertEqual(result["notification_type"], "REACTION_EVENT")
        self.assertEqual(result["reaction_direction"], "bullish")

    def test_same_lifecycle_alert_is_suppressed_for_one_hour(self) -> None:
        old = with_counter(scenario(direction="bearish", reaction_state="untouched"))
        current = with_counter(scenario(direction="bearish", reaction_state="reaction_building"))
        first, next_state = policy_decision(
            state(old), current, raw(),
            now=datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(first["should_notify"])

        # A local target/FVG changes two minutes later, but the lifecycle does not.
        repeated, _ = policy_decision(
            next_state, current, raw("TRADE_IDEA_TARGETS_CHANGED"),
            now=datetime(2026, 9, 28, 12, 2, tzinfo=timezone.utc),
        )
        self.assertFalse(repeated["should_notify"])

    def test_primary_zone_change_does_not_fabricate_opposite_reaction(self) -> None:
        old = scenario(direction="bearish", reaction_state="untouched")
        current = scenario(direction="bearish", reaction_state="reaction_building")
        result = self.decide(state(old), current)
        self.assertFalse(result["should_notify"])
        self.assertIsNone(result["reaction_direction"])

    def test_new_early_counter_candidate_calls_user_before_the_move(self) -> None:
        old = scenario(direction="bearish", opportunity="late")
        current = scenario(direction="bearish", opportunity="early_attention")
        current["market_state"]["counter_candidate"] = {
            "lifecycle_id": "early-counter-1",
            "leg_role": "counter_candidate",
            "direction": "bullish",
            "state": "watching",
        }
        result = self.decide(state(old), current, "TRADE_IDEA_STATE_CHANGED")
        self.assertTrue(result["should_notify"])
        self.assertEqual(result["notification_type"], "EARLY_ATTENTION")
        self.assertEqual(result["reaction_direction"], "bullish")
        self.assertEqual(result["current_snapshot"]["opportunity_lifecycle"], "watch")

    def test_late_and_missed_share_non_actionable_fingerprint(self) -> None:
        late = policy_snapshot(scenario(opportunity="late"))
        missed = policy_snapshot(scenario(opportunity="missed"))
        self.assertEqual(late["opportunity_lifecycle"], "non_actionable")
        self.assertEqual(missed["opportunity_lifecycle"], "non_actionable")
        self.assertEqual(fingerprint("REACTION_EVENT", late), fingerprint("REACTION_EVENT", missed))

    def test_reaction_fingerprint_ignores_intrabar_context_churn(self) -> None:
        first = scenario(opportunity="setup_forming")
        first["market_state"].update({
            "route_id": "local-route-a",
            "pa_state": {"stage": "testing"},
        })
        second = scenario(opportunity="setup_forming", decision_state="accepted")
        second["market_state"].update({
            "route_id": "local-route-b",
            "pa_state": {"stage": "reaction_building"},
        })
        self.assertEqual(
            fingerprint("REACTION_EVENT", policy_snapshot(first)),
            fingerprint("REACTION_EVENT", policy_snapshot(second)),
        )
        self.assertEqual(
            policy_snapshot(first)["alert_lifecycle_key"],
            policy_snapshot(second)["alert_lifecycle_key"],
        )

    def test_structural_target_change_opens_new_alert_lifecycle(self) -> None:
        first = policy_snapshot(scenario(opportunity="setup_forming", target=4110.91))
        second = policy_snapshot(scenario(opportunity="setup_forming", target=4019.30))
        self.assertNotEqual(first["alert_lifecycle_key"], second["alert_lifecycle_key"])
        self.assertNotEqual(
            fingerprint("REACTION_EVENT", first),
            fingerprint("REACTION_EVENT", second),
        )

    def test_new_counter_leg_opens_new_lifecycle_with_same_structural_target(self) -> None:
        first_scenario = scenario(direction="bearish", opportunity="setup_forming")
        first_scenario["market_state"].update({
            "active_counter_leg": {"lifecycle_id": "counter-a", "leg_role": "active_counter", "direction": "bullish"},
        })
        second_scenario = scenario(direction="bearish", opportunity="setup_forming")
        second_scenario["market_state"].update({
            "active_counter_leg": {"lifecycle_id": "counter-b", "leg_role": "active_counter", "direction": "bullish"},
        })
        first = policy_snapshot(first_scenario)
        second = policy_snapshot(second_scenario)
        self.assertNotEqual(first["alert_lifecycle_key"], second["alert_lifecycle_key"])
        self.assertNotEqual(fingerprint("REACTION_EVENT", first), fingerprint("REACTION_EVENT", second))

    def test_setup_forming_to_late_is_logged_but_not_notified(self) -> None:
        result = self.decide(
            state(scenario(opportunity="setup_forming")),
            scenario(opportunity="late"),
            "TRADE_IDEA_STATE_CHANGED",
        )
        self.assertFalse(result["should_notify"])
        self.assertIn("NON_ACTIONABLE_OPPORTUNITY_SUPPRESSED", result["reason_codes"])
        self.assertEqual(result["current_snapshot"]["opportunity_state"], "late")
        self.assertEqual(result["current_snapshot"]["opportunity_lifecycle"], "non_actionable")

    def test_late_missed_late_churn_never_notifies(self) -> None:
        previous = state(scenario(opportunity="late"))
        for value in ("missed", "late"):
            result, previous = policy_decision(
                previous,
                scenario(opportunity=value),
                raw("TRADE_IDEA_STATE_CHANGED"),
            )
            self.assertFalse(result["should_notify"])
            self.assertIn("NON_ACTIONABLE_OPPORTUNITY_SUPPRESSED", result["reason_codes"])

    def test_forming_late_forming_sequence_alerts_only_once(self) -> None:
        watch = scenario(opportunity="early_attention")
        forming = scenario(opportunity="setup_forming")
        first, current_state = policy_decision(
            state(watch), forming, raw("TRADE_IDEA_STATE_CHANGED"),
            now=datetime(2026, 10, 2, 8, 50, tzinfo=timezone.utc),
        )
        self.assertTrue(first["should_notify"])
        self.assertEqual(first["reason_codes"], ["OPPORTUNITY_LIFECYCLE_PROMOTED"])

        late, current_state = policy_decision(
            current_state, scenario(opportunity="late"), raw("TRADE_IDEA_STATE_CHANGED"),
            now=datetime(2026, 10, 2, 8, 57, tzinfo=timezone.utc),
        )
        self.assertFalse(late["should_notify"])

        repeated, _ = policy_decision(
            current_state, forming, raw("TRADE_IDEA_STATE_CHANGED"),
            now=datetime(2026, 10, 2, 8, 59, tzinfo=timezone.utc),
        )
        self.assertFalse(repeated["should_notify"])
        self.assertIn("DUPLICATE_ALERT_SUPPRESSED", repeated["reason_codes"])

    def test_initial_detection_already_late_is_silent(self) -> None:
        result = self.decide(
            state(scenario(opportunity="observe")),
            scenario(opportunity="late"),
            "TRADE_IDEA_STATE_CHANGED",
        )
        self.assertFalse(result["should_notify"])
        self.assertIn("NON_ACTIONABLE_OPPORTUNITY_SUPPRESSED", result["reason_codes"])

    def test_news_release_still_notifies_when_opportunity_is_late(self) -> None:
        previous = scenario(opportunity="setup_forming")
        driver = {
            "available": True, "title": "Core PCE", "status": "released",
            "effect": "bearish", "reaction_status": "confirmed", "actual": "0.3%",
            "importance": "high", "event_time_utc": "2026-10-02T12:30:00+00:00",
        }
        current = scenario(opportunity="late", driver=driver)
        result = self.decide(state(previous), current, "TRADE_IDEA_STATE_CHANGED", "NEWS_RELEASED")
        self.assertTrue(result["should_notify"])
        self.assertEqual(result["notification_type"], "NEWS_DRIVER")



if __name__ == "__main__":
    unittest.main(verbosity=2)
