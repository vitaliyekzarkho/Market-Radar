from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import run_radar


class RadarSchedulerTests(unittest.TestCase):
    def test_subprocess_environment_forces_utf8(self) -> None:
        original = os.environ.get("PYTHONUTF8")
        environment = run_radar.utf8_subprocess_environment()
        self.assertEqual(environment["PYTHONUTF8"], "1")
        self.assertEqual(environment["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(os.environ.get("PYTHONUTF8"), original)

    def test_last_closed_marker_ignores_open_candle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "H1.csv"
            path.write_text(
                "time,utc_time,is_closed,open,high,low,close\n"
                "1,2026-09-19T13:00:00+00:00,true,1,2,0,1\n"
                "2,2026-09-19T14:00:00+00:00,false,1,2,0,1\n",
                encoding="utf-8",
            )
            self.assertEqual(
                run_radar.last_closed_bar_marker(path),
                "2026-09-19T13:00:00+00:00",
            )

    def test_same_h1_marker_skips_heavy_layer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for output in run_radar.HEAVY_OUTPUTS:
                target = root / output
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("{}", encoding="utf-8")
            state = root / run_radar.SCHEDULE_STATE_FILE
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(json.dumps({"last_heavy_h1_closed_bar": "bar-1"}), encoding="utf-8")
            required, reason = run_radar.heavy_recalculation_required(root, "bar-1")
            self.assertFalse(required)
            self.assertEqual(reason, "same closed H1 candle")

    def test_new_h1_marker_runs_heavy_layer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for output in run_radar.HEAVY_OUTPUTS:
                target = root / output
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("{}", encoding="utf-8")
            state = root / run_radar.SCHEDULE_STATE_FILE
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(json.dumps({"last_heavy_h1_closed_bar": "bar-1"}), encoding="utf-8")
            required, reason = run_radar.heavy_recalculation_required(root, "bar-2")
            self.assertTrue(required)
            self.assertEqual(reason, "new closed H1 candle")

    def test_missing_output_forces_heavy_layer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            required, reason = run_radar.heavy_recalculation_required(Path(directory), "bar-1")
            self.assertTrue(required)
            self.assertEqual(reason, "missing derived output")

    def test_force_flag_runs_heavy_layer(self) -> None:
        required, reason = run_radar.heavy_recalculation_required(Path("."), "bar-1", force=True)
        self.assertTrue(required)
        self.assertEqual(reason, "forced")

    def test_weekend_failure_is_recognized_as_market_closed(self) -> None:
        saturday = datetime(2026, 9, 19, tzinfo=timezone.utc)
        self.assertTrue(run_radar.recognized_market_closed("anything", saturday))

    def test_weekday_unknown_failure_is_not_hidden(self) -> None:
        monday = datetime(2026, 9, 21, tzinfo=timezone.utc)
        self.assertFalse(run_radar.recognized_market_closed("connection lost", monday))

    def test_known_closed_signature_is_recognized(self) -> None:
        monday = datetime(2026, 9, 21, tzinfo=timezone.utc)
        output = "Trade allowed: False\nRuntimeError: Invalid broker UTC offset: -13"
        self.assertTrue(run_radar.recognized_market_closed(output, monday))


if __name__ == "__main__":
    unittest.main(verbosity=2)
