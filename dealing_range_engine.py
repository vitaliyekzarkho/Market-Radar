import json
from datetime import datetime, timezone
from pathlib import Path

from market_features import load_closed_bars


OUTPUT_DIRECTORY = Path("data") / "ranges"
OUTPUT_FILE = OUTPUT_DIRECTORY / "GOLD_pro.json"

TIMEFRAME_SETTINGS = {
    "W1": {
        "pivot_span": 2,
        "minimum_range_units": 3.0,
    },
    "D1": {
        "pivot_span": 3,
        "minimum_range_units": 4.0,
    },
    "H4": {
        "pivot_span": 4,
        "minimum_range_units": 4.0,
    },
    "H1": {
        "pivot_span": 4,
        "minimum_range_units": 5.0,
    },
}

FIB_LEVELS = [
    0.0,
    0.236,
    0.382,
    0.5,
    0.618,
    0.705,
    0.786,
    1.0,
]


def find_confirmed_pivots(data, span):
    pivots = []

    for index in range(
        span,
        len(data) - span,
    ):
        window = data.iloc[
            index - span:index + span + 1
        ]

        bar = data.iloc[index]

        unique_high = (
            bar["high"] == window["high"].max()
            and (window["high"] == bar["high"]).sum() == 1
        )

        unique_low = (
            bar["low"] == window["low"].min()
            and (window["low"] == bar["low"]).sum() == 1
        )

        if unique_high:
            pivots.append(
                {
                    "index": index,
                    "type": "high",
                    "price": float(bar["high"]),
                    "time": bar["utc_time"],
                }
            )

        if unique_low:
            pivots.append(
                {
                    "index": index,
                    "type": "low",
                    "price": float(bar["low"]),
                    "time": bar["utc_time"],
                }
            )

    pivots.sort(key=lambda item: item["index"])

    return compress_same_type_pivots(pivots)


def compress_same_type_pivots(pivots):
    compressed = []

    for pivot in pivots:
        if not compressed:
            compressed.append(pivot)
            continue

        previous = compressed[-1]

        if previous["type"] != pivot["type"]:
            compressed.append(pivot)
            continue

        if (
            pivot["type"] == "high"
            and pivot["price"] > previous["price"]
        ):
            compressed[-1] = pivot

        if (
            pivot["type"] == "low"
            and pivot["price"] < previous["price"]
        ):
            compressed[-1] = pivot

    return compressed


def calculate_reference_range(data, start_index):
    reference_start = max(0, start_index - 20)

    reference = data.iloc[
        reference_start:start_index
    ]

    if reference.empty:
        reference = data.iloc[
            :start_index + 1
        ]

    candle_ranges = (
        reference["high"] - reference["low"]
    )

    return float(candle_ranges.median())


def count_displacements(
    data,
    start_index,
    end_index,
):
    bodies = (
        data["close"] - data["open"]
    ).abs()

    ranges = data["high"] - data["low"]

    count = 0

    for index in range(
        max(start_index, 20),
        end_index + 1,
    ):
        median_body = bodies.iloc[
            index - 20:index
        ].median()

        median_range = ranges.iloc[
            index - 20:index
        ].median()

        if median_body <= 0 or median_range <= 0:
            continue

        if (
            bodies.iloc[index] >= median_body * 2
            and ranges.iloc[index] >= median_range * 1.5
        ):
            count += 1

    return count


def count_directional_fvgs(
    data,
    start_index,
    end_index,
    direction,
):
    count = 0

    for index in range(
        max(start_index + 2, 2),
        end_index + 1,
    ):
        first_bar = data.iloc[index - 2]
        third_bar = data.iloc[index]

        bullish_fvg = (
            third_bar["low"] > first_bar["high"]
        )

        bearish_fvg = (
            third_bar["high"] < first_bar["low"]
        )

        if direction == "bullish" and bullish_fvg:
            count += 1

        if direction == "bearish" and bearish_fvg:
            count += 1

    return count


def opposite_displacement_after_endpoint(
    data,
    end_index,
    direction,
    inspection_bars,
):
    bodies = (
        data["close"] - data["open"]
    ).abs()

    ranges = data["high"] - data["low"]

    inspection_end = min(
        len(data),
        end_index + inspection_bars + 1,
    )

    for index in range(
        end_index + 1,
        inspection_end,
    ):
        reference_start = max(0, index - 20)

        median_body = bodies.iloc[
            reference_start:index
        ].median()

        median_range = ranges.iloc[
            reference_start:index
        ].median()

        if median_body <= 0 or median_range <= 0:
            continue

        bar = data.iloc[index]

        large_candle = (
            bodies.iloc[index] >= median_body * 2
            and ranges.iloc[index] >= median_range * 1.5
        )

        bearish = bar["close"] < bar["open"]
        bullish = bar["close"] > bar["open"]

        if (
            direction == "bullish"
            and large_candle
            and bearish
        ):
            return True

        if (
            direction == "bearish"
            and large_candle
            and bullish
        ):
            return True

    return False


def endpoint_structure_break(
    data,
    end_index,
    direction,
    inspection_bars,
):
    reference_start = max(0, end_index - 3)

    reference = data.iloc[
        reference_start:end_index
    ]

    if reference.empty:
        return False

    inspection_end = min(
        len(data),
        end_index + inspection_bars + 1,
    )

    later = data.iloc[
        end_index + 1:inspection_end
    ]

    if later.empty:
        return False

    if direction == "bullish":
        structure_level = float(
            reference["low"].min()
        )

        return bool(
            (later["close"] < structure_level).any()
        )

    structure_level = float(
        reference["high"].max()
    )

    return bool(
        (later["close"] > structure_level).any()
    )


def endpoint_consolidation(
    data,
    end_index,
    inspection_bars,
):
    before_start = max(0, end_index - 20)

    previous = data.iloc[
        before_start:end_index
    ]

    inspection_end = min(
        len(data),
        end_index + inspection_bars + 1,
    )

    later = data.iloc[
        end_index + 1:inspection_end
    ]

    if len(previous) < 5 or len(later) < 3:
        return False

    previous_range = (
        previous["high"] - previous["low"]
    ).median()

    later_range = (
        later["high"] - later["low"]
    ).median()

    if previous_range <= 0:
        return False

    return bool(
        later_range <= previous_range * 0.7
    )


def detect_endpoint_reaction(
    data,
    end_pivot,
    direction,
    span,
):
    end_index = end_pivot["index"]
    endpoint_bar = data.iloc[end_index]

    candle_range = (
        endpoint_bar["high"]
        - endpoint_bar["low"]
    )

    wick_rejection = False

    if candle_range > 0:
        upper_wick = (
            endpoint_bar["high"]
            - max(
                endpoint_bar["open"],
                endpoint_bar["close"],
            )
        )

        lower_wick = (
            min(
                endpoint_bar["open"],
                endpoint_bar["close"],
            )
            - endpoint_bar["low"]
        )

        if direction == "bullish":
            wick_rejection = (
                upper_wick / candle_range >= 0.35
            )
        else:
            wick_rejection = (
                lower_wick / candle_range >= 0.35
            )

    inspection_bars = span + 6

    opposite_displacement = (
        opposite_displacement_after_endpoint(
            data,
            end_index,
            direction,
            inspection_bars,
        )
    )

    structure_break = endpoint_structure_break(
        data,
        end_index,
        direction,
        inspection_bars,
    )

    consolidation = endpoint_consolidation(
        data,
        end_index,
        inspection_bars,
    )

    if opposite_displacement and structure_break:
        status = "reversal_confirmed"

    elif (
        wick_rejection
        or opposite_displacement
        or structure_break
        or consolidation
    ):
        status = "reaction_building"

    else:
        status = "completed_no_reaction"

    return {
        "status": status,
        "wick_rejection": bool(wick_rejection),
        "opposite_displacement": bool(
            opposite_displacement
        ),
        "structure_break": bool(structure_break),
        "consolidation": bool(consolidation),
    }


def calculate_fibonacci(
    start_price,
    end_price,
):
    distance = end_price - start_price

    return {
        str(level): round(
            start_price + distance * level,
            5,
        )
        for level in FIB_LEVELS
    }


def create_candidate(
    data,
    start_pivot,
    end_pivot,
    internal_pivots,
    settings,
):
    if (
        start_pivot["type"] == "low"
        and end_pivot["type"] == "high"
    ):
        direction = "bullish"

    elif (
        start_pivot["type"] == "high"
        and end_pivot["type"] == "low"
    ):
        direction = "bearish"

    else:
        return None

    start_index = start_pivot["index"]
    end_index = end_pivot["index"]

    if end_index <= start_index:
        return None

    distance = abs(
        end_pivot["price"]
        - start_pivot["price"]
    )

    median_range = calculate_reference_range(
        data,
        start_index,
    )

    if median_range <= 0:
        return None

    range_units = distance / median_range

    if (
        range_units
        < settings["minimum_range_units"]
    ):
        return None

    displacement_count = count_displacements(
        data,
        start_index,
        end_index,
    )

    fvg_count = count_directional_fvgs(
        data,
        start_index,
        end_index,
        direction,
    )

    if (
        displacement_count == 0
        and fvg_count == 0
    ):
        return None

    reaction = detect_endpoint_reaction(
        data,
        end_pivot,
        direction,
        settings["pivot_span"],
    )

    hierarchy = (
        "parent"
        if internal_pivots >= 2
        else "nested"
    )

    return {
        "direction": direction,
        "hierarchy": hierarchy,
        "status": reaction["status"],
        "start_index": start_index,
        "end_index": end_index,
        "start": {
            "type": start_pivot["type"],
            "time": (
                start_pivot["time"].isoformat()
            ),
            "price": start_pivot["price"],
            "fib_value": 0,
        },
        "end": {
            "type": end_pivot["type"],
            "time": (
                end_pivot["time"].isoformat()
            ),
            "price": end_pivot["price"],
            "fib_value": 1,
        },
        "internal_pivots": internal_pivots,
        "bars_in_movement": (
            end_index - start_index + 1
        ),
        "range_units": round(
            float(range_units),
            2,
        ),
        "displacements": displacement_count,
        "directional_fvgs": fvg_count,
        "endpoint_reaction": reaction,
        "fibonacci": calculate_fibonacci(
            start_pivot["price"],
            end_pivot["price"],
        ),
        "classification": (
            "candidate_requires_context"
        ),
    }


def build_hierarchical_candidates(
    data,
    pivots,
    settings,
):
    candidates = []

    for start_position, start_pivot in enumerate(
        pivots
    ):
        best_endpoint_price = None

        for end_position in range(
            start_position + 1,
            len(pivots),
        ):
            pivot = pivots[end_position]

            if start_pivot["type"] == "low":
                if (
                    pivot["type"] == "low"
                    and pivot["price"]
                    <= start_pivot["price"]
                ):
                    break

                if pivot["type"] != "high":
                    continue

                new_extreme = (
                    best_endpoint_price is None
                    or pivot["price"]
                    > best_endpoint_price
                )

            else:
                if (
                    pivot["type"] == "high"
                    and pivot["price"]
                    >= start_pivot["price"]
                ):
                    break

                if pivot["type"] != "low":
                    continue

                new_extreme = (
                    best_endpoint_price is None
                    or pivot["price"]
                    < best_endpoint_price
                )

            if not new_extreme:
                continue

            best_endpoint_price = pivot["price"]

            internal_pivots = (
                end_position
                - start_position
                - 1
            )

            candidate = create_candidate(
                data,
                start_pivot,
                pivot,
                internal_pivots,
                settings,
            )

            if candidate is not None:
                candidates.append(candidate)

    return remove_duplicate_candidates(candidates)


def remove_duplicate_candidates(candidates):
    unique = {}

    for candidate in candidates:
        key = (
            candidate["direction"],
            candidate["start_index"],
            candidate["end_index"],
        )

        unique[key] = candidate

    return list(unique.values())


def select_relevant_candidates(
    candidates,
    data_length,
):
    if not candidates:
        return []

    recent_boundary = max(0, data_length - 180)

    recent = [
        candidate
        for candidate in candidates
        if candidate["end_index"] >= recent_boundary
    ]

    if not recent:
        recent = candidates

    parents = [
        candidate
        for candidate in recent
        if candidate["hierarchy"] == "parent"
    ]

    nested = [
        candidate
        for candidate in recent
        if candidate["hierarchy"] == "nested"
    ]

    parents.sort(
        key=lambda item: (
            item["end_index"],
            item["range_units"],
        )
    )

    nested.sort(
        key=lambda item: (
            item["end_index"],
            item["range_units"],
        )
    )

    selected = (
        parents[-4:]
        + nested[-3:]
    )

    selected = remove_duplicate_candidates(selected)

    selected.sort(
        key=lambda item: (
            item["end_index"],
            item["range_units"],
        )
    )

    return selected


def analyse_timeframe(timeframe):
    settings = TIMEFRAME_SETTINGS[timeframe]
    data = load_closed_bars(timeframe)

    pivots = find_confirmed_pivots(
        data,
        settings["pivot_span"],
    )

    all_candidates = (
        build_hierarchical_candidates(
            data,
            pivots,
            settings,
        )
    )

    selected = select_relevant_candidates(
        all_candidates,
        len(data),
    )

    return {
        "confirmed_pivots": len(pivots),
        "all_candidate_count": len(
            all_candidates
        ),
        "selected_ranges": selected,
    }


def main():
    result = {
        "symbol": "GOLD.pro",
        "generated_utc": (
            datetime.now(timezone.utc).isoformat()
        ),
        "definition": (
            "Completed directional delivery "
            "with parent and nested ranges."
        ),
        "warning": (
            "Selected ranges still require "
            "key-level and market context."
        ),
        "timeframes": {},
    }

    for timeframe in TIMEFRAME_SETTINGS:
        timeframe_result = analyse_timeframe(
            timeframe
        )

        result["timeframes"][timeframe] = (
            timeframe_result
        )

        selected = timeframe_result[
            "selected_ranges"
        ]

        print(
            f"\n{timeframe}: "
            f"pivots="
            f"{timeframe_result['confirmed_pivots']} | "
            f"all_candidates="
            f"{timeframe_result['all_candidate_count']} | "
            f"selected={len(selected)}"
        )

        for candidate in selected:
            print(
                f"  {candidate['hierarchy']} | "
                f"{candidate['direction']} | "
                f"{candidate['start']['price']} -> "
                f"{candidate['end']['price']} | "
                f"0.5="
                f"{candidate['fibonacci']['0.5']} | "
                f"{candidate['status']}"
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

    print(f"\nRANGES_SAVED: {OUTPUT_FILE}")
    print("DEALING_RANGE_ENGINE: PASSED")


if __name__ == "__main__":
    main()