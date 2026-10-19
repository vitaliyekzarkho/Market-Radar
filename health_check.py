from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5
import pandas as pd

from mt5_reader import (
    SYMBOL,
    TIMEFRAMES,
    connect,
    detect_broker_utc_offset,
)


DATA_DIRECTORY = Path("data") / "raw" / "GOLD_pro"

IRELAND_TIMEZONE = ZoneInfo("Europe/Dublin")
WORK_START_HOUR = 8
WORK_END_HOUR = 20

REQUIRED_COLUMNS = {
    "utc_time",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "is_closed",
}

PRICE_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
]


def monitoring_is_active():
    ireland_now = datetime.now(IRELAND_TIMEZONE)

    weekday_is_active = ireland_now.weekday() < 5

    hour_is_active = (
        WORK_START_HOUR
        <= ireland_now.hour
        < WORK_END_HOUR
    )

    return ireland_now, weekday_is_active and hour_is_active


def check_csv(timeframe_name):
    file_path = DATA_DIRECTORY / f"{timeframe_name}.csv"

    if not file_path.exists():
        return False, f"file missing: {file_path}"

    data = pd.read_csv(file_path)

    missing_columns = REQUIRED_COLUMNS - set(data.columns)

    if missing_columns:
        return False, (
            f"missing columns: {sorted(missing_columns)}"
        )

    if len(data) < 100:
        return False, f"insufficient bars: {len(data)}"

    data["utc_time"] = pd.to_datetime(
        data["utc_time"],
        utc=True,
        errors="coerce",
    )

    if data["utc_time"].isna().any():
        return False, "invalid UTC timestamps"

    if data["utc_time"].duplicated().any():
        return False, "duplicate timestamps"

    if not data["utc_time"].is_monotonic_increasing:
        return False, "timestamps are not chronological"

    if data[PRICE_COLUMNS].isna().any().any():
        return False, "missing OHLC values"

    invalid_candles = (
        (
            data["high"]
            < data[["open", "close", "low"]].max(axis=1)
        )
        |
        (
            data["low"]
            > data[["open", "close", "high"]].min(axis=1)
        )
    )

    if invalid_candles.any():
        return False, "invalid OHLC candle detected"

    closed_flags = (
        data["is_closed"]
        .astype(str)
        .str.lower()
    )

    if closed_flags.iloc[-1] != "false":
        return False, (
            "latest candle is not marked as forming"
        )

    if (closed_flags.iloc[:-1] != "true").any():
        return False, (
            "historical candle marked as forming"
        )

    return True, f"{len(data)} bars valid"


def main():
    connect()

    failures = []

    try:
        account = mt5.account_info()

        if account is None:
            raise RuntimeError(
                "Account information unavailable"
            )

        if account.trade_allowed:
            raise RuntimeError(
                "SAFETY FAILURE: account is not read-only"
            )

        offset_hours = detect_broker_utc_offset(SYMBOL)
        tick = mt5.symbol_info_tick(SYMBOL)

        if tick is None:
            raise RuntimeError("Live tick unavailable")

        broker_tick = datetime.fromtimestamp(
            tick.time,
            timezone.utc,
        )

        tick_utc = broker_tick - timedelta(
            hours=offset_hours
        )

        tick_age = abs(
            (
                datetime.now(timezone.utc)
                - tick_utc
            ).total_seconds()
        )

        ireland_now, monitoring_active = (
            monitoring_is_active()
        )

        print("READ_ONLY: PASS")
        print(f"BROKER_OFFSET: UTC{offset_hours:+d}")
        print(f"TICK_AGE_SECONDS: {tick_age:.1f}")
        print(f"IRELAND_TIME: {ireland_now}")
        print("MONITORING_WINDOW: 08:00-20:00")
        print(
            "MONITORING_ACTIVE: "
            f"{monitoring_active}"
        )

        if tick_age > 120:
            failures.append(
                f"Live quote is stale: "
                f"{tick_age:.1f} seconds"
            )

        for timeframe_name in TIMEFRAMES:
            passed, message = check_csv(
                timeframe_name
            )

            status = "PASS" if passed else "FAIL"

            print(
                f"{timeframe_name}: "
                f"{status} | {message}"
            )

            if not passed:
                failures.append(
                    f"{timeframe_name}: {message}"
                )

        if failures:
            print("\nHEALTH CHECK: FAILED")

            for failure in failures:
                print(f"- {failure}")

            raise SystemExit(1)

        print("\nHEALTH CHECK: PASSED")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()