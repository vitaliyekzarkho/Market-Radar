from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from causal_replay_runner import run_replay_pack, synthetic_usd_breadth


EVENT = datetime(2026, 3, 9, 12, 7, tzinfo=timezone.utc)


def rows(direction: int, pair: bool = False) -> list[dict]:
    result = []
    price = 1.0 if pair else 100.0
    start = EVENT - timedelta(hours=6, minutes=7)
    for index in range(48):
        opened = start + timedelta(minutes=15 * index)
        change = direction * (0.001 if pair else 0.2)
        close = price + change
        result.append({
            "utc_time": opened.isoformat(), "open": price, "high": max(price, close),
            "low": min(price, close), "close": close, "tick_volume": 100,
            "is_closed": True,
        })
        price = close
    return result


def write_csv(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)


class CausalReplayRunnerTests(unittest.TestCase):
    def test_synthetic_breadth_inverts_quote_usd_pairs(self) -> None:
        data = {"EURUSD": rows(-1, True), "GBPUSD": rows(-1, True), "AUDUSD": rows(-1, True)}
        synthetic = synthetic_usd_breadth(data)
        self.assertGreater(synthetic[-1]["close"], synthetic[0]["close"])

    def test_synthetic_breadth_requires_three_pairs(self) -> None:
        with self.assertRaisesRegex(ValueError, "THREE_COMPONENTS"):
            synthetic_usd_breadth({"EURUSD": rows(-1, True), "GBPUSD": rows(-1, True)})

    def test_full_repeated_replay_writes_all_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary)
            raw = pack / "raw"
            event = {
                "event_id": "event-1", "title": "Trump says conflict will end soon",
                "event_family": "geopolitical_deescalation", "event_time_utc": EVENT.isoformat(),
                "fact_status": "verified", "source": "Official", "source_verified": True,
                "primary_source": True, "lifecycle_state": "reported_fact", "status": "released",
            }
            (pack / "historical_event.json").write_text(json.dumps(event), encoding="utf-8")
            downloads = []
            specs = [("gold", "GOLD.pro", 1), ("oil", "OILBRNT.pro", 1)]
            for role, symbol, direction in specs:
                path = raw / f"{role}__{symbol.replace('.', '_')}__M15.csv"
                write_csv(path, rows(direction))
                downloads.append({"role": role, "symbol": symbol})
            for pair in ("EURUSD", "GBPUSD", "AUDUSD"):
                role, symbol = f"usd_{pair.lower()}", f"{pair}.pro"
                path = raw / f"{role}__{symbol.replace('.', '_')}__M15.csv"
                write_csv(path, rows(-1, True))
                downloads.append({"role": role, "symbol": symbol})
            manifest = {"causal_replay_ready": True, "downloads": downloads}
            (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = run_replay_pack(pack, "repeated", 1)
            self.assertEqual(result["classification"], "priced_in")
            self.assertEqual(result["final_lifecycle"], "priced_in")
            self.assertFalse(result["driver_confirmation_allowed"])
            for name in (
                "reaction_windows.json", "mechanism_candidates.json", "causal_validation.json",
                "priced_in_evaluation.json", "historical_replay_result.json",
            ):
                self.assertTrue((pack / name).is_file(), name)

    def test_inline_macro_release_remains_context_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary)
            raw = pack / "raw"
            event = {
                "event_id": "cpi-inline", "title": "US CPI", "event_family": "inflation_upside_surprise",
                "event_time_utc": EVENT.isoformat(), "fact_status": "verified", "source": "BLS",
                "source_verified": True, "primary_source": True, "lifecycle_state": "reported_fact",
                "status": "released", "release_components": [
                    {"name": "headline_mom", "actual": "0.3%", "forecast": "0.3%"},
                    {"name": "core_mom", "actual": "0.2%", "forecast": "0.2%"},
                ],
            }
            (pack / "historical_event.json").write_text(json.dumps(event), encoding="utf-8")
            downloads = []
            for role, symbol, direction in (("gold", "GOLD.pro", 1), ("oil", "OILBRNT.pro", 1)):
                write_csv(raw / f"{role}__{symbol.replace('.', '_')}__M15.csv", rows(direction))
                downloads.append({"role": role, "symbol": symbol})
            for pair in ("EURUSD", "GBPUSD", "AUDUSD"):
                role, symbol = f"usd_{pair.lower()}", f"{pair}.pro"
                write_csv(raw / f"{role}__{symbol.replace('.', '_')}__M15.csv", rows(-1, True))
                downloads.append({"role": role, "symbol": symbol})
            (pack / "manifest.json").write_text(json.dumps({"causal_replay_ready": True, "downloads": downloads}), encoding="utf-8")
            result = run_replay_pack(pack, "new", 0)
            self.assertEqual(result["classification"], "no_directional_surprise")
            self.assertEqual(result["causal_role"], "context_only")
            self.assertEqual(result["final_lifecycle"], "reported_fact")
            self.assertIsNone(result["primary_mechanism"])
            self.assertFalse(result["driver_confirmation_allowed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
