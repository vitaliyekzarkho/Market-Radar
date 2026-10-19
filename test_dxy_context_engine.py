from __future__ import annotations

import unittest

from dxy_context_engine import context_from_frames, detect_fvgs


class DxyContextTests(unittest.TestCase):
    def test_detects_direct_bullish_fvg_interaction(self) -> None:
        rows = [
            {"time": 1, "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0},
            {"time": 2, "open": 100.1, "high": 100.8, "low": 100.0, "close": 100.7},
            {"time": 3, "open": 100.6, "high": 101.0, "low": 100.5, "close": 100.8},
            {"time": 4, "open": 100.8, "high": 100.9, "low": 100.1, "close": 100.30},
        ]
        zones = detect_fvgs(rows, "D1")
        self.assertTrue(zones)
        self.assertEqual(zones[0]["direction"], "bullish")
        self.assertEqual(zones[0]["interaction"], "inside")

    def test_context_labels_direct_chart_source(self) -> None:
        rows = [
            {"time": index, "open": 100.0, "high": 100.2 + index, "low": 99.8 + index, "close": 100.0 + index}
            for index in range(6)
        ]
        result = context_from_frames({"D1": rows, "H4": rows, "H1": rows}, "DXY.pro")
        self.assertTrue(result["available"])
        self.assertEqual(result["source_type"], "direct DXY/USDX broker chart")


if __name__ == "__main__":
    unittest.main(verbosity=2)
