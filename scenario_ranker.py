import json
from datetime import datetime, timezone
from pathlib import Path


FEATURES_FILE = (
    Path("data") / "features" / "GOLD_pro.json"
)

RANGES_FILE = (
    Path("data") / "ranges" / "GOLD_pro.json"
)

LEVELS_FILE = (
    Path("data") / "levels" / "GOLD_pro.json"
)

OUTPUT_DIRECTORY = Path("data") / "scenarios"

OUTPUT_JSON = (
    OUTPUT_DIRECTORY / "GOLD_pro.json"
)

OUTPUT_TEXT = (
    OUTPUT_DIRECTORY / "GOLD_pro.txt"
)

CONFLUENCE_TOLERANCE_PERCENT = 0.15
NEAR_LEVEL_PERCENT = 0.50


def load_json(file_path):
    with file_path.open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def percent_distance(price, level):
    if price == 0:
        return 0

    return (
        (level - price)
        / price
        * 100
    )


def prices_are_close(
    first_price,
    second_price,
    tolerance_percent=CONFLUENCE_TOLERANCE_PERCENT,
):
    if first_price == 0:
        return False

    difference_percent = abs(
        (
            second_price - first_price
        )
        / first_price
        * 100
    )

    return (
        difference_percent
        <= tolerance_percent
    )


def select_current_range(
    ranges_data,
    timeframe,
):
    timeframe_data = ranges_data[
        "timeframes"
    ].get(timeframe, {})

    candidates = timeframe_data.get(
        "selected_ranges",
        [],
    )

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda item: (
            item.get("end_index", 0),
            item.get("range_units", 0),
        ),
    )


def determine_primary_direction(
    ranges_data,
):
    weights = {
        "D1": 3,
        "H4": 2,
        "H1": 1,
    }

    bullish_score = 0
    bearish_score = 0
    current_ranges = {}

    for timeframe, weight in weights.items():
        current_range = select_current_range(
            ranges_data,
            timeframe,
        )

        current_ranges[timeframe] = current_range

        if current_range is None:
            continue

        if current_range["direction"] == "bullish":
            bullish_score += weight

        if current_range["direction"] == "bearish":
            bearish_score += weight

    if bullish_score > bearish_score:
        direction = "bullish"

    elif bearish_score > bullish_score:
        direction = "bearish"

    else:
        direction = "mixed"

    return {
        "direction": direction,
        "bullish_score": bullish_score,
        "bearish_score": bearish_score,
        "ranges": current_ranges,
    }


def sort_key_levels(
    levels_data,
    current_price,
):
    above = []
    below = []

    for name, level in levels_data[
        "levels"
    ].items():
        item = {
            "name": name,
            "price": float(level["price"]),
            "distance_percent": (
                percent_distance(
                    current_price,
                    float(level["price"]),
                )
            ),
        }

        if item["price"] > current_price:
            above.append(item)

        elif item["price"] < current_price:
            below.append(item)

    above.sort(
        key=lambda item: item["price"]
    )

    below.sort(
        key=lambda item: item["price"],
        reverse=True,
    )

    return above, below


def choose_next_key_level(
    direction,
    above,
    below,
):
    if direction == "bullish" and above:
        return above[0]

    if direction == "bearish" and below:
        return below[0]

    return None


def find_range_confluences(
    target,
    direction_data,
):
    confluences = []

    target_price = target["price"]

    for timeframe, range_item in (
        direction_data["ranges"].items()
    ):
        if range_item is None:
            continue

        start_price = float(
            range_item["start"]["price"]
        )

        end_price = float(
            range_item["end"]["price"]
        )

        if prices_are_close(
            target_price,
            start_price,
        ):
            confluences.append(
                f"{timeframe} range start"
            )

        if prices_are_close(
            target_price,
            end_price,
        ):
            confluences.append(
                f"{timeframe} range endpoint"
            )

        for fib_name, fib_price in (
            range_item["fibonacci"].items()
        ):
            if fib_name in {"0.0", "1.0"}:
                continue

            if prices_are_close(
                target_price,
                float(fib_price),
            ):
                confluences.append(
                    f"{timeframe} fib {fib_name}"
                )

    return confluences


def find_fvg_confluences(
    target,
    features_data,
):
    confluences = []
    target_price = target["price"]

    for timeframe in [
        "D1",
        "H4",
        "H1",
        "M15",
    ]:
        timeframe_data = features_data[
            "timeframes"
        ].get(timeframe, {})

        for fvg in timeframe_data.get(
            "fvgs",
            [],
        ):
            if fvg["status"] == "fulfilled":
                continue

            lower = float(fvg["lower"])
            upper = float(fvg["upper"])

            inside = (
                lower
                <= target_price
                <= upper
            )

            close_to_edge = (
                prices_are_close(
                    target_price,
                    lower,
                )
                or prices_are_close(
                    target_price,
                    upper,
                )
            )

            if inside or close_to_edge:
                confluences.append(
                    f"{timeframe} "
                    f"{fvg['direction']} FVG "
                    f"({fvg['status']})"
                )

    return confluences


def find_fractal_confluences(
    target,
    features_data,
):
    confluences = []
    target_price = target["price"]

    for timeframe in [
        "D1",
        "H4",
        "H1",
        "M15",
    ]:
        timeframe_data = features_data[
            "timeframes"
        ].get(timeframe, {})

        fractals = timeframe_data.get(
            "fractals",
            {},
        )

        for side_name in ["highs", "lows"]:
            for fractal in fractals.get(
                side_name,
                [],
            ):
                fractal_price = float(
                    fractal["price"]
                )

                if prices_are_close(
                    target_price,
                    fractal_price,
                ):
                    state = (
                        "swept"
                        if fractal["swept"]
                        else "unswept"
                    )

                    confluences.append(
                        f"{timeframe} fractal "
                        f"{side_name[:-1]} "
                        f"({state})"
                    )

    return confluences


def volume_context(
    features_data,
):
    result = []

    for timeframe in ["H4", "H1", "M15"]:
        timeframe_data = features_data[
            "timeframes"
        ].get(timeframe)

        if not timeframe_data:
            continue

        volume = timeframe_data.get(
            "volume_context",
            {},
        )

        ratio = volume.get(
            "relative_ratio"
        )

        if ratio is not None:
            result.append(
                {
                    "timeframe": timeframe,
                    "relative_ratio": float(ratio),
                }
            )

    return result


def observation_priority(
    direction_data,
    target,
    confluences,
):
    score = 0
    reasons = []

    direction = direction_data["direction"]

    d1 = direction_data["ranges"].get("D1")
    h4 = direction_data["ranges"].get("H4")
    h1 = direction_data["ranges"].get("H1")

    if (
        d1
        and h4
        and d1["direction"] == h4["direction"]
    ):
        score += 2
        reasons.append("D1 and H4 aligned")

    if (
        h4
        and h1
        and h4["direction"] == h1["direction"]
    ):
        score += 1
        reasons.append("H4 and H1 aligned")

    if abs(target["distance_percent"]) <= 0.5:
        score += 1
        reasons.append("price near key level")

    if confluences:
        score += min(3, len(confluences))
        reasons.append(
            f"{len(confluences)} confluence factors"
        )

    if score >= 6:
        priority = "high"

    elif score >= 3:
        priority = "medium"

    else:
        priority = "low"

    return {
        "score": score,
        "priority": priority,
        "reasons": reasons,
        "direction": direction,
    }


def format_range_line(
    timeframe,
    range_item,
):
    if range_item is None:
        return f"{timeframe}: unavailable"

    return (
        f"{timeframe}: "
        f"{range_item['direction']} "
        f"{range_item['start']['price']} -> "
        f"{range_item['end']['price']} "
        f"({range_item['status']})"
    )


def build_message(
    current_price,
    direction_data,
    target,
    confluences,
    volume,
    priority,
):
    direction = direction_data["direction"]

    lines = [
        "XAUUSD - MARKET CONTEXT",
        "",
        f"Price: {current_price}",
        f"Primary delivery: {direction}",
        (
            "Observation priority: "
            f"{priority['priority'].upper()}"
        ),
        "",
        "Active ranges:",
    ]

    for timeframe in ["D1", "H4", "H1"]:
        lines.append(
            format_range_line(
                timeframe,
                direction_data[
                    "ranges"
                ].get(timeframe),
            )
        )

    if target is None:
        lines.extend(
            [
                "",
                "Next key level: unavailable",
            ]
        )

    else:
        lines.extend(
            [
                "",
                (
                    "Next key level / "
                    "possible Point B:"
                ),
                (
                    f"{target['name']} "
                    f"{target['price']} "
                    f"({abs(target['distance_percent']):.3f}% "
                    f"{'above' if target['distance_percent'] > 0 else 'below'})"
                ),
            ]
        )

    if confluences:
        lines.extend(
            [
                "",
                "Additional variables:",
            ]
        )

        for item in confluences:
            lines.append(f"- {item}")

    if volume:
        lines.extend(
            [
                "",
                "Tick volume context:",
            ]
        )

        for item in volume:
            lines.append(
                f"- {item['timeframe']}: "
                f"{item['relative_ratio']}x "
                f"median"
            )

    lines.extend(
        [
            "",
            "Interpretation:",
        ]
    )

    if (
        target
        and abs(
            target["distance_percent"]
        ) <= NEAR_LEVEL_PERCENT
    ):
        lines.append(
            "Price is approaching a first "
            "trouble area. Chasing the current "
            "movement has reduced space."
        )

        lines.append(
            "Watch PA at the level: rejection, "
            "RB, wick, consolidation, liquidity "
            "build or impulsive continuation."
        )

    else:
        lines.append(
            "Price is still delivering toward "
            "the next key level. Monitor the path "
            "and intermediate obstacles."
        )

    lines.extend(
        [
            "",
            "News context: not connected yet.",
            (
                "Status: hypothesis for visual "
                "review, not an entry signal."
            ),
        ]
    )

    return "\n".join(lines)


def main():
    features_data = load_json(
        FEATURES_FILE
    )

    ranges_data = load_json(
        RANGES_FILE
    )

    levels_data = load_json(
        LEVELS_FILE
    )

    current_price = float(
        levels_data["current_price"]
    )

    direction_data = (
        determine_primary_direction(
            ranges_data
        )
    )

    above, below = sort_key_levels(
        levels_data,
        current_price,
    )

    target = choose_next_key_level(
        direction_data["direction"],
        above,
        below,
    )

    confluences = []

    if target is not None:
        confluences.extend(
            find_range_confluences(
                target,
                direction_data,
            )
        )

        confluences.extend(
            find_fvg_confluences(
                target,
                features_data,
            )
        )

        confluences.extend(
            find_fractal_confluences(
                target,
                features_data,
            )
        )

    confluences = list(
        dict.fromkeys(confluences)
    )

    volume = volume_context(
        features_data
    )

    if target is not None:
        priority = observation_priority(
            direction_data,
            target,
            confluences,
        )
    else:
        priority = {
            "score": 0,
            "priority": "low",
            "reasons": [],
            "direction": (
                direction_data["direction"]
            ),
        }

    message = build_message(
        current_price,
        direction_data,
        target,
        confluences,
        volume,
        priority,
    )

    result = {
        "symbol": "GOLD.pro",
        "generated_utc": (
            datetime.now(timezone.utc).isoformat()
        ),
        "current_price": current_price,
        "primary_direction": direction_data,
        "next_key_level": target,
        "confluences": confluences,
        "volume_context": volume,
        "observation_priority": priority,
        "message": message,
        "limitations": [
            "No news feed connected",
            "No DXY context connected",
            "Tick volume is not institutional volume",
            "Human visual confirmation required",
        ],
    }

    OUTPUT_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_JSON.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            result,
            file,
            indent=2,
            ensure_ascii=False,
        )

    with OUTPUT_TEXT.open(
        "w",
        encoding="utf-8",
    ) as file:
        file.write(message)

    print(message)
    print("")
    print(f"SCENARIO_SAVED: {OUTPUT_JSON}")
    print("SCENARIO_RANKER: PASSED")


if __name__ == "__main__":
    main()