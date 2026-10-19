from __future__ import annotations

import copy
import unittest

import event_monitor


def base_snapshot() -> dict:
    primary = {
        "direction": "bullish",
        "status": "building",
        "score": 68,
        "point_b": {"name": "PWH", "price": 4442.98},
        "decision_zone": {
            "name": "H4 bearish FVG",
            "low": 4400.25,
            "high": 4405.73,
        },
        "decision_state": "approaching",
        "pullback_zone": {
            "name": "H1 bullish FVG",
            "low": 4367.10,
            "high": 4377.38,
        },
        "confirmation": "H1 acceptance above 4405.73",
        "invalidation": "H1 acceptance below 4367.10",
    }
    return {
        "symbol": "GOLD.pro",
        "current_price": 4394.16,
        "global_bias": "bullish",
        "primary": primary,
        "branches": {
            "bullish": copy.deepcopy(primary),
            "bearish": {
                "direction": "bearish",
                "status": "observe",
                "score": 38,
            },
        },
    }


def event_types(before: dict, after: dict) -> set[str]:
    return {item["type"] for item in event_monitor.compare_snapshots(before, after)}


class EventMonitorTests(unittest.TestCase):
    def test_unchanged_snapshot_creates_no_event(self) -> None:
        before = base_snapshot()
        self.assertEqual(event_monitor.compare_snapshots(before, copy.deepcopy(before)), [])

    def test_primary_direction_and_status_change(self) -> None:
        before = base_snapshot()
        after = copy.deepcopy(before)
        after["primary"]["direction"] = "bearish"
        after["primary"]["status"] = "confirmed"
        types = event_types(before, after)
        self.assertIn("PRIMARY_DIRECTION_CHANGED", types)
        self.assertIn("PRIMARY_STATUS_CHANGED", types)

    def test_decision_zone_entry_is_important(self) -> None:
        before = base_snapshot()
        after = copy.deepcopy(before)
        after["primary"]["decision_state"] = "testing"
        events = event_monitor.compare_snapshots(before, after)
        match = next(item for item in events if item["type"] == "DECISION_STATE_CHANGED")
        self.assertEqual(match["severity"], "important")
        self.assertEqual(match["after"], "testing")

    def test_small_score_change_is_ignored(self) -> None:
        before = base_snapshot()
        after = copy.deepcopy(before)
        after["primary"]["score"] = 73
        self.assertNotIn("PRIMARY_SCORE_SHIFT", event_types(before, after))

    def test_material_score_change_is_detected(self) -> None:
        before = base_snapshot()
        after = copy.deepcopy(before)
        after["primary"]["score"] = 80
        self.assertIn("PRIMARY_SCORE_SHIFT", event_types(before, after))

    def test_pullback_zone_change_is_detected(self) -> None:
        before = base_snapshot()
        after = copy.deepcopy(before)
        after["primary"]["pullback_zone"] = {
            "name": "H1 bullish FVG",
            "low": 4378.39,
            "high": 4388.97,
        }
        self.assertIn("PULLBACK_ZONE_CHANGED", event_types(before, after))

    def test_alternative_branch_change_is_detected_without_primary_duplicate(self) -> None:
        before = base_snapshot()
        after = copy.deepcopy(before)
        after["branches"]["bearish"]["status"] = "building"
        events = event_monitor.compare_snapshots(before, after)
        branch_events = [item for item in events if item["type"] == "BRANCH_STATUS_CHANGED"]
        self.assertEqual(len(branch_events), 1)
        self.assertIn("Bearish", branch_events[0]["text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
