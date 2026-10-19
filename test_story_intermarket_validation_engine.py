import unittest
from story_intermarket_validation_engine import evaluate_story


def report(moves, complete=True):
    return {"event_id": "diesel_story", "event_time_utc": "2026-10-09T18:46:00+00:00", "assets": {
        key: {"source": "test", "symbol": key, "post_event": {"post_60m": {
            "complete": complete, "available": True, "move_percent": move}}}
        for key, move in moves.items()}}


def story(expected):
    return {"story_id": "diesel_story", "mechanism_candidates": [{"mechanism_id": "m1", "expected": expected}]}


class ValidationTests(unittest.TestCase):
    def test_support_not_cause(self):
        result = evaluate_story(story({"oil": "bearish", "yields": "falling", "gold": "bullish"}),
                                report({"oil": -1, "yields": -0.2, "gold": 0.3}))
        self.assertEqual(result["mechanisms"][0]["status"], "supported")
        self.assertFalse(result["permissions"]["confirm_cause"])
        self.assertFalse(result["mechanisms"][0]["causality_confirmed"])

    def test_mixed(self):
        result = evaluate_story(story({"oil": "bearish", "gold": "bullish"}), report({"oil": -1, "gold": -0.2}))
        self.assertEqual(result["mechanisms"][0]["status"], "mixed")

    def test_missing_data_cannot_confirm(self):
        result = evaluate_story(story({"oil": "bearish", "yields": "falling"}), report({"oil": -1}))
        self.assertEqual(result["mechanisms"][0]["status"], "insufficient_data")

    def test_incomplete_window(self):
        result = evaluate_story(story({"oil": "bearish"}), report({"oil": -1}, complete=False))
        self.assertEqual(result["mechanisms"][0]["status"], "insufficient_data")

    def test_conditional_not_confirmed(self):
        result = evaluate_story(story({"gold": "conditional"}), report({"gold": 1}))
        self.assertEqual(result["mechanisms"][0]["status"], "insufficient_data")

    def test_mismatched_event(self):
        with self.assertRaises(ValueError):
            evaluate_story({"story_id": "other"}, report({"oil": -1}))

    def test_multiple_aliases_not_arbitrarily_selected(self):
        result = evaluate_story(story({"oil": "bearish"}), report({"wti": -1, "brent": -1}))
        self.assertEqual(result["mechanisms"][0]["status"], "insufficient_data")

    def test_zero_move_threshold(self):
        result = evaluate_story(story({"oil": "bearish"}), report({"oil": -0.001}), min_abs_move_percent=0.01)
        self.assertEqual(result["mechanisms"][0]["status"], "contradicted")


if __name__ == "__main__":
    unittest.main()
