from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5

from mt5_reader import (
    SYMBOL,
    TIMEFRAMES,
    connect,
    detect_broker_utc_offset,
    get_rates,
)


OUTPUT_DIRECTORY = Path("data") / "raw" / "GOLD_pro"
BAR_COUNT = 500


def main():
    connect()

    try:
        account = mt5.account_info()

        if account is None:
            raise RuntimeError("MT5 account information unavailable")

        if account.trade_allowed:
            raise RuntimeError(
                "Safety stop: MT5 is not connected in read-only mode"
            )

        offset_hours = detect_broker_utc_offset(SYMBOL)
        snapshot_time = datetime.now(timezone.utc)

        OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)

        print(f"Snapshot UTC: {snapshot_time}")
        print(f"Broker UTC offset: {offset_hours:+d} hours")

        for name, timeframe in TIMEFRAMES.items():
            data = get_rates(
                SYMBOL,
                timeframe,
                offset_hours,
                BAR_COUNT,
            )

            data["is_closed"] = True
            data.loc[data.index[-1], "is_closed"] = False
            data["snapshot_utc"] = snapshot_time

            output_file = OUTPUT_DIRECTORY / f"{name}.csv"
            data.to_csv(output_file, index=False)

            print(
                f"Saved {name}: {len(data)} bars -> "
                f"{output_file}"
            )

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()