import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


DATA_DIRECTORY = Path("data") / "raw" / "GOLD_pro"
OUTPUT_DIRECTORY = Path("data") / "levels"
OUTPUT_FILE = OUTPUT_DIRECTORY / "GOLD_pro.json"


def load_data(timeframe):
    file_path = DATA_DIRECTORY / f"{timeframe}.csv"

    data = pd.read_csv(file_path)

    data["utc_time"] = pd.to_datetime(
        data["utc_time"],
        utc=True,
    )

    data["broker_time"] = pd.to_datetime(
        data["broker_time"],
        utc=True,
    )

    for column in [
        "open",
        "high",
        "low",
        "close",
    ]:
        data[column] = pd.to_numeric(
            data[column],
            errors="coerce",
        )

    data = data.dropna(
        subset=["open", "high", "low", "close"]
    )

    return data.sort_values(
        "broker_time"
    ).reset_index(drop=True)


def closed_bars(data):
    mask = (
        data["is_closed"]
        .astype(str)
        .str.lower()
        .eq("true")
    )

    return data.loc[mask].copy()


def make_level(
    name,
    price,
    source_time,
    current_price,
):
    distance = price - current_price

    distance_percent = (
        distance / current_price * 100
        if current_price
        else 0
    )

    location = (
        "above"
        if price > current_price
        else "below"
        if price < current_price
        else "at_price"
    )

    return {
        "name": name,
        "price": round(float(price), 5),
        "source_time": source_time.isoformat(),
        "location": location,
        "distance": round(float(distance), 5),
        "distance_percent": round(
            float(distance_percent),
            3,
        ),
    }


def previous_day_levels(
    daily,
    current_price,
):
    completed = closed_bars(daily)

    if completed.empty:
        raise RuntimeError(
            "No completed D1 candle"
        )

    previous_day = completed.iloc[-1]

    return {
        "PDH": make_level(
            "PDH",
            previous_day["high"],
            previous_day["broker_time"],
            current_price,
        ),
        "PDL": make_level(
            "PDL",
            previous_day["low"],
            previous_day["broker_time"],
            current_price,
        ),
    }


def previous_week_levels(
    weekly,
    current_price,
):
    completed = closed_bars(weekly)

    if completed.empty:
        raise RuntimeError(
            "No completed W1 candle"
        )

    previous_week = completed.iloc[-1]

    return {
        "PWH": make_level(
            "PWH",
            previous_week["high"],
            previous_week["broker_time"],
            current_price,
        ),
        "PWL": make_level(
            "PWL",
            previous_week["low"],
            previous_week["broker_time"],
            current_price,
        ),
    }


def previous_month_levels(
    daily,
    current_price,
):
    completed = closed_bars(daily)

    if completed.empty:
        raise RuntimeError(
            "No completed D1 candles"
        )

    latest_broker_time = daily.iloc[-1][
        "broker_time"
    ]

    current_year = latest_broker_time.year
    current_month = latest_broker_time.month

    previous_month_data = completed[
        (
            completed["broker_time"].dt.year
            < current_year
        )
        |
        (
            (
                completed["broker_time"].dt.year
                == current_year
            )
            &
            (
                completed["broker_time"].dt.month
                < current_month
            )
        )
    ].copy()

    if previous_month_data.empty:
        raise RuntimeError(
            "Previous month data unavailable"
        )

    last_previous_time = (
        previous_month_data.iloc[-1][
            "broker_time"
        ]
    )

    previous_year = last_previous_time.year
    previous_month = last_previous_time.month

    month_data = previous_month_data[
        (
            previous_month_data[
                "broker_time"
            ].dt.year
            == previous_year
        )
        &
        (
            previous_month_data[
                "broker_time"
            ].dt.month
            == previous_month
        )
    ]

    month_start = month_data.iloc[0][
        "broker_time"
    ]

    return {
        "PMH": make_level(
            "PMH",
            month_data["high"].max(),
            month_start,
            current_price,
        ),
        "PML": make_level(
            "PML",
            month_data["low"].min(),
            month_start,
            current_price,
        ),
    }


def current_period_opens(
    daily,
    weekly,
    current_price,
):
    current_day = daily.iloc[-1]
    current_week = weekly.iloc[-1]

    latest_time = daily.iloc[-1][
        "broker_time"
    ]

    current_month_data = daily[
        (
            daily["broker_time"].dt.year
            == latest_time.year
        )
        &
        (
            daily["broker_time"].dt.month
            == latest_time.month
        )
    ]

    result = {
        "DO": make_level(
            "DO",
            current_day["open"],
            current_day["broker_time"],
            current_price,
        ),
        "WO": make_level(
            "WO",
            current_week["open"],
            current_week["broker_time"],
            current_price,
        ),
    }

    if not current_month_data.empty:
        first_month_bar = (
            current_month_data.iloc[0]
        )

        result["MO"] = make_level(
            "MO",
            first_month_bar["open"],
            first_month_bar["broker_time"],
            current_price,
        )

    return result


def main():
    daily = load_data("D1")
    weekly = load_data("W1")
    m15 = load_data("M15")

    current_price = float(
        m15.iloc[-1]["close"]
    )

    levels = {}

    levels.update(
        previous_day_levels(
            daily,
            current_price,
        )
    )

    levels.update(
        previous_week_levels(
            weekly,
            current_price,
        )
    )

    levels.update(
        previous_month_levels(
            daily,
            current_price,
        )
    )

    levels.update(
        current_period_opens(
            daily,
            weekly,
            current_price,
        )
    )

    ordered_names = [
        "PMH",
        "PWH",
        "PDH",
        "MO",
        "WO",
        "DO",
        "PDL",
        "PWL",
        "PML",
    ]

    ordered_levels = {
        name: levels[name]
        for name in ordered_names
        if name in levels
    }

    result = {
        "symbol": "GOLD.pro",
        "generated_utc": (
            datetime.now(timezone.utc).isoformat()
        ),
        "current_price": current_price,
        "levels": ordered_levels,
        "purpose": (
            "Reference liquidity and key levels. "
            "Not standalone trade signals."
        ),
    }

    OUTPUT_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            result,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print(f"CURRENT_PRICE: {current_price}")

    for name, level in ordered_levels.items():
        print(
            f"{name}: {level['price']} | "
            f"{level['location']} | "
            f"distance="
            f"{level['distance_percent']}%"
        )

    print(f"LEVELS_SAVED: {OUTPUT_FILE}")
    print("KEY_LEVEL_ENGINE: PASSED")


if __name__ == "__main__":
    main()