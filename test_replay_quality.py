from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from replay_radar import evaluate_timeline, resolve_pack_root


def item(index: int, *, direction: str = "bearish", notify: bool = False, opportunity: str = "setup_forming", lifecycle: str = "leg-1", point_a: float = 4300.0) -> dict:
    return {
        "checkpoint_utc": f"2026-09-28T{index:02d}:00:00+00:00",
        "primary": {"direction": direction},
        "should_notify": notify,
        "notification_type": "REACTION_EVENT" if notify else "NONE",
        "alert_lifecycle_key": lifecycle,
        "opportunity": opportunity,
        "structural_leg": {"lifecycle_id": "structural-1", "point_a": {"name": "H4 source", "price": point_a}},
    }


class ReplayQualityTests(unittest.TestCase):
    def test_subrange_resolves_to_containing_replay_pack(self) -> None:
        with TemporaryDirectory() as temporary:
            pack = Path(temporary) / "2026-01-29_2026-03-23"
            pack.mkdir()
            (pack / "manifest.json").write_text("{}", encoding="utf-8")
            self.assertEqual(resolve_pack_root(Path(temporary), "2026-03-20", "2026-03-23"), pack)

    def test_clean_early_lifecycle_passes(self) -> None:
        metrics = evaluate_timeline([item(8), item(9, notify=True), item(10)])
        self.assertTrue(metrics["passed"])
        self.assertEqual(metrics["timely_alert_ratio"], 1.0)

    def test_post_fact_duplicate_and_point_a_drift_are_detected(self) -> None:
        metrics = evaluate_timeline([
            item(8, notify=True, opportunity="late"),
            item(9, notify=True, opportunity="missed", point_a=4310.0),
        ])
        self.assertFalse(metrics["passed"])
        self.assertEqual(metrics["post_fact_alerts"], 2)
        self.assertEqual(metrics["duplicate_lifecycle_alerts"], 1)
        self.assertEqual(metrics["structural_point_a_drift"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
