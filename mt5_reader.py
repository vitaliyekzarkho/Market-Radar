from datetime import datetime, timezone

import MetaTrader5 as mt5
import pandas as pd


SYMBOL = "GOLD.pro"

TIMEFRAMES = {
    "W1": mt5.TIMEFRAME_W1,
    "D1": mt5.TIMEFRAME_D1,
    "H4": mt5.TIMEFRAME_H4,
    "H1": mt5.TIMEFRAME_H1,
    "M15": mt5.TIMEFRAME_M15,
}


def connect():
    if not mt5.initialize(r"C:\Program Files\OANDA TMS MT5 Terminal\terminal64.exe", timeout=15000):
        raise RuntimeError("OANDA MT5 connection failed")
    print("Connected to OANDA MT5: market-data reads only")


def detect_broker_utc_offset(symbol):
    tick = mt5.symbol_info_tick(symbol)

    if tick is None:
        raise RuntimeError(f"No tick data for {symbol}")

    computer_utc = datetime.now(timezone.utc)
    broker_time = datetime.fromtimestamp(tick.time, timezone.utc)

    offset_hours = round(
        (broker_time - computer_utc).total_seconds() / 3600
    )

    if not -12 <= offset_hours <= 14:
        raise RuntimeError(
            f"Invalid broker UTC offset: {offset_hours}"
        )

    return offset_hours


def get_rates(symbol, timeframe, offset_hours, count=500, start_pos=0):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, start_pos, count)

    if rates is None or len(rates) == 0:
        raise RuntimeError(
            f"No data for {symbol}: {mt5.last_error()}"
        )

    dataframe = pd.DataFrame(rates)

    dataframe["broker_time"] = pd.to_datetime(
        dataframe["time"],
        unit="s",
        utc=True,
    )

    dataframe["utc_time"] = (
        dataframe["broker_time"]
        - pd.Timedelta(hours=offset_hours)
    )

    return dataframe


def main():
    connect()

    try:
        symbol = mt5.symbol_info(SYMBOL)

        if symbol is None:
            raise RuntimeError(f"Symbol {SYMBOL} not found")

        if not symbol.visible:
            if not mt5.symbol_select(SYMBOL, True):
                raise RuntimeError(f"Cannot select {SYMBOL}")

        offset_hours = detect_broker_utc_offset(SYMBOL)
        tick = mt5.symbol_info_tick(SYMBOL)

        print(f"Symbol: {SYMBOL}")
        print(f"Bid: {tick.bid}")
        print(f"Ask: {tick.ask}")
        print(f"Broker UTC offset: {offset_hours:+d} hours")
        print(f"Computer UTC: {datetime.now(timezone.utc)}")

        for name, timeframe in TIMEFRAMES.items():
            data = get_rates(
                SYMBOL,
                timeframe,
                offset_hours,
            )
            last_bar = data.iloc[-1]

            print(
                f"{name}: {len(data)} bars | "
                f"UTC={last_bar['utc_time']} | "
                f"O={last_bar['open']} "
                f"H={last_bar['high']} "
                f"L={last_bar['low']} "
                f"C={last_bar['close']}"
            )

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
