import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


RAW_DIRECTORY = (
    Path("data") / "raw" / "GOLD_pro"
)

LEVELS_FILE = (
    Path("data") / "levels" / "GOLD_pro.json"
)

RANGES_FILE = (
    Path("data") / "ranges" / "GOLD_pro.json"
)

OUTPUT_DIRECTORY = (
    Path("data") / "liquidity"
)

OUTPUT_FILE = (
    OUTPUT_DIRECTORY / "GOLD_pro.json"
)

TIMEFRAMES = [
    "D1",
    "H4",
    "H1",
    "M15",
]

TIMEFRAME_WEIGHT = {
    "D1": 4,
    "H4": 3,
    "H1": 2,
    "M15": 1,
    "reference": 3,
}

TYPE_WEIGHT = {
    "key_level": 4,
    "IDM": 3,
    "equal_highs": 3,
    "equal_lows": 3,
    "FVG": 2,
    "fractal_high": 1,
    "fractal_low": 1,
    "range_endpoint": 2,
    "fib": 1,
}

IDM_PROMINENCE_RATIO = 0.15
EQUAL_LEVEL_TOLERANCE_RATIO = 0.12
CLUSTER_TOLERANCE_PERCENT = 0.15


def load_json(file_path):
    with file_path.open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def load_closed_bars(timeframe):
    file_path = (
        RAW_DIRECTORY / f"{timeframe}.csv"
    )

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

    for column in [
        "open",
        "high",
        "low",
        "close",
        "tick_volume",
    ]:
        data[column] = pd.to_numeric(
            data[column],
            errors="coerce",
        )

    data = data.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    )

    return data.sort_values(
        "utc_time"
    ).reset_index(drop=True)


def reference_median_range(
    data,
    index,
):
    start = max(0, index - 20)

    reference = data.iloc[start:index]

    if reference.empty:
        return 0

    ranges = (
        reference["high"] - reference["low"]
    )

    return float(ranges.median())


def detect_three_candle_levels(
    data,
    timeframe,
):
    levels = []

    for index in range(1, len(data) - 1):
        previous_bar = data.iloc[index - 1]
        middle_bar = data.iloc[index]
        next_bar = data.iloc[index + 1]

        median_range = reference_median_range(
            data,
            index,
        )

        if median_range <= 0:
            continue

        high_prominence = (
            middle_bar["high"]
            - max(
                previous_bar["high"],
                next_bar["high"],
            )
        )

        low_prominence = (
            min(
                previous_bar["low"],
                next_bar["low"],
            )
            - middle_bar["low"]
        )

        if high_prominence > 0:
            prominence_ratio = (
                high_prominence / median_range
            )

            later = data.iloc[index + 1:]

            swept = bool(
                (
                    later["high"]
                    > middle_bar["high"]
                ).any()
            )

            level_type = (
                "IDM"
                if prominence_ratio
                >= IDM_PROMINENCE_RATIO
                else "fractal_high"
            )

            levels.append(
                {
                    "type": level_type,
                    "side": "high",
                    "timeframe": timeframe,
                    "time": (
                        middle_bar[
                            "utc_time"
                        ].isoformat()
                    ),
                    "price": float(
                        middle_bar["high"]
                    ),
                    "prominence_ratio": round(
                        float(prominence_ratio),
                        3,
                    ),
                    "swept": swept,
                    "index": index,
                }
            )

        if low_prominence > 0:
            prominence_ratio = (
                low_prominence / median_range
            )

            later = data.iloc[index + 1:]

            swept = bool(
                (
                    later["low"]
                    < middle_bar["low"]
                ).any()
            )

            level_type = (
                "IDM"
                if prominence_ratio
                >= IDM_PROMINENCE_RATIO
                else "fractal_low"
            )

            levels.append(
                {
                    "type": level_type,
                    "side": "low",
                    "timeframe": timeframe,
                    "time": (
                        middle_bar[
                            "utc_time"
                        ].isoformat()
                    ),
                    "price": float(
                        middle_bar["low"]
                    ),
                    "prominence_ratio": round(
                        float(prominence_ratio),
                        3,
                    ),
                    "swept": swept,
                    "index": index,
                }
            )

    return levels


def detect_equal_levels(
    data,
    timeframe,
    pivots,
):
    candle_ranges = (
        data["high"] - data["low"]
    )

    tolerance = float(
        candle_ranges.tail(100).median()
        * EQUAL_LEVEL_TOLERANCE_RATIO
    )

    if tolerance <= 0:
        return []

    equal_levels = []

    for side in ["high", "low"]:
        side_pivots = [
            pivot
            for pivot in pivots[-80:]
            if pivot["side"] == side
        ]

        used = set()

        for first_index, first in enumerate(
            side_pivots
        ):
            if first_index in used:
                continue

            group = [first]

            for second_index in range(
                first_index + 1,
                len(side_pivots),
            ):
                second = side_pivots[
                    second_index
                ]

                if (
                    second["index"]
                    - first["index"]
                    < 2
                ):
                    continue

                group_average = sum(
                    item["price"]
                    for item in group
                ) / len(group)

                if abs(
                    second["price"]
                    - group_average
                ) <= tolerance:
                    group.append(second)
                    used.add(second_index)

            if len(group) < 2:
                continue

            average_price = sum(
                item["price"]
                for item in group
            ) / len(group)

            last_index = max(
                item["index"]
                for item in group
            )

            later = data.iloc[
                last_index + 1:
            ]

            if side == "high":
                swept = bool(
                    (
                        later["high"]
                        > average_price
                    ).any()
                )

                level_type = "equal_highs"

            else:
                swept = bool(
                    (
                        later["low"]
                        < average_price
                    ).any()
                )

                level_type = "equal_lows"

            equal_levels.append(
                {
                    "type": level_type,
                    "side": side,
                    "timeframe": timeframe,
                    "price": round(
                        float(average_price),
                        5,
                    ),
                    "touches": len(group),
                    "swept": swept,
                    "times": [
                        item["time"]
                        for item in group
                    ],
                }
            )

    return equal_levels


def detect_active_fvgs(
    data,
    timeframe,
    lookback=250,
):
    fvgs = []

    start_index = max(
        2,
        len(data) - lookback,
    )

    for index in range(
        start_index,
        len(data),
    ):
        first_bar = data.iloc[index - 2]
        third_bar = data.iloc[index]

        bullish = (
            third_bar["low"]
            > first_bar["high"]
        )

        bearish = (
            third_bar["high"]
            < first_bar["low"]
        )

        if bullish:
            lower = float(
                first_bar["high"]
            )

            upper = float(
                third_bar["low"]
            )

            later = data.iloc[index + 1:]

            if (
                not later.empty
                and (
                    later["low"] <= lower
                ).any()
            ):
                status = "fulfilled"

            elif (
                not later.empty
                and (
                    later["low"] < upper
                ).any()
            ):
                status = "partial"

            else:
                status = "open"

            if status != "fulfilled":
                fvgs.append(
                    {
                        "type": "FVG",
                        "direction": "bullish",
                        "timeframe": timeframe,
                        "time": (
                            third_bar[
                                "utc_time"
                            ].isoformat()
                        ),
                        "lower": lower,
                        "upper": upper,
                        "price": (
                            lower + upper
                        ) / 2,
                        "status": status,
                    }
                )

        if bearish:
            lower = float(
                third_bar["high"]
            )

            upper = float(
                first_bar["low"]
            )

            later = data.iloc[index + 1:]

            if (
                not later.empty
                and (
                    later["high"] >= upper
                ).any()
            ):
                status = "fulfilled"

            elif (
                not later.empty
                and (
                    later["high"] > lower
                ).any()
            ):
                status = "partial"

            else:
                status = "open"

            if status != "fulfilled":
                fvgs.append(
                    {
                        "type": "FVG",
                        "direction": "bearish",
                        "timeframe": timeframe,
                        "time": (
                            third_bar[
                                "utc_time"
                            ].isoformat()
                        ),
                        "lower": lower,
                        "upper": upper,
                        "price": (
                            lower + upper
                        ) / 2,
                        "status": status,
                    }
                )

    return fvgs


def key_level_components(
    levels_data,
):
    components = []

    for name, level in levels_data[
        "levels"
    ].items():
        components.append(
            {
                "type": "key_level",
                "name": name,
                "timeframe": "reference",
                "price": float(
                    level["price"]
                ),
            }
        )

    return components


def range_components(
    ranges_data,
):
    components = []

    for timeframe in [
        "D1",
        "H4",
        "H1",
    ]:
        timeframe_data = ranges_data[
            "timeframes"
        ].get(timeframe, {})

        for range_item in timeframe_data.get(
            "selected_ranges",
            [],
        ):
            components.append(
                {
                    "type": "range_endpoint",
                    "name": (
                        f"{timeframe} "
                        f"{range_item['direction']} "
                        f"endpoint"
                    ),
                    "timeframe": timeframe,
                    "price": float(
                        range_item[
                            "end"
                        ]["price"]
                    ),
                }
            )

            for fib_name in [
                "0.5",
                "0.618",
                "0.705",
                "0.786",
            ]:
                components.append(
                    {
                        "type": "fib",
                        "name": (
                            f"{timeframe} "
                            f"fib {fib_name}"
                        ),
                        "timeframe": timeframe,
                        "price": float(
                            range_item[
                                "fibonacci"
                            ][fib_name]
                        ),
                    }
                )

    return components


def deduplicate_components(
    components,
):
    unique = {}

    for component in components:
        component_type = component["type"]
        timeframe = component.get(
            "timeframe",
            "unknown",
        )

        rounded_price = round(
            float(component["price"]),
            2,
        )

        if component_type == "key_level":
            identity = (
                component_type,
                component.get("name"),
                rounded_price,
            )

        elif component_type == "fib":
            identity = (
                component_type,
                timeframe,
                component.get("name"),
                rounded_price,
            )

        else:
            identity = (
                component_type,
                timeframe,
                rounded_price,
            )

        if identity not in unique:
            unique[identity] = component

    return list(unique.values())


def component_score(component):
    timeframe_score = TIMEFRAME_WEIGHT.get(
        component.get("timeframe"),
        1,
    )

    type_score = TYPE_WEIGHT.get(
        component["type"],
        1,
    )

    return timeframe_score + type_score


def evidence_family(component):
    component_type = component["type"]
    timeframe = component.get(
        "timeframe",
        "unknown",
    )

    if component_type == "key_level":
        return (
            component_type,
            component.get("name"),
        )

    if component_type == "fib":
        return (
            component_type,
            timeframe,
            component.get("name"),
        )

    return (
        component_type,
        timeframe,
    )


def calculate_cluster_score(
    components,
):
    family_scores = {}

    for component in components:
        family = evidence_family(
            component
        )

        score = component_score(
            component
        )

        existing_score = (
            family_scores.get(family, 0)
        )

        family_scores[family] = max(
            existing_score,
            score,
        )

    return sum(
        family_scores.values()
    )


def cluster_components(
    components,
    current_price,
):
    active_components = [
        component
        for component in components
        if component.get("swept") is not True
    ]

    active_components = (
        deduplicate_components(
            active_components
        )
    )

    active_components.sort(
        key=lambda item: item["price"]
    )

    tolerance = (
        current_price
        * CLUSTER_TOLERANCE_PERCENT
        / 100
    )

    clusters = []

    for component in active_components:
        matched_cluster = None

        for cluster in clusters:
            if abs(
                component["price"]
                - cluster["center"]
            ) <= tolerance:
                matched_cluster = cluster
                break

        if matched_cluster is None:
            clusters.append(
                {
                    "center": component[
                        "price"
                    ],
                    "components": [
                        component
                    ],
                }
            )

        else:
            matched_cluster[
                "components"
            ].append(component)

            matched_cluster["center"] = (
                sum(
                    item["price"]
                    for item in matched_cluster[
                        "components"
                    ]
                )
                / len(
                    matched_cluster[
                        "components"
                    ]
                )
            )

    for cluster in clusters:
        cluster["center"] = round(
            float(cluster["center"]),
            5,
        )

        cluster["score"] = (
            calculate_cluster_score(
                cluster["components"]
            )
        )

        cluster["distance_percent"] = round(
            (
                cluster["center"]
                - current_price
            )
            / current_price
            * 100,
            3,
        )

        cluster["side"] = (
            "above"
            if cluster["center"]
            > current_price
            else "below"
        )

        cluster["component_count"] = len(
            cluster["components"]
        )

    clusters.sort(
        key=lambda item: abs(
            item["distance_percent"]
        )
    )

    return clusters


def main():
    levels_data = load_json(
        LEVELS_FILE
    )

    ranges_data = load_json(
        RANGES_FILE
    )

    current_price = float(
        levels_data["current_price"]
    )

    all_components = []
    timeframe_results = {}

    for timeframe in TIMEFRAMES:
        data = load_closed_bars(
            timeframe
        )

        pivots = (
            detect_three_candle_levels(
                data,
                timeframe,
            )
        )

        equal_levels = (
            detect_equal_levels(
                data,
                timeframe,
                pivots,
            )
        )

        fvgs = detect_active_fvgs(
            data,
            timeframe,
        )

        unswept_pivots = [
            pivot
            for pivot in pivots
            if not pivot["swept"]
        ]

        recent_swept_pivots = [
            pivot
            for pivot in pivots
            if pivot["swept"]
        ][-20:]

        stored_pivots = (
            unswept_pivots
            + recent_swept_pivots
        )

        timeframe_results[timeframe] = {
            "three_candle_levels": (
                stored_pivots
            ),
            "equal_levels": equal_levels,
            "active_fvgs": fvgs,
        }

        all_components.extend(
            unswept_pivots
        )

        all_components.extend(
            [
                level
                for level in equal_levels
                if not level["swept"]
            ]
        )

        all_components.extend(fvgs)

    all_components.extend(
        key_level_components(
            levels_data
        )
    )

    all_components.extend(
        range_components(
            ranges_data
        )
    )

    clusters = cluster_components(
        all_components,
        current_price,
    )

    result = {
        "symbol": "GOLD.pro",
        "generated_utc": (
            datetime.now(
                timezone.utc
            ).isoformat()
        ),
        "current_price": current_price,
        "timeframes": timeframe_results,
        "clusters": clusters,
        "settings": {
            "idm_prominence_ratio": (
                IDM_PROMINENCE_RATIO
            ),
            "equal_level_tolerance_ratio": (
                EQUAL_LEVEL_TOLERANCE_RATIO
            ),
            "cluster_tolerance_percent": (
                CLUSTER_TOLERANCE_PERCENT
            ),
        },
        "warning": (
            "Liquidity clusters are candidates "
            "for scenario analysis, not signals."
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
    print("")
    print("NEAREST LIQUIDITY CLUSTERS:")

    for cluster in clusters[:12]:
        labels = []

        for component in cluster[
            "components"
        ]:
            label = component.get(
                "name",
                component["type"],
            )

            labels.append(
                f"{component.get('timeframe')} "
                f"{label}"
            )

        print(
            f"{cluster['side']} | "
            f"{cluster['center']} | "
            f"{cluster['distance_percent']}% | "
            f"score={cluster['score']} | "
            f"{'; '.join(labels)}"
        )

    print("")
    print(f"LIQUIDITY_SAVED: {OUTPUT_FILE}")
    print("LIQUIDITY_MAP: PASSED")


if __name__ == "__main__":
    main()