import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


DATA_DIRECTORY = Path("data") / "raw" / "GOLD_pro"
OUTPUT_DIRECTORY = Path("data") / "features"
OUTPUT_FILE = OUTPUT_DIRECTORY / "GOLD_pro.json"

TIMEFRAMES = ["W1", "D1", "H4", "H1", "M15"]


def load_closed_bars(timeframe):
    file_path = DATA_DIRECTORY / f"{timeframe}.csv"

    data = pd.read_csv(file_path)

    data["utc_time"] = pd.to_datetime(
        data["utc_time"],
        utc=True,
    )

    closed = (
        data["is_closed"]
        .astype(str)
        .str.lower()
        .eq("true")
    )

    data = data.loc[closed].copy()
    data = data.sort_values("utc_time")
    data = data.reset_index(drop=True)

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "tick_volume",
    ]

    for column in numeric_columns:
        data[column] = pd.to_numeric(
            data[column],
            errors="coerce",
        )

    data = data.dropna(subset=numeric_columns)

    return data


def find_fractals(data, limit=8):
    fractal_highs = []
    fractal_lows = []

    for index in range(1, len(data) - 1):
        previous_bar = data.iloc[index - 1]
        middle_bar = data.iloc[index]
        next_bar = data.iloc[index + 1]

        is_fractal_high = (
            middle_bar["high"] > previous_bar["high"]
            and middle_bar["high"] > next_bar["high"]
        )

        is_fractal_low = (
            middle_bar["low"] < previous_bar["low"]
            and middle_bar["low"] < next_bar["low"]
        )

        later_bars = data.iloc[index + 1:]

        if is_fractal_high:
            swept_bars = later_bars[
                later_bars["high"] > middle_bar["high"]
            ]

            fractal_highs.append(
                {
                    "time": middle_bar[
                        "utc_time"
                    ].isoformat(),
                    "price": float(middle_bar["high"]),
                    "swept": not swept_bars.empty,
                    "sweep_time": (
                        swept_bars.iloc[0][
                            "utc_time"
                        ].isoformat()
                        if not swept_bars.empty
                        else None
                    ),
                }
            )

        if is_fractal_low:
            swept_bars = later_bars[
                later_bars["low"] < middle_bar["low"]
            ]

            fractal_lows.append(
                {
                    "time": middle_bar[
                        "utc_time"
                    ].isoformat(),
                    "price": float(middle_bar["low"]),
                    "swept": not swept_bars.empty,
                    "sweep_time": (
                        swept_bars.iloc[0][
                            "utc_time"
                        ].isoformat()
                        if not swept_bars.empty
                        else None
                    ),
                }
            )

    return {
        "highs": fractal_highs[-limit:],
        "lows": fractal_lows[-limit:],
    }


def bullish_fill_status(data, index, lower, upper):
    later_bars = data.iloc[index + 1:]

    if later_bars.empty:
        return "open"

    if (later_bars["low"] <= lower).any():
        return "fulfilled"

    if (later_bars["low"] < upper).any():
        return "partial"

    return "open"


def bearish_fill_status(data, index, lower, upper):
    later_bars = data.iloc[index + 1:]

    if later_bars.empty:
        return "open"

    if (later_bars["high"] >= upper).any():
        return "fulfilled"

    if (later_bars["high"] > lower).any():
        return "partial"

    return "open"


def validity_status(data, index, direction, lower, upper):
    """Native-TF validity based only on later closed candle closes."""
    later_bars = data.iloc[index + 1:]

    if later_bars.empty:
        return {
            "validity_status": "valid",
            "invalidation_time": None,
        }

    if direction == "bullish":
        invalidating_bars = later_bars[
            later_bars["close"] < lower
        ]
    else:
        invalidating_bars = later_bars[
            later_bars["close"] > upper
        ]

    return {
        "validity_status": (
            "invalidated"
            if not invalidating_bars.empty
            else "valid"
        ),
        "invalidation_time": (
            invalidating_bars.iloc[0]["utc_time"].isoformat()
            if not invalidating_bars.empty
            else None
        ),
    }


def build_imbalance_object(
    feature_type,
    direction,
    data,
    index,
    lower,
    upper,
):
    if direction == "bullish":
        fill_status = bullish_fill_status(
            data,
            index,
            lower,
            upper,
        )
    else:
        fill_status = bearish_fill_status(
            data,
            index,
            lower,
            upper,
        )

    validity = validity_status(
        data,
        index,
        direction,
        lower,
        upper,
    )

    result = {
        "type": feature_type,
        "direction": direction,
        "time": data.iloc[index]["utc_time"].isoformat(),
        "lower": float(lower),
        "upper": float(upper),
        "midpoint": (float(lower) + float(upper)) / 2,
        "fill_status": fill_status,
        "validity_status": validity["validity_status"],
        "invalidation_time": validity["invalidation_time"],
    }

    # Temporary compatibility for level_state.py. This is fill state only;
    # validity_status remains the authoritative lifecycle field.
    if feature_type == "fvg":
        result["status"] = fill_status

    return result


def find_fvgs(data, lookback=200, limit=10):
    fvgs = []

    start_index = max(2, len(data) - lookback)

    for index in range(start_index, len(data)):
        first_bar = data.iloc[index - 2]
        third_bar = data.iloc[index]

        bullish_fvg = (
            third_bar["low"] > first_bar["high"]
        )

        bearish_fvg = (
            third_bar["high"] < first_bar["low"]
        )

        if bullish_fvg:
            lower = float(first_bar["high"])
            upper = float(third_bar["low"])

            fvgs.append(
                build_imbalance_object(
                    "fvg",
                    "bullish",
                    data,
                    index,
                    lower,
                    upper,
                )
            )

        if bearish_fvg:
            lower = float(third_bar["high"])
            upper = float(first_bar["low"])

            fvgs.append(
                build_imbalance_object(
                    "fvg",
                    "bearish",
                    data,
                    index,
                    lower,
                    upper,
                )
            )

    return fvgs[-limit:]


def find_volume_imbalances(data, lookback=200, limit=10):
    """Adjacent body-to-body gaps where candle wicks still overlap."""
    imbalances = []
    start_index = max(1, len(data) - lookback)

    for index in range(start_index, len(data)):
        previous_bar = data.iloc[index - 1]
        current_bar = data.iloc[index]

        previous_body_low = min(
            previous_bar["open"],
            previous_bar["close"],
        )
        previous_body_high = max(
            previous_bar["open"],
            previous_bar["close"],
        )
        current_body_low = min(
            current_bar["open"],
            current_bar["close"],
        )
        current_body_high = max(
            current_bar["open"],
            current_bar["close"],
        )

        bullish_body_gap = current_body_low > previous_body_high
        bearish_body_gap = current_body_high < previous_body_low
        bullish_true_gap = current_bar["low"] > previous_bar["high"]
        bearish_true_gap = current_bar["high"] < previous_bar["low"]

        if bullish_body_gap and not bullish_true_gap:
            imbalances.append(
                build_imbalance_object(
                    "volume_imbalance",
                    "bullish",
                    data,
                    index,
                    float(previous_body_high),
                    float(current_body_low),
                )
            )

        if bearish_body_gap and not bearish_true_gap:
            imbalances.append(
                build_imbalance_object(
                    "volume_imbalance",
                    "bearish",
                    data,
                    index,
                    float(current_body_high),
                    float(previous_body_low),
                )
            )

    return imbalances[-limit:]


def find_gaps(data, lookback=200, limit=10):
    """True adjacent-candle price gaps with no wick overlap."""
    gaps = []
    start_index = max(1, len(data) - lookback)

    for index in range(start_index, len(data)):
        previous_bar = data.iloc[index - 1]
        current_bar = data.iloc[index]

        if current_bar["low"] > previous_bar["high"]:
            gaps.append(
                build_imbalance_object(
                    "gap",
                    "bullish",
                    data,
                    index,
                    float(previous_bar["high"]),
                    float(current_bar["low"]),
                )
            )

        if current_bar["high"] < previous_bar["low"]:
            gaps.append(
                build_imbalance_object(
                    "gap",
                    "bearish",
                    data,
                    index,
                    float(current_bar["high"]),
                    float(previous_bar["low"]),
                )
            )

    return gaps[-limit:]


def find_displacements(data, limit=8):
    if len(data) < 25:
        return []

    candle_body = (
        data["close"] - data["open"]
    ).abs()

    candle_range = data["high"] - data["low"]

    displacements = []

    for index in range(20, len(data)):
        previous_bodies = candle_body.iloc[
            index - 20:index
        ]

        previous_ranges = candle_range.iloc[
            index - 20:index
        ]

        median_body = previous_bodies.median()
        median_range = previous_ranges.median()

        current_body = candle_body.iloc[index]
        current_range = candle_range.iloc[index]

        if median_body <= 0 or median_range <= 0:
            continue

        is_displacement = (
            current_body >= median_body * 2
            and current_range >= median_range * 1.5
        )

        if not is_displacement:
            continue

        bar = data.iloc[index]

        direction = (
            "bullish"
            if bar["close"] > bar["open"]
            else "bearish"
        )

        displacements.append(
            {
                "time": bar["utc_time"].isoformat(),
                "direction": direction,
                "body_ratio": round(
                    float(current_body / median_body),
                    2,
                ),
                "range_ratio": round(
                    float(current_range / median_range),
                    2,
                ),
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
            }
        )

    return displacements[-limit:]


def calculate_range_candidate(data, lookback=100):
    sample = data.tail(lookback)

    range_high = float(sample["high"].max())
    range_low = float(sample["low"].min())
    midpoint = (range_high + range_low) / 2

    last_close = float(data.iloc[-1]["close"])

    if last_close > midpoint:
        position = "premium"
    elif last_close < midpoint:
        position = "discount"
    else:
        position = "equilibrium"

    return {
        "type": "mechanical_candidate",
        "lookback_bars": int(len(sample)),
        "high": range_high,
        "low": range_low,
        "midpoint_0_5": midpoint,
        "last_close": last_close,
        "position": position,
    }


def calculate_volume_context(data):
    sample = data.tail(20)

    median_volume = float(
        sample["tick_volume"].median()
    )

    last_volume = float(
        data.iloc[-1]["tick_volume"]
    )

    ratio = (
        last_volume / median_volume
        if median_volume > 0
        else 0
    )

    return {
        "type": "supporting_context_only",
        "last_tick_volume": last_volume,
        "median_20": median_volume,
        "relative_ratio": round(ratio, 2),
    }


def analyse_timeframe(timeframe):
    data = load_closed_bars(timeframe)

    fractals = find_fractals(data)
    fvgs = find_fvgs(data)
    volume_imbalances = find_volume_imbalances(data)
    gaps = find_gaps(data)
    displacements = find_displacements(data)

    return {
        "bars_used": int(len(data)),
        "last_closed_time": (
            data.iloc[-1]["utc_time"].isoformat()
        ),
        "last_close": float(
            data.iloc[-1]["close"]
        ),
        "range_candidate": (
            calculate_range_candidate(data)
        ),
        "volume_context": (
            calculate_volume_context(data)
        ),
        "fractals": fractals,
        "fvgs": fvgs,
        "volume_imbalances": volume_imbalances,
        "gaps": gaps,
        "displacements": displacements,
    }


def main():
    result = {
        "schema_version": 3,
        "symbol": "GOLD.pro",
        "generated_utc": (
            datetime.now(timezone.utc).isoformat()
        ),
        "purpose": (
            "Observable market facts only. "
            "Not a trading signal."
        ),
        "timeframes": {},
    }

    for timeframe in TIMEFRAMES:
        features = analyse_timeframe(timeframe)
        result["timeframes"][timeframe] = features

        valid_fvgs = [
            fvg
            for fvg in features["fvgs"]
            if fvg["validity_status"] == "valid"
        ]

        valid_volume_imbalances = [
            item
            for item in features["volume_imbalances"]
            if item["validity_status"] == "valid"
        ]

        valid_gaps = [
            item
            for item in features["gaps"]
            if item["validity_status"] == "valid"
        ]

        print(
            f"{timeframe}: "
            f"fractals="
            f"{len(features['fractals']['highs']) + len(features['fractals']['lows'])} | "
            f"valid_fvgs={len(valid_fvgs)} | "
            f"valid_volume_imbalances={len(valid_volume_imbalances)} | "
            f"valid_gaps={len(valid_gaps)} | "
            f"displacements="
            f"{len(features['displacements'])} | "
            f"position="
            f"{features['range_candidate']['position']}"
        )

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

    print(f"FEATURES_SAVED: {OUTPUT_FILE}")
    print("FEATURE_EXTRACTION: PASSED")


if __name__ == "__main__":
    main()
