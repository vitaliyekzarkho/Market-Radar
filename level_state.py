from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


SYMBOL = "GOLD.pro"
SYMBOL_FILE = SYMBOL.replace(".", "_")
RAW_DIR = Path("data") / "raw" / SYMBOL_FILE
LEVELS_FILE = Path("data") / "levels" / f"{SYMBOL_FILE}.json"
FEATURES_FILE = Path("data") / "features" / f"{SYMBOL_FILE}.json"
OUTPUT_FILE = Path("data") / "states" / f"{SYMBOL_FILE}.json"

# State is evaluated mainly from closed H1 bars. M15 is used for fresh reaction clues.
PRIMARY_TF = "H1"
FAST_TF = "M15"
LOOKBACK_BARS = 160
TOUCH_ATR = 0.08
SWEEP_ATR = 0.04
ACCEPT_ATR = 0.08
REACTION_ATR = 0.35
ACCEPT_CLOSES = 2


@dataclass
class MarketObject:
    object_id: str
    kind: str
    name: str
    timeframe: str | None
    low: float
    high: float
    directional_role: str
    source: str
    active_from: str | None = None
    fill_status: str | None = None
    validity_status: str | None = None
    invalidation_time: str | None = None


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        value = json.load(fh)
    return value if isinstance(value, dict) else {}


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def first_number(item: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in item:
            number = finite_number(item[key])
            if number is not None:
                return number
    return None


def text_value(item: dict[str, Any], keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def normalise_role(text: str) -> str:
    text = text.lower()
    if any(word in text for word in ("bull", "support", "demand", "low", "ssl")):
        return "support"
    if any(word in text for word in ("bear", "resistance", "supply", "high", "bsl")):
        return "resistance"
    return "neutral"


def add_object(objects: list[MarketObject], obj: MarketObject) -> None:
    if obj.low > obj.high:
        obj.low, obj.high = obj.high, obj.low
    if obj.low <= 0 or obj.high <= 0:
        return
    key = (obj.kind, obj.name, obj.timeframe, round(obj.low, 5), round(obj.high, 5))
    for old in objects:
        old_key = (old.kind, old.name, old.timeframe, round(old.low, 5), round(old.high, 5))
        if old_key == key:
            return
    objects.append(obj)


def extract_key_levels(data: dict[str, Any]) -> list[MarketObject]:
    objects: list[MarketObject] = []
    allowed = {"PDH", "PDL", "PWH", "PWL", "PMH", "PML", "DO", "WO", "MO"}

    for item in walk(data):
        # Shape A: {"name": "PDL", "price": 4253.68}
        name = text_value(item, ("name", "label", "level_name", "type")).upper()
        price = first_number(item, ("price", "value", "level"))
        if name in allowed and price is not None:
            role = "support" if name.endswith("L") else "resistance" if name.endswith("H") else "neutral"
            add_object(objects, MarketObject(
                f"KEY:{name}", "key_level", name, None, price, price, role, "key_levels"
            ))

        # Shape B: {"PDL": 4253.68, "PWL": 4292.11, ...}
        for key, value in item.items():
            upper = str(key).upper()
            number = finite_number(value)
            if upper in allowed and number is not None:
                role = "support" if upper.endswith("L") else "resistance" if upper.endswith("H") else "neutral"
                add_object(objects, MarketObject(
                    f"KEY:{upper}", "key_level", upper, None, number, number, role, "key_levels"
                ))
    return objects


def extract_imbalances(data: dict[str, Any]) -> list[MarketObject]:
    objects: list[MarketObject] = []

    collection_types = {
        "fvgs": ("fvg", "FVG"),
        "volume_imbalances": (
            "volume_imbalance",
            "Volume Imbalance",
        ),
        "gaps": ("gap", "GAP"),
    }

    def consume(
        item: dict[str, Any],
        tf_hint: str,
        base_kind: str,
        display_name: str,
    ) -> None:
        item_type = text_value(
            item,
            ("type", "kind", "feature"),
            base_kind,
        ).lower()
        if item_type != base_kind:
            return

        low = first_number(item, ("low", "bottom", "lower", "zone_low", "price_low"))
        high = first_number(item, ("high", "top", "upper", "zone_high", "price_high"))
        if low is None or high is None or abs(high - low) < 1e-9:
            return

        tf = text_value(item, ("timeframe", "tf"), tf_hint).upper()
        direction = text_value(item, ("direction", "side", "bias", "type", "kind"))
        role = normalise_role(direction)
        direction_name = "bullish" if role == "support" else "bearish" if role == "resistance" else "neutral"
        created = text_value(item, ("time", "created_at", "start_time", "timestamp"), "na")
        fill_status = text_value(item, ("fill_status", "status"), "unknown").lower()
        validity = text_value(item, ("validity_status",), "valid").lower()
        invalidation_time = text_value(item, ("invalidation_time",), "") or None
        kind = (
            "invalidated_fvg"
            if base_kind == "fvg" and validity == "invalidated"
            else base_kind
        )
        object_id = (
            f"{kind.upper()}:{tf}:{direction_name}:{created}:"
            f"{min(low, high):.5f}:{max(low, high):.5f}"
        )
        add_object(objects, MarketObject(
            object_id,
            kind,
            f"{tf} {direction_name} {display_name}",
            tf,
            min(low, high),
            max(low, high),
            role,
            "market_features",
            created,
            fill_status,
            validity,
            invalidation_time,
        ))

    timeframes = data.get("timeframes")
    if isinstance(timeframes, dict):
        for tf_name, tf_payload in timeframes.items():
            if not isinstance(tf_payload, dict):
                continue
            for collection_name, (
                base_kind,
                display_name,
            ) in collection_types.items():
                collection = tf_payload.get(collection_name, [])
                if not isinstance(collection, list):
                    continue
                for item in collection:
                    if isinstance(item, dict):
                        consume(
                            item,
                            str(tf_name).upper(),
                            base_kind,
                            display_name,
                        )
    return objects


def load_bars(tf: str, closed_only: bool = True) -> pd.DataFrame:
    path = RAW_DIR / f"{tf}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing candle file: {path}")
    frame = pd.read_csv(path)
    frame.columns = [str(col).strip().lower() for col in frame.columns]
    required = {"time", "open", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    # MT5 exports Unix seconds in `time`. Parsing them without a unit makes
    # pandas treat them as nanoseconds and produces false 1970 timestamps.
    if "utc_time" in frame.columns:
        parsed_utc = pd.to_datetime(frame["utc_time"], utc=True, errors="coerce")
    else:
        parsed_utc = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
    numeric_time = pd.to_numeric(frame["time"], errors="coerce")
    parsed_epoch = pd.to_datetime(numeric_time, unit="s", utc=True, errors="coerce")
    frame["time"] = parsed_utc.fillna(parsed_epoch)
    for col in ("open", "high", "low", "close"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if closed_only and "is_closed" in frame.columns:
        mask = frame["is_closed"].astype(str).str.lower().isin(("true", "1", "yes"))
        frame = frame.loc[mask]
    frame = frame.dropna(subset=["time", "open", "high", "low", "close"])
    return frame.sort_values("time").tail(LOOKBACK_BARS).reset_index(drop=True)


def atr(frame: pd.DataFrame, period: int = 14) -> float:
    previous_close = frame["close"].shift(1)
    true_range = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous_close).abs(),
        (frame["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    value = finite_number(true_range.tail(period).median())
    if value is None or value <= 0:
        value = finite_number((frame["high"] - frame["low"]).tail(period).median())
    return value or 1.0


def candle_record(row: pd.Series | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "time": row["time"].isoformat(),
        "open": round(float(row["open"]), 5),
        "high": round(float(row["high"]), 5),
        "low": round(float(row["low"]), 5),
        "close": round(float(row["close"]), 5),
    }


def last_matching(frame: pd.DataFrame, mask: pd.Series) -> pd.Series | None:
    matches = frame.loc[mask]
    return None if matches.empty else matches.iloc[-1]


def classify_object(
    obj: MarketObject,
    frame: pd.DataFrame,
    accept_closes: int = ACCEPT_CLOSES,
    live_price: float | None = None,
) -> dict[str, Any]:
    if frame.empty:
        zone_mid = (obj.low + obj.high) / 2
        current_market_price = live_price if live_price is not None else zone_mid
        if current_market_price > obj.high:
            current_position = "above"
        elif current_market_price < obj.low:
            current_position = "below"
        else:
            current_position = "inside_or_at"
        if obj.validity_status == "invalidated":
            state = "invalidated"
        elif obj.fill_status == "fulfilled":
            state = "fulfilled_valid"
        elif obj.fill_status == "partial":
            state = "partially_mitigated"
        else:
            state = "awaiting_closed_observation"
        return {
            **asdict(obj),
            "midpoint": round(zone_mid, 5),
            "state": state,
            "current_position": current_position,
            "zone_condition": state,
            "current_price": round(current_market_price, 5),
            "last_closed_price": None,
            "distance_percent": round((zone_mid / current_market_price - 1) * 100, 4),
            "first_touch": None,
            "last_sweep_above": None,
            "last_sweep_below": None,
            "accepted_above_now": False,
            "accepted_below_now": False,
            "reclaimed_from_below": False,
            "reclaimed_from_above": False,
            "bullish_reaction": False,
            "bearish_reaction": False,
            "atr": None,
            "accept_closes_required": accept_closes,
        }

    current_atr = atr(frame)
    touch_tolerance = current_atr * TOUCH_ATR
    sweep_tolerance = current_atr * SWEEP_ATR
    accept_tolerance = current_atr * ACCEPT_ATR

    low_edge, high_edge = obj.low, obj.high
    zone_mid = (low_edge + high_edge) / 2
    is_zone = high_edge - low_edge > current_atr * 0.02

    touched_mask = (frame["high"] >= low_edge - touch_tolerance) & (frame["low"] <= high_edge + touch_tolerance)
    touch_indices = frame.index[touched_mask].tolist()
    first_touch_index = touch_indices[0] if touch_indices else None
    first_touch = frame.loc[first_touch_index] if first_touch_index is not None else None
    after_touch = frame.loc[first_touch_index:] if first_touch_index is not None else frame.iloc[0:0]

    closes_above = frame["close"] > high_edge + accept_tolerance
    closes_below = frame["close"] < low_edge - accept_tolerance
    accepted_above_now = bool(len(frame) >= accept_closes and closes_above.tail(accept_closes).all())
    accepted_below_now = bool(len(frame) >= accept_closes and closes_below.tail(accept_closes).all())

    swept_above_mask = touched_mask & (frame["high"] > high_edge + sweep_tolerance) & (frame["close"] <= high_edge)
    swept_below_mask = touched_mask & (frame["low"] < low_edge - sweep_tolerance) & (frame["close"] >= low_edge)
    swept_above = bool(swept_above_mask.any())
    swept_below = bool(swept_below_mask.any())

    # Reclaim means price traded through an edge and later closed back across it.
    reclaimed_from_below = False
    reclaimed_from_above = False
    if first_touch_index is not None:
        below_seen = False
        above_seen = False
        for _, row in after_touch.iterrows():
            if row["close"] < low_edge - accept_tolerance:
                below_seen = True
            if below_seen and row["close"] > high_edge:
                reclaimed_from_below = True
            if row["close"] > high_edge + accept_tolerance:
                above_seen = True
            if above_seen and row["close"] < low_edge:
                reclaimed_from_above = True

    last_closed_price = float(frame.iloc[-1]["close"])
    current_market_price = live_price if live_price is not None else last_closed_price
    if current_market_price > high_edge + accept_tolerance:
        current_position = "above"
    elif current_market_price < low_edge - accept_tolerance:
        current_position = "below"
    else:
        current_position = "inside_or_at"
    max_after = float(after_touch["high"].max()) if not after_touch.empty else None
    min_after = float(after_touch["low"].min()) if not after_touch.empty else None
    bullish_reaction = bool(min_after is not None and current_market_price - min_after >= current_atr * REACTION_ATR)
    bearish_reaction = bool(max_after is not None and max_after - current_market_price >= current_atr * REACTION_ATR)

    if obj.validity_status == "invalidated":
        state = "invalidated"
    elif first_touch_index is None:
        if obj.fill_status == "fulfilled":
            state = "fulfilled_valid"
        elif obj.fill_status == "partial":
            state = "partially_mitigated"
        else:
            state = "untouched"
    # Live transition has priority over an older closed-candle state. The
    # accepted_above/below flags below still preserve that confirmed history.
    elif current_position == "below" and obj.directional_role == "support" and not accepted_below_now:
        state = "testing_below"
    elif current_position == "above" and obj.directional_role == "resistance" and not accepted_above_now:
        state = "testing_above"
    elif accepted_above_now:
        state = "accepted_above"
    elif accepted_below_now:
        state = "accepted_below"
    elif reclaimed_from_below:
        state = "reclaimed_above"
    elif reclaimed_from_above:
        state = "reclaimed_below"
    elif obj.directional_role == "support" and swept_below:
        state = "swept_rejected" if bullish_reaction else "swept"
    elif obj.directional_role == "resistance" and swept_above:
        state = "swept_rejected" if bearish_reaction else "swept"
    elif swept_above or swept_below:
        state = "swept"
    elif bullish_reaction or bearish_reaction:
        state = "reaction_building"
    else:
        state = "touched"

    if is_zone:
        overlap = ((frame["high"] >= low_edge) & (frame["low"] <= high_edge)).sum()
        closed_through = accepted_below_now if obj.directional_role == "support" else accepted_above_now
        if obj.validity_status == "invalidated" or closed_through:
            zone_condition = "failed"
        elif obj.fill_status == "fulfilled":
            zone_condition = "fulfilled_valid"
        elif obj.fill_status == "partial":
            zone_condition = "partially_mitigated"
        elif overlap:
            zone_condition = "mitigated"
        else:
            zone_condition = "untested"
    else:
        zone_condition = None

    return {
        **asdict(obj),
        "midpoint": round(zone_mid, 5),
        "state": state,
        "current_position": current_position,
        "zone_condition": zone_condition,
        "current_price": round(current_market_price, 5),
        "last_closed_price": round(last_closed_price, 5),
        "distance_percent": round((zone_mid / current_market_price - 1) * 100, 4),
        "first_touch": candle_record(first_touch),
        "last_sweep_above": candle_record(last_matching(frame, swept_above_mask)),
        "last_sweep_below": candle_record(last_matching(frame, swept_below_mask)),
        "accepted_above_now": accepted_above_now,
        "accepted_below_now": accepted_below_now,
        "reclaimed_from_below": reclaimed_from_below,
        "reclaimed_from_above": reclaimed_from_above,
        "bullish_reaction": bullish_reaction,
        "bearish_reaction": bearish_reaction,
        "atr": round(current_atr, 5),
        "accept_closes_required": accept_closes,
    }


def layer_summary(result: dict[str, Any], timeframe: str) -> dict[str, Any]:
    return {
        "timeframe": timeframe,
        "state": result["state"],
        "current_position": result["current_position"],
        "accepted_above": result["accepted_above_now"],
        "accepted_below": result["accepted_below_now"],
        "bullish_reaction": result["bullish_reaction"],
        "bearish_reaction": result["bearish_reaction"],
        "last_sweep_above": result["last_sweep_above"],
        "last_sweep_below": result["last_sweep_below"],
        "accept_closes_required": result["accept_closes_required"],
    }


def lifecycle_start(obj: MarketObject, anchors: dict[str, pd.Timestamp]) -> pd.Timestamp | None:
    if obj.kind in {
        "fvg",
        "invalidated_fvg",
        "volume_imbalance",
        "gap",
    } and obj.active_from and obj.active_from != "na":
        return pd.to_datetime(obj.active_from, utc=True, errors="coerce")
    name = obj.name.upper()
    if name.startswith("PD") or name == "DO":
        return anchors.get("day")
    if name.startswith("PW") or name == "WO":
        return anchors.get("week")
    if name.startswith("PM") or name == "MO":
        return anchors.get("month")
    return None


def object_window(
    obj: MarketObject,
    frame: pd.DataFrame,
    anchors: dict[str, pd.Timestamp],
) -> pd.DataFrame:
    start = lifecycle_start(obj, anchors)
    if start is None or pd.isna(start):
        return frame
    # The candle stamped active_from participates in creating the imbalance;
    # it is not a later retest and must not confirm its own FVG.
    strict_after_creation = obj.kind in {
        "fvg",
        "invalidated_fvg",
        "volume_imbalance",
        "gap",
    }
    selected = frame.loc[frame["time"] > start].copy() if strict_after_creation else frame.loc[frame["time"] >= start].copy()
    if strict_after_creation:
        return selected
    return selected if not selected.empty else frame.tail(2)


def native_rejection_confirmation(
    obj: MarketObject,
    frame: pd.DataFrame,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """Confirm only a CLOSED native candle that retests and rejects the zone."""
    if frame.empty or obj.directional_role not in {"support", "resistance"}:
        return False, None, None

    touched = (frame["high"] >= obj.low) & (frame["low"] <= obj.high)
    if obj.directional_role == "resistance":
        rejected = touched & (frame["close"] < obj.low)
        direction = "bearish"
    else:
        rejected = touched & (frame["close"] > obj.high)
        direction = "bullish"

    candle = last_matching(frame, rejected)
    return candle is not None, direction, candle_record(candle)


def build_anchors(primary: pd.DataFrame) -> dict[str, pd.Timestamp]:
    # Forming D1/W1 bars contain the actual current session start timestamps.
    d1 = load_bars("D1", closed_only=False)
    w1 = load_bars("W1", closed_only=False)
    latest = primary.iloc[-1]["time"]
    month_start = pd.Timestamp(
        year=latest.year,
        month=latest.month,
        day=1,
        tz="UTC",
    )
    return {
        "day": d1.iloc[-1]["time"],
        "week": w1.iloc[-1]["time"],
        "month": month_start,
    }


def enrich_with_fast_reaction(result: dict[str, Any], fast: pd.DataFrame) -> None:
    low_edge = float(result["low"])
    high_edge = float(result["high"])
    current_atr = atr(fast)
    recent = fast.tail(8)
    touched = recent[(recent["high"] >= low_edge) & (recent["low"] <= high_edge)]
    if touched.empty:
        result["recent_m15"] = {"timeframe": FAST_TF, "recent_touch": False}
        return
    since = recent.loc[touched.index[0]:]
    last_close = float(recent.iloc[-1]["close"])
    result["recent_m15"] = {
        "timeframe": FAST_TF,
        "recent_touch": True,
        "bullish_rejection": bool(last_close - float(since["low"].min()) >= current_atr * REACTION_ATR),
        "bearish_rejection": bool(float(since["high"].max()) - last_close >= current_atr * REACTION_ATR),
        "last_close": round(last_close, 5),
    }


def print_summary(states: list[dict[str, Any]]) -> None:
    print(f"CURRENT_PRICE: {states[0]['current_price'] if states else 'no data'}")
    print("\nKEY LEVEL STATES:")
    for item in sorted((x for x in states if x["kind"] == "key_level"), key=lambda x: x["low"], reverse=True):
        print(f"{item['name']:>4} | {item['low']:.5f} | {item['state']}")

    sections = (
        ("fvg", "NEAREST VALID H1 FVG STATES", 8),
        (
            "volume_imbalance",
            "NEAREST VALID H1 VOLUME IMBALANCE STATES",
            5,
        ),
        ("gap", "NEAREST VALID H1 GAP STATES", 5),
    )
    for kind, title, limit in sections:
        print(f"\n{title}:")
        items = [
            x
            for x in states
            if x["kind"] == kind
            and x["timeframe"] == "H1"
            and x.get("validity_status") == "valid"
        ]
        items.sort(key=lambda x: abs(x["distance_percent"]))
        if not items:
            print("none")
            continue
        for item in items[:limit]:
            print(
                f"{item['name']:<28} | "
                f"{item['low']:.5f}-{item['high']:.5f} | "
                f"fill={item.get('fill_status', 'unknown'):<9} | "
                f"{item['state']} | "
                f"{item['distance_percent']:+.3f}%"
            )


def main() -> None:
    levels = load_json(LEVELS_FILE)
    features = load_json(FEATURES_FILE)
    objects = extract_key_levels(levels) + extract_imbalances(features)
    if not objects:
        raise RuntimeError(
            "No key levels or imbalance objects found in input JSON files"
        )

    primary = load_bars(PRIMARY_TF)
    fast = load_bars(FAST_TF)
    live_frame = load_bars(FAST_TF, closed_only=False)
    live_price = float(live_frame.iloc[-1]["close"])
    frame_cache: dict[str, pd.DataFrame] = {
        PRIMARY_TF: primary,
        FAST_TF: fast,
    }
    for timeframe in ("W1", "D1", "H4"):
        frame_cache[timeframe] = load_bars(timeframe)
    anchors = build_anchors(primary)
    states = []
    for obj in objects:
        h1_window = object_window(obj, primary, anchors)
        state = classify_object(obj, h1_window, accept_closes=2, live_price=live_price)

        # H1 is observation context, never an automatic entry instruction.
        state["observation_h1"] = layer_summary(state, "H1")

        # M15 is the lower observation/possible execution layer. It still does
        # not place or recommend a trade by itself.
        m15_window = object_window(obj, fast, anchors)
        m15_result = classify_object(obj, m15_window, accept_closes=2, live_price=live_price)
        state["observation_m15"] = layer_summary(m15_result, "M15")

        # Native confirmation is separated from lower-timeframe acceptance.
        # W1/D1/H4 require a CLOSED native candle that first trades into the
        # zone and then closes back out in the expected rejection direction.
        native_tf = (
            obj.timeframe
            if obj.kind in {
                "fvg",
                "invalidated_fvg",
                "volume_imbalance",
                "gap",
            }
            else None
        )
        if native_tf in frame_cache:
            if native_tf == "H1":
                native_result = state
                native_closes = 2
                rejection_confirmed = False
                rejection_direction = None
                rejection_candle = None
            else:
                native_window = object_window(obj, frame_cache[native_tf], anchors)
                native_closes = 1
                if native_window.empty:
                    native_result = state
                    rejection_confirmed = False
                    rejection_direction = None
                    rejection_candle = None
                else:
                    native_result = classify_object(
                        obj,
                        native_window,
                        accept_closes=native_closes,
                        live_price=live_price,
                    )
                    rejection_confirmed, rejection_direction, rejection_candle = native_rejection_confirmation(
                        obj,
                        native_window,
                    )
            native_layer = layer_summary(native_result, native_tf)
            if native_tf == "H1":
                native_layer["confirmed"] = native_result["state"] in {
                    "accepted_above",
                    "accepted_below",
                }
            else:
                native_layer["confirmed"] = rejection_confirmed
                if native_window.empty:
                    native_layer["state"] = "awaiting_closed_retest"
            native_layer["rejection_direction"] = rejection_direction
            native_layer["rejection_candle"] = rejection_candle
            state["native_confirmation"] = native_layer
        else:
            state["native_confirmation"] = None
        state["lifecycle_start"] = (
            lifecycle_start(obj, anchors).isoformat()
            if lifecycle_start(obj, anchors) is not None
            and not pd.isna(lifecycle_start(obj, anchors))
            else None
        )
        states.append(state)
    for item in states:
        enrich_with_fast_reaction(item, fast)

    payload = {
        "schema_version": 3,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "primary_timeframe": PRIMARY_TF,
        "fast_timeframe": FAST_TF,
        "live_price": live_price,
        "layer_meaning": {
            "observation_h1": "primary PA observation; not an entry signal",
            "observation_m15": "lower-TF PA and possible execution model observation",
            "native_confirmation": (
                "confirmation by a closed candle of the imbalance timeframe"
            ),
        },
        "rules": {
            "accept_closes": ACCEPT_CLOSES,
            "touch_atr": TOUCH_ATR,
            "sweep_atr": SWEEP_ATR,
            "accept_atr": ACCEPT_ATR,
            "reaction_atr": REACTION_ATR,
        },
        "objects": states,
    }
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print_summary(states)
    print(f"\nSTATES_SAVED: {OUTPUT_FILE}")
    print("LEVEL_STATE_ENGINE: PASSED")


if __name__ == "__main__":
    main()
