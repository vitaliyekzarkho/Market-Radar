from __future__ import annotations

import argparse
import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_SYMBOL = "GOLD.pro"
DEFAULT_START = "2026-01-29"
DEFAULT_END = "2026-03-23"
DEFAULT_WARMUP_DAYS = 420


def parse_date(value: str, end_of_day: bool = False) -> datetime:
    parsed = datetime.strptime(value, "%Y-%m-%d").date()
    boundary = time(23, 59, 59) if end_of_day else time.min
    return datetime.combine(parsed, boundary, tzinfo=timezone.utc)


def atomic_csv(path: Path, data: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    data.to_csv(temporary, index=False)
    temporary.replace(path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def normalize_rates(rates: Any, broker_offset_hours: int = 2) -> pd.DataFrame:
    data = pd.DataFrame(rates)
    if data.empty:
        return data
    data["time"] = pd.to_numeric(data["time"], errors="coerce").astype("Int64")
    data = data.dropna(subset=["time", "open", "high", "low", "close"]).copy()
    data["time"] = data["time"].astype("int64")
    utc = pd.to_datetime(data["time"], unit="s", utc=True)
    broker = utc + pd.Timedelta(hours=broker_offset_hours)
    data.insert(1, "broker_time", broker.map(lambda value: value.isoformat()))
    data.insert(2, "utc_time", utc.map(lambda value: value.isoformat()))
    data["is_closed"] = True
    data["snapshot_utc"] = datetime.now(timezone.utc).isoformat()
    preferred = [
        "time", "open", "high", "low", "close", "tick_volume", "spread",
        "real_volume", "broker_time", "utc_time", "is_closed", "snapshot_utc",
    ]
    return data[[column for column in preferred if column in data.columns]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download an isolated MT5 history pack for deterministic MarketRadar replay."
    )
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--start", default=DEFAULT_START, help="Replay start date, YYYY-MM-DD.")
    parser.add_argument("--end", default=DEFAULT_END, help="Replay end date, YYYY-MM-DD.")
    parser.add_argument(
        "--warmup-days",
        type=int,
        default=DEFAULT_WARMUP_DAYS,
        help="History before the replay window used to establish W1/D1 structure.",
    )
    parser.add_argument(
        "--broker-offset-hours",
        type=int,
        default=2,
        help="Broker candle-time offset from UTC used by key-level calculations.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    replay_start = parse_date(args.start)
    replay_end = parse_date(args.end, end_of_day=True)
    if replay_end <= replay_start:
        raise ValueError("Replay end must be later than replay start")
    warmup_days = max(60, int(args.warmup_days))
    download_start = replay_start - timedelta(days=warmup_days)

    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 package is not installed in this Python environment") from exc

    timeframe_map = {
        "W1": mt5.TIMEFRAME_W1,
        "D1": mt5.TIMEFRAME_D1,
        "H4": mt5.TIMEFRAME_H4,
        "H1": mt5.TIMEFRAME_H1,
        "M15": mt5.TIMEFRAME_M15,
    }
    symbol_file = args.symbol.replace(".", "_")
    window_name = f"{args.start}_{args.end}"
    output_dir = Path("data") / "replay" / symbol_file / window_name / "raw"

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        if not mt5.symbol_select(args.symbol, True):
            raise RuntimeError(f"MT5 symbol_select failed for {args.symbol}: {mt5.last_error()}")

        files: dict[str, dict[str, Any]] = {}
        for name, timeframe in timeframe_map.items():
            rates = mt5.copy_rates_range(args.symbol, timeframe, download_start, replay_end)
            data = normalize_rates(rates, broker_offset_hours=args.broker_offset_hours)
            if data.empty:
                raise RuntimeError(
                    f"No {name} history returned for {args.symbol}: {mt5.last_error()}"
                )
            path = output_dir / f"{name}.csv"
            atomic_csv(path, data)
            files[name] = {
                "path": str(path),
                "rows": int(len(data)),
                "first_utc": str(data.iloc[0]["utc_time"]),
                "last_utc": str(data.iloc[-1]["utc_time"]),
            }
            print(
                f"REPLAY_DATA: {name:<3} | rows={len(data):>6} | "
                f"{data.iloc[0]['utc_time']} -> {data.iloc[-1]['utc_time']}"
            )
    finally:
        mt5.shutdown()

    manifest = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": args.symbol,
        "replay_window": {"start": args.start, "end": args.end},
        "download_window_utc": {
            "start": download_start.isoformat(),
            "end": replay_end.isoformat(),
        },
        "warmup_days": warmup_days,
        "broker_offset_hours": int(args.broker_offset_hours),
        "source": "MetaTrader5.copy_rates_range",
        "isolated_from_live_data": True,
        "files": files,
    }
    manifest_path = output_dir.parent / "manifest.json"
    atomic_json(manifest_path, manifest)
    print(f"REPLAY_MANIFEST: {manifest_path}")
    print("PREPARE_REPLAY_DATA: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
