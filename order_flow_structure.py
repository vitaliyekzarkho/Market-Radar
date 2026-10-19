from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


SYMBOL = "GOLD.pro"
SYMBOL_FILE = SYMBOL.replace(".", "_")
DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw" / SYMBOL_FILE
OUTPUT_FILE = DATA_DIR / "order_flow" / f"{SYMBOL_FILE}.json"
TIMEFRAMES = ("W1", "D1", "H4", "H1")
PARENT_CHILD = (("W1", "D1", 5), ("D1", "H4", 6), ("H4", "H1", 4))


def number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def load_closed_bars(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"Missing required file: {path}")
    data = pd.read_csv(path)
    required = {"open", "high", "low", "close"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")
    for column in required:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=list(required)).copy()
    if "is_closed" in data.columns:
        closed = data["is_closed"].astype(str).str.lower().isin({"true", "1", "yes"})
        data = data[closed].copy()
    time_column = "utc_time" if "utc_time" in data.columns else "time"
    data["_time"] = pd.to_datetime(data[time_column], utc=True, errors="coerce")
    data = data.dropna(subset=["_time"]).sort_values("_time").reset_index(drop=True)
    if len(data) < 7:
        raise RuntimeError(f"Not enough closed bars in {path}: {len(data)}")
    return data


def raw_pivots(data: pd.DataFrame, width: int) -> list[dict[str, Any]]:
    pivots: list[dict[str, Any]] = []
    for index in range(width, len(data) - width):
        bar = data.iloc[index]
        left = data.iloc[index - width:index]
        right = data.iloc[index + 1:index + width + 1]
        if float(bar["high"]) > float(left["high"].max()) and float(bar["high"]) > float(right["high"].max()):
            pivots.append({
                "index": index,
                "time": bar["_time"].isoformat(),
                "type": "high",
                "price": float(bar["high"]),
            })
        if float(bar["low"]) < float(left["low"].min()) and float(bar["low"]) < float(right["low"].min()):
            pivots.append({
                "index": index,
                "time": bar["_time"].isoformat(),
                "type": "low",
                "price": float(bar["low"]),
            })
    pivots.sort(key=lambda item: (item["index"], 0 if item["type"] == "low" else 1))
    return pivots


def alternating_pivots(pivots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for pivot in pivots:
        if not result or result[-1]["type"] != pivot["type"]:
            result.append(dict(pivot))
            continue
        previous = result[-1]
        more_extreme = (
            pivot["price"] > previous["price"]
            if pivot["type"] == "high"
            else pivot["price"] < previous["price"]
        )
        if more_extreme:
            result[-1] = dict(pivot)
    return result


def label_pivots(pivots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous_price: dict[str, float] = {}
    labelled: list[dict[str, Any]] = []
    for pivot in pivots:
        item = dict(pivot)
        pivot_type = str(pivot["type"])
        prior = previous_price.get(pivot_type)
        if prior is None:
            label = "H" if pivot_type == "high" else "L"
        elif pivot_type == "high":
            label = "HH" if pivot["price"] > prior else "LH" if pivot["price"] < prior else "EH"
        else:
            label = "HL" if pivot["price"] > prior else "LL" if pivot["price"] < prior else "EL"
        item["label"] = label
        labelled.append(item)
        previous_price[pivot_type] = float(pivot["price"])
    return labelled


def infer_bias(pivots: list[dict[str, Any]]) -> str:
    highs = [item for item in pivots if item["type"] == "high"]
    lows = [item for item in pivots if item["type"] == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return "insufficient"
    high_rising = highs[-1]["price"] > highs[-2]["price"]
    high_falling = highs[-1]["price"] < highs[-2]["price"]
    low_rising = lows[-1]["price"] > lows[-2]["price"]
    low_falling = lows[-1]["price"] < lows[-2]["price"]
    if high_rising and low_rising:
        return "bullish"
    if high_falling and low_falling:
        return "bearish"
    return "transition"


def protected_swing(pivots: list[dict[str, Any]], bias: str) -> dict[str, Any] | None:
    if bias == "bullish":
        higher_highs = [item for item in pivots if item.get("label") == "HH"]
        if not higher_highs:
            return None
        latest_hh = higher_highs[-1]
        prior_lows = [item for item in pivots if item["type"] == "low" and item["index"] < latest_hh["index"]]
        return dict(prior_lows[-1]) if prior_lows else None
    if bias == "bearish":
        lower_lows = [item for item in pivots if item.get("label") == "LL"]
        if not lower_lows:
            return None
        latest_ll = lower_lows[-1]
        prior_highs = [item for item in pivots if item["type"] == "high" and item["index"] < latest_ll["index"]]
        return dict(prior_highs[-1]) if prior_highs else None
    return None


def structure_layer(data: pd.DataFrame, width: int) -> dict[str, Any]:
    pivots = label_pivots(alternating_pivots(raw_pivots(data, width)))
    bias = infer_bias(pivots)
    protected = protected_swing(pivots, bias)
    return {
        "pivot_width": width,
        "bias": bias,
        "sequence": [item["label"] for item in pivots[-8:]],
        "protected_swing": protected,
        "recent_pivots": pivots[-12:],
    }


def displacement_candidates(data: pd.DataFrame) -> list[dict[str, Any]]:
    if len(data) < 25:
        return []
    bodies = (data["close"] - data["open"]).abs()
    ranges = data["high"] - data["low"]
    structural_pivots = alternating_pivots(raw_pivots(data, width=2))
    candidates: list[dict[str, Any]] = []
    for index in range(20, len(data)):
        median_body = float(bodies.iloc[index - 20:index].median())
        median_range = float(ranges.iloc[index - 20:index].median())
        if median_body <= 0 or median_range <= 0:
            continue
        body_ratio = float(bodies.iloc[index] / median_body)
        range_ratio = float(ranges.iloc[index] / median_range)
        if body_ratio < 2.0 or range_ratio < 1.5:
            continue
        bar = data.iloc[index]
        direction = "bullish" if float(bar["close"]) > float(bar["open"]) else "bearish"
        relevant_type = "high" if direction == "bullish" else "low"
        prior = [
            pivot for pivot in structural_pivots
            if pivot["type"] == relevant_type and pivot["index"] < index
        ]
        if not prior:
            continue
        broken_pivot = prior[-1]
        broke_structure = (
            float(bar["close"]) > float(broken_pivot["price"])
            if direction == "bullish"
            else float(bar["close"]) < float(broken_pivot["price"])
        )
        if not broke_structure:
            continue
        candidates.append({
            "index": index,
            "time": bar["_time"].isoformat(),
            "direction": direction,
            "body_ratio": round(body_ratio, 2),
            "range_ratio": round(range_ratio, 2),
            "broken_pivot": dict(broken_pivot),
        })
    return candidates


def active_leg(data: pd.DataFrame) -> dict[str, Any]:
    candidates = displacement_candidates(data)
    if not candidates:
        start_index = max(0, len(data) - 120)
        return {
            "confirmed": False,
            "direction": "unknown",
            "start_index": start_index,
            "start_time": data.iloc[start_index]["_time"].isoformat(),
            "reason": "no_displacement_with_structure_break",
        }
    displacement = candidates[-1]
    displacement_index = int(displacement["index"])
    opposite_type = "low" if displacement["direction"] == "bullish" else "high"
    pivots = alternating_pivots(raw_pivots(data, width=1))
    anchors = [
        pivot for pivot in pivots
        if pivot["type"] == opposite_type and pivot["index"] < displacement_index
    ]
    if anchors:
        anchor = anchors[-1]
        start_index = int(anchor["index"])
    else:
        start_index = max(0, displacement_index - 20)
        bar = data.iloc[start_index]
        anchor = {
            "index": start_index,
            "time": bar["_time"].isoformat(),
            "type": opposite_type,
            "price": float(bar["low"] if opposite_type == "low" else bar["high"]),
        }
    return {
        "confirmed": True,
        "direction": displacement["direction"],
        "start_index": start_index,
        "start_time": data.iloc[start_index]["_time"].isoformat(),
        "anchor": anchor,
        "displacement": displacement,
        "reason": "displacement_closed_through_structural_pivot",
    }


def candle_liquidity(data: pd.DataFrame) -> dict[str, Any]:
    previous = data.iloc[-2]
    latest = data.iloc[-1]
    took_previous_high = float(latest["high"]) > float(previous["high"])
    took_previous_low = float(latest["low"]) < float(previous["low"])
    return {
        "latest_closed_time": latest["_time"].isoformat(),
        "previous_high": float(previous["high"]),
        "previous_low": float(previous["low"]),
        "latest_high": float(latest["high"]),
        "latest_low": float(latest["low"]),
        "latest_close": float(latest["close"]),
        "took_previous_high": took_previous_high,
        "took_previous_low": took_previous_low,
        "closed_back_below_previous_high": took_previous_high and float(latest["close"]) < float(previous["high"]),
        "closed_back_above_previous_low": took_previous_low and float(latest["close"]) > float(previous["low"]),
        "meaning": "candle_liquidity_only_not_structure_break",
    }


def structural_state(data: pd.DataFrame, layer: dict[str, Any]) -> str:
    protected = layer.get("protected_swing")
    if not isinstance(protected, dict):
        return "unconfirmed"
    last_close = float(data.iloc[-1]["close"])
    price = float(protected["price"])
    if layer.get("bias") == "bullish":
        return "broken" if last_close < price else "protected"
    if layer.get("bias") == "bearish":
        return "broken" if last_close > price else "protected"
    return "transition"


def analyse_frame(data: pd.DataFrame, timeframe: str) -> dict[str, Any]:
    leg = active_leg(data)
    leg_data = data.iloc[int(leg["start_index"]):].reset_index(drop=True)
    # Keep enough context for confirmed pivots if the active leg is very new.
    if len(leg_data) < 7:
        leg_data = data.tail(40).reset_index(drop=True)
    internal = structure_layer(leg_data, width=1)
    structural = structure_layer(data, width=2)
    internal["state"] = structural_state(data, internal)
    structural["state"] = structural_state(data, structural)
    delivery_direction = str(leg.get("direction") or "unknown")
    anchor = leg.get("anchor") if isinstance(leg.get("anchor"), dict) else None
    last_close = float(data.iloc[-1]["close"])
    delivery_broken = False
    if anchor and delivery_direction == "bullish":
        delivery_broken = last_close < float(anchor["price"])
    elif anchor and delivery_direction == "bearish":
        delivery_broken = last_close > float(anchor["price"])
    if delivery_direction not in {"bullish", "bearish"}:
        delivery_state = "unconfirmed"
        current_phase = "unconfirmed"
    elif delivery_broken:
        delivery_state = "broken"
        current_phase = "delivery_broken"
    else:
        delivery_state = "active"
        internal_bias = str(internal.get("bias"))
        if internal_bias == delivery_direction:
            current_phase = "continuation"
        elif internal_bias in {"transition", "insufficient"}:
            current_phase = "pullback_or_transition"
        else:
            current_phase = "counter_move"
    delivery_protected = protected_swing(internal.get("recent_pivots") or [], delivery_direction)
    if delivery_protected is None and anchor is not None:
        delivery_protected = dict(anchor)
    return {
        "timeframe": timeframe,
        "bars_used": int(len(data)),
        "last_close": float(data.iloc[-1]["close"]),
        "active_leg": leg,
        "delivery_direction": delivery_direction,
        "delivery_state": delivery_state,
        "current_phase": current_phase,
        "delivery_protected_swing": delivery_protected,
        "candle_liquidity": candle_liquidity(data),
        "internal_structure": internal,
        "structural_structure": structural,
    }


def nested_parent_check(
    parent: pd.DataFrame,
    child: pd.DataFrame,
    parent_tf: str,
    child_tf: str,
    expected_children: int,
) -> dict[str, Any]:
    latest = parent.iloc[-1]
    start = latest["_time"]
    durations = {"W1": pd.Timedelta(days=7), "D1": pd.Timedelta(days=1), "H4": pd.Timedelta(hours=4)}
    end = start + durations[parent_tf]
    subset = child[(child["_time"] >= start) & (child["_time"] < end)].copy()
    parent_direction = (
        "bullish" if float(latest["close"]) > float(latest["open"])
        else "bearish" if float(latest["close"]) < float(latest["open"])
        else "neutral"
    )
    if subset.empty:
        child_direction = "unavailable"
        bullish_bodies = bearish_bodies = 0
        close_location = None
    else:
        child_direction = (
            "bullish" if float(subset.iloc[-1]["close"]) > float(subset.iloc[0]["open"])
            else "bearish" if float(subset.iloc[-1]["close"]) < float(subset.iloc[0]["open"])
            else "neutral"
        )
        bullish_bodies = int((subset["close"] > subset["open"]).sum())
        bearish_bodies = int((subset["close"] < subset["open"]).sum())
        child_range = float(subset["high"].max() - subset["low"].min())
        close_location = (
            round((float(subset.iloc[-1]["close"]) - float(subset["low"].min())) / child_range, 3)
            if child_range > 0 else None
        )
    complete = len(subset) >= expected_children
    aligned = parent_direction == child_direction and parent_direction in {"bullish", "bearish"}
    return {
        "parent_timeframe": parent_tf,
        "child_timeframe": child_tf,
        "parent_start": start.isoformat(),
        "parent_direction": parent_direction,
        "child_direction": child_direction,
        "child_count": int(len(subset)),
        "expected_child_count": expected_children,
        "complete": complete,
        "bullish_child_bodies": bullish_bodies,
        "bearish_child_bodies": bearish_bodies,
        "child_close_location": close_location,
        "aligned": aligned,
    }


def timeframe_sync(timeframes: dict[str, Any], nested_pa: dict[str, Any]) -> dict[str, Any]:
    delivery = {tf: str(timeframes[tf].get("delivery_direction") or "unknown") for tf in ("D1", "H4", "H1")}
    d1, h4, h1 = delivery["D1"], delivery["H4"], delivery["H1"]
    directional = {"bullish", "bearish"}
    if {d1, h4, h1} <= directional and d1 == h4 == h1:
        delivery_status = f"ALIGNED_{d1.upper()}"
        delivery_interpretation = f"D1, H4 and H1 active delivery is synchronized {d1}."
    elif {d1, h4, h1} <= directional and d1 == h4 and h1 != h4:
        delivery_status = "H1_PULLBACK"
        delivery_interpretation = f"H1 {h1} delivery is a pullback inside D1/H4 {d1} delivery."
    elif {d1, h4, h1} <= directional and h4 == h1 and d1 != h4:
        delivery_status = "D1_COUNTER_DELIVERY"
        delivery_interpretation = f"H1/H4 {h4} delivery is counter to current D1 {d1} delivery."
    elif any(value not in directional for value in (d1, h4, h1)):
        delivery_status = "TRANSITION"
        delivery_interpretation = "At least one timeframe has no confirmed active delivery."
    else:
        delivery_status = "CONFLICT"
        delivery_interpretation = "Active delivery is mixed without a clean parent-child hierarchy."

    # A stale/broken weekly leg cannot define the external narrative. Prefer
    # protected W1 structure; use live delivery only while that leg is active.
    w1 = timeframes["W1"]
    w1_structural = str((w1.get("structural_structure") or {}).get("bias") or "unknown")
    w1_structural_state = str((w1.get("structural_structure") or {}).get("state") or "unknown")
    w1_delivery = str(w1.get("delivery_direction") or "unknown")
    w1_delivery_state = str(w1.get("delivery_state") or "unknown")
    w1_internal = str((w1.get("internal_structure") or {}).get("bias") or "unknown")
    if w1_structural in directional and w1_structural_state == "protected":
        w1_narrative, w1_narrative_source = w1_structural, "protected_structural_bias"
    elif w1_delivery in directional and w1_delivery_state == "active":
        w1_narrative, w1_narrative_source = w1_delivery, "active_delivery"
    elif w1_internal in directional:
        w1_narrative, w1_narrative_source = w1_internal, "internal_structure_fallback"
    else:
        w1_narrative, w1_narrative_source = "unknown", "unconfirmed"
    d1_structure = str(timeframes["D1"]["structural_structure"].get("bias") or "unknown")
    h4_structure = str(timeframes["H4"]["structural_structure"].get("bias") or "unknown")
    if d1_structure == h4_structure and d1_structure in directional:
        structure_status = f"ALIGNED_{d1_structure.upper()}"
    elif d1 in directional and d1 != d1_structure and h4 == d1 and h1 == d1:
        structure_status = "REVERSAL_BUILDING"
    elif d1_structure in directional and h4_structure in directional:
        structure_status = "STRUCTURAL_CONFLICT"
    else:
        structure_status = "STRUCTURE_TRANSITION"

    if structure_status == "REVERSAL_BUILDING":
        interpretation = (
            f"{d1.capitalize()} delivery is synchronized D1->H1, while D1 structural bias remains "
            f"{d1_structure}; structural reversal is building, not confirmed."
        )
    else:
        interpretation = delivery_interpretation
    return {
        "weekly_narrative": w1_narrative,
        "weekly_narrative_source": w1_narrative_source,
        "delivery_sync": {"status": delivery_status, **delivery, "interpretation": delivery_interpretation},
        "structure_sync": {
            "status": structure_status,
            "D1": d1_structure,
            "H4": h4_structure,
        },
        "nested_pa": nested_pa,
        "interpretation": interpretation,
        "scoring_enabled": False,
    }


def compact_console(result: dict[str, Any]) -> None:
    for timeframe in TIMEFRAMES:
        item = result["timeframes"][timeframe]
        internal = item["internal_structure"]
        structural = item["structural_structure"]
        candle = item["candle_liquidity"]
        internal_protected = internal.get("protected_swing") or {}
        structural_protected = structural.get("protected_swing") or {}
        print(
            f"{timeframe}: internal_pattern={internal['bias']} [{'-'.join(internal['sequence'])}] "
            f"| protected={internal.get('state')} @ {internal_protected.get('price', 'none')}"
        )
        print(
            f"    delivery={item.get('delivery_direction')} | state={item.get('delivery_state')} "
            f"| phase={item.get('current_phase')} | from={item['active_leg'].get('start_time')}"
        )
        print(
            f"    structural={structural['bias']} [{'-'.join(structural['sequence'])}] "
            f"| protected={structural.get('state')} @ {structural_protected.get('price', 'none')}"
        )
        print(
            f"    last_candle | took_prev_high={candle['took_previous_high']} "
            f"| took_prev_low={candle['took_previous_low']} "
            "| meaning=candle_liquidity_only"
        )
    print("NESTED_PA:")
    for key, check in result["nested_pa"].items():
        print(
            f"    {key}: children={check['child_count']}/{check['expected_child_count']} "
            f"| complete={check['complete']} | parent={check['parent_direction']} "
            f"| child_delivery={check['child_direction']} | aligned={check['aligned']}"
        )
    sync = result["timeframe_sync"]
    delivery_sync = sync["delivery_sync"]
    structure_sync = sync["structure_sync"]
    print(
        f"W1_NARRATIVE: {sync['weekly_narrative']}"
    )
    print(
        f"DELIVERY_SYNC: {delivery_sync['status']} | D1={delivery_sync['D1']} "
        f"| H4={delivery_sync['H4']} | H1={delivery_sync['H1']}"
    )
    print(
        f"STRUCTURE_SYNC: {structure_sync['status']} | D1={structure_sync['D1']} "
        f"| H4={structure_sync['H4']}"
    )
    print(f"    {sync['interpretation']}")


def main() -> None:
    timeframes: dict[str, Any] = {}
    frames: dict[str, pd.DataFrame] = {}
    for timeframe in TIMEFRAMES:
        data = load_closed_bars(RAW_DIR / f"{timeframe}.csv")
        frames[timeframe] = data
        timeframes[timeframe] = analyse_frame(data, timeframe)
    nested_pa = {
        f"{parent}<-{child}": nested_parent_check(
            frames[parent], frames[child], parent, child, expected
        )
        for parent, child, expected in PARENT_CHILD
    }
    result = {
        "schema_version": 2,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "purpose": "Separate delivery, structure and nested parent-child price action.",
        "timeframes": timeframes,
        "nested_pa": nested_pa,
        "timeframe_sync": timeframe_sync(timeframes, nested_pa),
    }
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    compact_console(result)
    print(f"ORDER_FLOW_SAVED: {OUTPUT_FILE}")
    print("ORDER_FLOW_STRUCTURE_ENGINE: PASSED")


if __name__ == "__main__":
    main()
