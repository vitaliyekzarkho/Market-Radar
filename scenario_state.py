from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from market_reasoning_engine import build_reasoning_state


SYMBOL = "GOLD.pro"
SYMBOL_FILE = SYMBOL.replace(".", "_")
DATA_DIR = Path("data")

STATE_FILE = DATA_DIR / "states" / f"{SYMBOL_FILE}.json"
RANGE_FILE = DATA_DIR / "ranges" / f"{SYMBOL_FILE}.json"
LIQUIDITY_FILE = DATA_DIR / "liquidity" / f"{SYMBOL_FILE}.json"
SESSION_FILE = DATA_DIR / "sessions" / f"{SYMBOL_FILE}.json"
ORDER_FLOW_FILE = DATA_DIR / "order_flow" / f"{SYMBOL_FILE}.json"
NEWS_DRIVER_FILE = DATA_DIR / "news" / f"{SYMBOL_FILE}.json"
USD_CONTEXT_FILE = DATA_DIR / "market_context" / f"{SYMBOL_FILE}.json"
MACRO_CONTEXT_FILE = DATA_DIR / "macro" / f"{SYMBOL_FILE}.json"
PULSE_CONTEXT_FILE = DATA_DIR / "market_context" / f"{SYMBOL_FILE}_pulse.json"
VOLUME_CONTEXT_FILE = DATA_DIR / "market_context" / f"{SYMBOL_FILE}_volume.json"
OUTPUT_FILE = DATA_DIR / "scenario_states" / f"{SYMBOL_FILE}.json"
HYPOTHESIS_RUNTIME_FILE = DATA_DIR / "runtime" / f"{SYMBOL_FILE}_hypothesis_state.json"

# This is deliberately strict. Telegram will later use only confirmed scenarios.
CONFIRMED_SCORE = 75
BUILDING_SCORE = 55
OBSERVE_SCORE = 30
MAX_TARGET_DISTANCE_PERCENT = 4.0
MAX_BLOCKERS_TO_CONFIRM = 0


@dataclass
class Evidence:
    code: str
    text: str
    weight: int
    direction: str
    source: str


def load_json(path: Path, required: bool = True) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Missing required file: {path}")
        return {}
    with path.open("r", encoding="utf-8") as fh:
        value = json.load(fh)
    return value if isinstance(value, dict) else {}


def walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def text(item: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def state_objects(state_data: dict[str, Any]) -> list[dict[str, Any]]:
    value = state_data.get("objects", [])
    return [item for item in value if isinstance(item, dict)]


def current_price(objects: list[dict[str, Any]]) -> float:
    for item in objects:
        value = number(item.get("current_price"))
        if value is not None:
            return value
    raise RuntimeError("Current price not found in level-state objects")


def current_session_names(session_data: dict[str, Any]) -> list[str]:
    raw = session_data.get("active_sessions") or session_data.get("active") or []
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, list):
        return [str(part) for part in raw]
    names: list[str] = []
    for item in walk(session_data):
        status = text(item, "status").lower()
        if status == "active":
            name = text(item, "name", "session")
            if name:
                names.append(name)
    return sorted(set(names))


def session_liquidity_checkpoints(
    session_data: dict[str, Any], direction: str, price: float, target_price: float | None,
) -> list[dict[str, Any]]:
    """Expose nearby session extremes as route context, never as score evidence."""
    if target_price is None:
        return []
    lower, upper = sorted((price, target_price))
    boundary = "high" if direction == "bullish" else "low"
    grouped: dict[float, set[str]] = {}
    sessions = session_data.get("sessions")
    if not isinstance(sessions, list):
        return []
    for session in sessions:
        if not isinstance(session, dict) or session.get("status") not in {"active", "completed"}:
            continue
        market_data = session.get("market_data")
        if not isinstance(market_data, dict):
            continue
        level = number(market_data.get(boundary))
        if level is None or not (lower < level < upper):
            continue
        grouped.setdefault(round(level, 5), set()).add(f"{session.get('name')} {boundary}")
    output = [
        {
            "name": " / ".join(sorted(names)), "kind": "session_liquidity", "timeframe": "session",
            "low": level, "high": level, "price": level, "state": "session liquidity",
            "status": "checkpoint", "requires_acceptance": False,
            "distance_percent": round(abs(level / price - 1) * 100, 4),
            "source": "session_context", "route_role": "TA",
        }
        for level, names in grouped.items()
    ]
    return sorted(output, key=lambda item: item["distance_percent"])


def extract_order_flow_context(order_flow: dict[str, Any]) -> dict[str, Any]:
    """Compact descriptive context. It deliberately contributes zero score."""
    sync = order_flow.get("timeframe_sync")
    sync = sync if isinstance(sync, dict) else {}
    delivery = sync.get("delivery_sync")
    delivery = delivery if isinstance(delivery, dict) else {}
    structure = sync.get("structure_sync")
    structure = structure if isinstance(structure, dict) else {}
    raw_frames = order_flow.get("timeframes")
    raw_frames = raw_frames if isinstance(raw_frames, dict) else {}
    phases: dict[str, dict[str, str]] = {}
    for timeframe in ("D1", "H4", "H1"):
        item = raw_frames.get(timeframe)
        item = item if isinstance(item, dict) else {}
        internal = item.get("internal_structure")
        internal = internal if isinstance(internal, dict) else {}
        structural = item.get("structural_structure")
        structural = structural if isinstance(structural, dict) else {}
        phases[timeframe] = {
            "delivery": str(item.get("delivery_direction") or "unknown"),
            "phase": str(item.get("current_phase") or "unknown"),
            "internal_pattern": str(internal.get("bias") or "unknown"),
            "structural_bias": str(structural.get("bias") or "unknown"),
            "active_leg": item.get("active_leg") if isinstance(item.get("active_leg"), dict) else {},
            "protected_swing": item.get("delivery_protected_swing")
            if isinstance(item.get("delivery_protected_swing"), dict) else {},
            "latest_closed_time": str(
                ((item.get("candle_liquidity") or {}).get("latest_closed_time")) or ""
            ),
        }
    nested = order_flow.get("nested_pa")
    nested = nested if isinstance(nested, dict) else {}
    nested_complete = bool(nested) and all(
        bool(item.get("complete"))
        for item in nested.values()
        if isinstance(item, dict)
    )
    return {
        "available": bool(order_flow),
        "weekly_narrative": str(sync.get("weekly_narrative") or "unknown"),
        "delivery_status": str(delivery.get("status") or "unknown"),
        "structure_status": str(structure.get("status") or "unknown"),
        "interpretation": str(sync.get("interpretation") or ""),
        "phases": phases,
        "nested_pa_complete": nested_complete,
        "scoring_applied": False,
    }


def extract_range_bias(range_data: dict[str, Any]) -> tuple[str, list[Evidence]]:
    weights = {"W1": 4, "D1": 5, "H4": 3, "H1": 1}
    scores = {"bullish": 0, "bearish": 0}
    seen: set[tuple[Any, ...]] = set()
    evidence: list[Evidence] = []

    def consume(item: dict[str, Any], tf_hint: str | None = None) -> None:
        direction = text(item, "direction", "bias").lower()
        if direction not in scores:
            return
        tf = text(item, "timeframe", "tf", default=tf_hint or "").upper()
        if tf not in weights:
            return
        status = text(item, "status", "reaction_status", "state").lower()
        if status and status in {"invalid", "invalidated", "failed", "expired"}:
            return
        start = number(item.get("start")) or number(item.get("low"))
        end = number(item.get("end")) or number(item.get("high"))
        key = (tf, direction, start, end)
        if key in seen:
            return
        seen.add(key)
        weight = weights[tf]
        if status in {"reversal_confirmed", "completed_reaction", "confirmed"}:
            weight += 2
        scores[direction] += weight
        evidence.append(Evidence(
            f"{tf}_RANGE_{direction.upper()}",
            f"{tf} active dealing range is {direction} ({status or 'active'})",
            weight,
            direction,
            "dealing_range_engine",
        ))

    timeframes = range_data.get("timeframes")
    if isinstance(timeframes, dict):
        for tf_name, tf_payload in timeframes.items():
            for item in walk(tf_payload):
                consume(item, str(tf_name))
    else:
        for item in walk(range_data):
            consume(item)

    if scores["bearish"] > scores["bullish"]:
        bias = "bearish"
    elif scores["bullish"] > scores["bearish"]:
        bias = "bullish"
    else:
        bias = "neutral"
    selected = [e for e in evidence if e.direction == bias]
    selected.sort(key=lambda e: e.weight, reverse=True)
    return bias, selected[:5]


def layer_state(item: dict[str, Any], layer: str) -> str:
    value = item.get(layer)
    if isinstance(value, dict):
        return str(value.get("state", ""))
    return ""


def object_timeframe(item: dict[str, Any]) -> str:
    explicit = text(item, "timeframe", "tf").upper()
    if explicit:
        return explicit
    name = text(item, "name").upper()
    return name.split()[0] if name else ""


def h1_broke_zone(item: dict[str, Any], direction: str) -> bool:
    """Return True when a closed H1 acceptance invalidated the zone."""
    h1 = layer_state(item, "observation_h1") or str(item.get("state", ""))
    broken_state = "accepted_above" if direction == "bearish" else "accepted_below"
    return h1 == broken_state


def meaningful_reaction(
    objects: list[dict[str, Any]],
    direction: str,
    price: float,
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
    list[Evidence],
]:
    wanted_role = "resistance" if direction == "bearish" else "support"
    confirmed_origins: list[tuple[float, float, dict[str, Any]]] = []
    confirmed_continuations: list[tuple[float, float, dict[str, Any]]] = []
    pending_h4: list[tuple[float, float, dict[str, Any]]] = []
    for item in objects:
        if item.get("kind") != "fvg" or item.get("directional_role") != wanted_role:
            continue
        tf = object_timeframe(item)
        if tf not in {"W1", "D1", "H4"}:
            continue
        native = item.get("native_confirmation")
        native = native if isinstance(native, dict) else {}
        native_confirmed = bool(native.get("confirmed"))
        native_direction = str(native.get("rejection_direction") or "").lower()
        h1 = layer_state(item, "observation_h1") or str(item.get("state", ""))
        reaction_states = {"swept_rejected", "reaction_building", "reclaimed_above", "reclaimed_below"}
        midpoint = number(item.get("midpoint"))
        if midpoint is None:
            continue
        distance = abs(midpoint / price - 1) * 100
        event = native.get("rejection_candle")
        latest_event = 0.0
        if isinstance(event, dict) and event.get("time"):
            try:
                latest_event = datetime.fromisoformat(str(event["time"]).replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        pair = (latest_event, -distance, item)
        if native_confirmed and native_direction == direction:
            if tf in {"W1", "D1"}:
                confirmed_origins.append(pair)
            elif not h1_broke_zone(item, direction):
                confirmed_continuations.append(pair)
        elif tf == "H4" and h1 in reaction_states:
            pending_h4.append(pair)

    confirmed_origins.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)
    confirmed_continuations.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)
    pending_h4.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)

    origin = confirmed_origins[0][2] if confirmed_origins else (
        confirmed_continuations[0][2] if confirmed_continuations else None
    )
    continuation = confirmed_continuations[0][2] if confirmed_continuations else None
    if continuation is origin:
        continuation = None
    pending = pending_h4[0][2] if pending_h4 else None
    if origin is None:
        return None, continuation, pending, []

    item = origin
    evidence = [Evidence(
        "POINT_A_REACTION",
        f"Closed native rejection from {item.get('name')} {item.get('low')}-{item.get('high')}",
        20,
        direction,
        "level_state",
    )]
    return origin, continuation, pending, evidence


def opposing_fvg_transition(
    objects: list[dict[str, Any]],
    direction: str,
    price: float,
) -> tuple[dict[str, Any] | None, list[Evidence]]:
    opposing_role = "support" if direction == "bearish" else "resistance"
    candidates: list[tuple[float, dict[str, Any]]] = []
    for item in objects:
        if item.get("kind") != "fvg" or item.get("directional_role") != opposing_role:
            continue
        midpoint = number(item.get("midpoint"))
        if midpoint is None:
            continue
        h1 = layer_state(item, "observation_h1") or str(item.get("state", ""))
        relevant = {"testing_below", "accepted_below"} if direction == "bearish" else {"testing_above", "accepted_above"}
        # Scenario transitions are H1-led. M15 is intentionally excluded from
        # hypothesis scoring and remains available only in level-state data.
        if h1 not in relevant:
            continue
        candidates.append((abs(midpoint / price - 1) * 100, item))

    if not candidates:
        return None, []
    candidates.sort(key=lambda pair: pair[0])
    item = candidates[0][1]
    h1 = layer_state(item, "observation_h1") or str(item.get("state", ""))
    accepted = "accepted_below" if direction == "bearish" else "accepted_above"
    testing = "testing_below" if direction == "bearish" else "testing_above"
    evidence: list[Evidence] = []
    if h1 == accepted:
        evidence.append(Evidence("H1_OPPOSING_FVG_LOST", f"H1 accepted through opposing {item.get('name')}", 20, direction, "level_state"))
    elif h1 == testing:
        evidence.append(Evidence("H1_OPPOSING_FVG_TEST", f"H1 is testing through opposing {item.get('name')}", 7, direction, "level_state"))
    return item, evidence


def counter_h1_transition(
    objects: list[dict[str, Any]],
    direction: str,
    price: float,
) -> tuple[dict[str, Any] | None, list[Evidence]]:
    """Penalize a branch when fresh H1 acceptance breaks its HTF FVG."""
    wanted_role = "resistance" if direction == "bearish" else "support"
    candidates: list[tuple[float, dict[str, Any]]] = []
    for item in objects:
        if item.get("kind") != "fvg" or item.get("directional_role") != wanted_role:
            continue
        if object_timeframe(item) not in {"W1", "D1", "H4"}:
            continue
        if not h1_broke_zone(item, direction):
            continue
        midpoint = number(item.get("midpoint"))
        if midpoint is None:
            continue
        candidates.append((abs(midpoint / price - 1) * 100, item))

    if not candidates:
        return None, []
    candidates.sort(key=lambda pair: pair[0])
    item = candidates[0][1]
    return item, [Evidence(
        "H1_COUNTER_TRANSITION",
        f"H1 accepted against {direction} through {item.get('name')}",
        -20,
        direction,
        "level_state",
    )]


def objective_candidates(
    objects: list[dict[str, Any]],
    direction: str,
    price: float,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in objects:
        if item.get("kind") != "key_level":
            continue
        level = number(item.get("low"))
        if level is None:
            continue
        correct_side = level < price if direction == "bearish" else level > price
        if not correct_side:
            continue
        distance = abs(level / price - 1) * 100
        if distance > MAX_TARGET_DISTANCE_PERCENT:
            continue
        state = str(item.get("state", ""))
        if state not in {"untouched", "touched", "testing_above", "testing_below", "reaction_building"}:
            continue
        name = str(item.get("name", "LEVEL"))
        priority = 0
        if direction == "bearish" and name in {"PDL", "PWL", "PML"}:
            priority += {"PDL": 30, "PWL": 20, "PML": 10}[name]
        if direction == "bullish" and name in {"PDH", "PWH", "PMH"}:
            priority += {"PDH": 30, "PWH": 20, "PMH": 10}[name]
        priority += max(0, 10 - int(distance * 3))
        candidates.append({
            "name": name,
            "price": level,
            "distance_percent": round(distance, 4),
            "state": state,
            "priority": priority,
            "source": "key_levels",
        })
    candidates.sort(key=lambda x: (-x["priority"], x["distance_percent"]))
    return candidates


def liquidity_objectives(
    liquidity_data: dict[str, Any],
    direction: str,
    price: float,
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for item in walk(liquidity_data):
        level = number(item.get("price"))
        if level is None:
            level = number(item.get("center"))
        if level is None:
            level = number(item.get("cluster_price"))
        if level is None:
            continue
        side = text(item, "side", "location").lower()
        correct_side = level < price if direction == "bearish" else level > price
        if not correct_side:
            continue
        if side and side not in ({"below", "low", "sell_side", "ssl"} if direction == "bearish" else {"above", "high", "buy_side", "bsl"}):
            continue
        distance = abs(level / price - 1) * 100
        score = number(item.get("score")) or 0
        if distance <= MAX_TARGET_DISTANCE_PERCENT and score >= 10:
            found.append({
                "name": "liquidity_cluster",
                "price": round(level, 5),
                "distance_percent": round(distance, 4),
                "priority": int(score),
                "source": "liquidity_map",
                "evidence": item.get("evidence") or item.get("components") or [],
            })
    unique: dict[float, dict[str, Any]] = {}
    for item in found:
        key = round(float(item["price"]), 2)
        if key not in unique or item["priority"] > unique[key]["priority"]:
            unique[key] = item
    result = list(unique.values())
    result.sort(key=lambda x: (-x["priority"], x["distance_percent"]))
    return result[:8]


def choose_target(
    key_targets: list[dict[str, Any]],
    liquidity_targets: list[dict[str, Any]],
) -> dict[str, Any] | None:
    # A named daily/weekly liquidity objective gets precedence. Nearby clusters
    # are attached later as confluence, not allowed to replace it blindly.
    if key_targets:
        return dict(key_targets[0])
    return dict(liquidity_targets[0]) if liquidity_targets else None


def find_blockers(
    objects: list[dict[str, Any]],
    direction: str,
    price: float,
    target_price: float,
    ignored_ids: set[str],
) -> list[dict[str, Any]]:
    lower, upper = sorted((price, target_price))
    blocker_role = "support" if direction == "bearish" else "resistance"
    blockers: list[dict[str, Any]] = []
    for item in objects:
        if item.get("kind") != "fvg" or item.get("directional_role") != blocker_role:
            continue
        # Only HTF zones may block the structural path. H1/M15 are monitored
        # separately as lower-timeframe price-action development.
        if object_timeframe(item) not in {"W1", "D1", "H4"}:
            continue
        if str(item.get("object_id")) in ignored_ids:
            continue
        zone_low = number(item.get("low"))
        zone_high = number(item.get("high"))
        if zone_low is None or zone_high is None:
            continue
        if zone_high < lower or zone_low > upper:
            continue
        h1 = layer_state(item, "observation_h1") or str(item.get("state", ""))
        m15 = layer_state(item, "observation_m15")
        passed_state = "accepted_below" if direction == "bearish" else "accepted_above"
        if h1 == passed_state:
            continue
        blockers.append({
            "name": item.get("name"),
            "low": zone_low,
            "high": zone_high,
            "h1_state": h1,
            "m15_state": m15,
            "distance_percent": round(min(abs(zone_low / price - 1), abs(zone_high / price - 1)) * 100, 4),
            "width_percent": round(abs(zone_high - zone_low) / price * 100, 4),
        })
    blockers.sort(key=lambda x: x["distance_percent"])
    return blockers


def find_route_checkpoints(
    objects: list[dict[str, Any]],
    direction: str,
    price: float,
    target_price: float,
) -> list[dict[str, Any]]:
    """Return the complete observable route before Point B.

    The first unresolved item is the FTA. Remaining items are Trouble Areas;
    their order is spatial, not a synthetic importance ranking.
    """
    lower, upper = sorted((price, target_price))
    grouped: dict[tuple[str, float, float], list[dict[str, Any]]] = {}
    opposing_role = "resistance" if direction == "bullish" else "support"
    route_objects: list[dict[str, Any]] = []
    for item in objects:
        kind = str(item.get("kind") or "")
        if kind not in {"key_level", "fvg", "volume_imbalance", "gap"}:
            continue
        if str(item.get("validity_status") or "valid") == "invalidated":
            continue
        if kind != "key_level" and str(item.get("directional_role") or "") != opposing_role:
            continue
        # M15 is an internal PA/pulse observer, never a structural route level.
        # It may explain a reaction inside a senior zone, but must not fragment
        # the Telegram route into intrahour noise.
        if kind != "key_level" and object_timeframe(item) == "M15":
            continue
        route_objects.append(item)

    timeframe_rank = {"W1": 4, "D1": 3, "H4": 2, "H1": 1}
    for item in route_objects:
        kind = str(item.get("kind") or "")
        zone_low = number(item.get("low"))
        zone_high = number(item.get("high"))
        if kind == "key_level" and zone_low is not None and zone_high is None:
            zone_high = zone_low
        if zone_low is None or zone_high is None:
            continue
        zone_low, zone_high = sorted((zone_low, zone_high))
        # Include only areas actually crossed before Point B; Point B itself is
        # rendered separately and must not be duplicated as a checkpoint.
        if zone_high <= lower or zone_low >= upper:
            continue
        # Tiny H1 volume imbalances are execution detail, not structural
        # Trouble Areas. Senior imbalances remain visible regardless of width.
        if (
            kind == "volume_imbalance"
            and object_timeframe(item) == "H1"
            and abs(zone_high - zone_low) / price * 100 < 0.03
        ):
            continue
        # When an H1 inefficiency is nested in a W1/D1/H4 area, publish the
        # senior boundary. H1 remains available as internal confirmation.
        if kind != "key_level" and object_timeframe(item) == "H1":
            nested_in_senior = False
            for senior in route_objects:
                if senior is item or str(senior.get("kind") or "") == "key_level":
                    continue
                senior_tf = object_timeframe(senior)
                if timeframe_rank.get(senior_tf, 0) <= timeframe_rank["H1"]:
                    continue
                senior_low = number(senior.get("low"))
                senior_high = number(senior.get("high"))
                if senior_low is None or senior_high is None:
                    continue
                senior_low, senior_high = sorted((senior_low, senior_high))
                if zone_low <= senior_high and zone_high >= senior_low:
                    nested_in_senior = True
                    break
            if nested_in_senior:
                continue
        key = (kind, round(zone_low, 5), round(zone_high, 5))
        grouped.setdefault(key, []).append(item)

    checkpoints: list[dict[str, Any]] = []
    unresolved_state = "accepted_below" if direction == "bullish" else "accepted_above"
    passed_state = "accepted_above" if direction == "bullish" else "accepted_below"
    for (kind, zone_low, zone_high), items in grouped.items():
        names = sorted({str(item.get("name") or "LEVEL") for item in items})
        states = [
            str(layer_state(item, "observation_h1") or item.get("state") or "unknown")
            for item in items
        ]
        if unresolved_state in states:
            status = "decision"
        elif passed_state in states:
            status = "passed"
        else:
            status = "checkpoint"
        edge = zone_low if direction == "bullish" else zone_high
        checkpoints.append({
            "name": " / ".join(names),
            "kind": kind,
            "timeframe": next((object_timeframe(item) for item in items if object_timeframe(item)), None),
            "low": zone_low,
            "high": zone_high,
            "price": edge,
            "state": "/".join(sorted(set(states))),
            "status": status,
            "requires_acceptance": status == "decision",
            "distance_percent": round(abs(edge / price - 1) * 100, 4),
            "source": str(items[0].get("source") or kind),
        })
    checkpoints.sort(key=lambda item: item["distance_percent"])
    first_unresolved = next((item for item in checkpoints if item.get("status") != "passed"), None)
    for item in checkpoints:
        item["route_role"] = "FTA" if item is first_unresolved else "TA"
    return checkpoints


def decision_zone_state(zone: dict[str, Any] | None, direction: str, price: float) -> str:
    """Describe the nearest HTF obstacle without turning it into score evidence."""
    if not zone:
        return "none"
    low = number(zone.get("low"))
    high = number(zone.get("high"))
    if low is None or high is None:
        return "unknown"
    low, high = sorted((low, high))
    h1 = str(zone.get("h1_state") or "")
    rejected_states = {"swept_rejected", "reaction_building", "reclaimed_below", "reclaimed_above"}

    if low <= price <= high:
        return "testing"
    if direction == "bullish":
        if price > high and h1 == "accepted_above":
            return "accepted"
        if price < low and h1 in rejected_states:
            return "rejected"
        return "approaching" if price < low else "passed_unconfirmed"

    if price < low and h1 == "accepted_below":
        return "accepted"
    if price > high and h1 in rejected_states:
        return "rejected"
    return "approaching" if price > high else "passed_unconfirmed"


def resolve_delivery_direction(order_flow_context: dict[str, Any], global_bias: str) -> str:
    """Resolve structural current delivery without letting a fast pulse rename it."""
    structure = str(order_flow_context.get("structure_status") or "").upper()
    if "BEARISH" in structure:
        return "bearish"
    if "BULLISH" in structure:
        return "bullish"
    phases = order_flow_context.get("phases")
    phases = phases if isinstance(phases, dict) else {}
    h1 = phases.get("H1")
    h1 = h1 if isinstance(h1, dict) else {}
    h1_delivery = str(h1.get("delivery") or "").lower()
    if h1_delivery in {"bullish", "bearish"}:
        return h1_delivery
    return global_bias if global_bias in {"bullish", "bearish"} else "mixed"


def lower_tf_observations(
    objects: list[dict[str, Any]],
    direction: str,
    price: float,
    target_price: float,
) -> list[dict[str, Any]]:
    lower, upper = sorted((price, target_price))
    observed_role = "support" if direction == "bearish" else "resistance"
    observations: list[dict[str, Any]] = []
    for item in objects:
        if item.get("kind") != "fvg" or item.get("directional_role") != observed_role:
            continue
        tf = object_timeframe(item)
        if tf != "H1":
            continue
        zone_low = number(item.get("low"))
        zone_high = number(item.get("high"))
        if zone_low is None or zone_high is None:
            continue
        if zone_high < lower or zone_low > upper:
            continue
        observations.append({
            "name": item.get("name"),
            "timeframe": tf,
            "low": zone_low,
            "high": zone_high,
            "h1_state": layer_state(item, "observation_h1") or str(item.get("state", "")),
            "m15_state": layer_state(item, "observation_m15"),
            "distance_percent": round(min(abs(zone_low / price - 1), abs(zone_high / price - 1)) * 100, 4),
            "width_percent": round(abs(zone_high - zone_low) / price * 100, 4),
        })
    observations.sort(key=lambda x: x["distance_percent"])
    return observations


def find_pullback_zone(
    objects: list[dict[str, Any]],
    direction: str,
    price: float,
) -> dict[str, Any] | None:
    """Find the nearest H1 FVG behind price that may support continuation."""
    wanted_role = "support" if direction == "bullish" else "resistance"
    broken_state = "accepted_below" if direction == "bullish" else "accepted_above"
    candidates: list[tuple[float, dict[str, Any]]] = []
    for item in objects:
        if item.get("kind") != "fvg" or item.get("directional_role") != wanted_role:
            continue
        if object_timeframe(item) != "H1":
            continue
        low = number(item.get("low"))
        high = number(item.get("high"))
        if low is None or high is None:
            continue
        low, high = sorted((low, high))
        behind_price = high < price if direction == "bullish" else low > price
        if not behind_price:
            continue
        h1 = layer_state(item, "observation_h1") or str(item.get("state", ""))
        if h1 == broken_state:
            continue
        edge = high if direction == "bullish" else low
        candidates.append((abs(edge / price - 1) * 100, {
            "name": item.get("name"),
            "low": low,
            "high": high,
            "h1_state": h1,
            "distance_percent": round(abs(edge / price - 1) * 100, 4),
        }))
    candidates.sort(key=lambda pair: pair[0])
    return candidates[0][1] if candidates else None


def find_current_reaction_zone(
    objects: list[dict[str, Any]],
    direction: str,
    price: float,
) -> dict[str, Any] | None:
    """Return the H1 continuation zone interacting with price right now."""
    wanted_role = "support" if direction == "bullish" else "resistance"
    broken_state = "accepted_below" if direction == "bullish" else "accepted_above"
    candidates: list[tuple[int, float, dict[str, Any]]] = []
    for item in objects:
        if item.get("kind") != "fvg" or item.get("directional_role") != wanted_role:
            continue
        if object_timeframe(item) != "H1":
            continue
        low = number(item.get("low"))
        high = number(item.get("high"))
        if low is None or high is None:
            continue
        low, high = sorted((low, high))
        h1 = layer_state(item, "observation_h1") or str(item.get("state", ""))
        if h1 == broken_state:
            continue
        inside = low <= price <= high
        edge_distance = 0.0 if inside else min(abs(low / price - 1), abs(high / price - 1)) * 100
        # A current reaction is either being traded inside or is immediately
        # adjacent to price. Deeper zones remain the pullback plan instead.
        if not inside and edge_distance > 0.15:
            continue
        candidates.append((0 if inside else 1, edge_distance, {
            "name": item.get("name"),
            "low": low,
            "high": high,
            "h1_state": h1,
            "interaction": "inside" if inside else "nearby",
            "distance_percent": round(edge_distance, 4),
        }))
    candidates.sort(key=lambda value: (value[0], value[1]))
    return candidates[0][2] if candidates else None


def hypothesis_conditions(
    direction: str,
    target: dict[str, Any] | None,
    decision: dict[str, Any] | None,
    decision_state: str,
    pullback: dict[str, Any] | None,
    checkpoint: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Build an IF/THEN hypothesis tree; these texts never affect scoring."""
    target_name = str((target or {}).get("name") or "next objective")
    target_price = (target or {}).get("price", "")
    side = "above" if direction == "bullish" else "below"
    opposite = "bearish" if direction == "bullish" else "bullish"

    if decision:
        decision_name = str(decision.get("name") or "decision zone")
        decision_low = float(decision.get("low", 0))
        decision_high = float(decision.get("high", 0))
        target_value = number((target or {}).get("price"))
        target_inside_decision = (
            target_value is not None and decision_low <= target_value <= decision_high
        )
        # If Point B is inside a wide zone, acceptance through the far edge
        # would occur only after the target. In that case use the entry edge.
        if target_inside_decision:
            boundary = decision_low if direction == "bullish" else decision_high
        else:
            boundary = decision_high if direction == "bullish" else decision_low
        expectation = (
            f"Observe {decision_name} {decision_low:.5f}-{decision_high:.5f}; "
            f"current state={decision_state}"
        )
        confirmation = (
            f"IF H1 accepts {side} {boundary:.5f}, THEN continuation toward "
            f"{target_name} {target_price} remains active"
        )
    elif checkpoint:
        checkpoint_name = str(checkpoint.get("name") or "key level")
        checkpoint_price = float(checkpoint.get("price", 0))
        expectation = (
            f"Observe {checkpoint_name} {checkpoint_price:.5f} before {target_name} {target_price}; "
            f"current state={checkpoint.get('state', 'unknown')}"
        )
        confirmation = (
            f"IF H1 accepts {side} {checkpoint_price:.5f}, THEN continuation toward "
            f"{target_name} {target_price} remains active"
        )
    else:
        expectation = f"Path is structurally clear toward {target_name} {target_price}"
        confirmation = f"IF H1 delivery remains {direction}, THEN continue monitoring {target_name} {target_price}"

    if pullback:
        pullback_name = str(pullback.get("name") or "H1 FVG")
        pullback_low = float(pullback.get("low", 0))
        pullback_high = float(pullback.get("high", 0))
        invalidation_boundary = pullback_low if direction == "bullish" else pullback_high
        invalidation_side = "below" if direction == "bullish" else "above"
        pullback_case = (
            f"IF price pulls back into {pullback_name} {pullback_low:.5f}-{pullback_high:.5f}, "
            f"THEN observe for {direction} continuation reaction"
        )
        invalidation = (
            f"IF H1 accepts {invalidation_side} {invalidation_boundary:.5f}, "
            f"THEN downgrade the {direction} branch"
        )
        alternative = (
            f"The same H1 acceptance strengthens the {opposite} alternative; "
            "it does not confirm an entry by itself"
        )
    else:
        pullback_case = "No valid H1 continuation pullback zone is currently available"
        invalidation = f"A confirmed H1 transition against {direction} downgrades this branch"
        alternative = f"That transition promotes the {opposite} branch to active observation"

    return {
        "expectation": expectation,
        "confirmation": confirmation,
        "pullback_case": pullback_case,
        "invalidation": invalidation,
        "alternative_trigger": alternative,
    }


def build_direction(
    direction: str,
    global_bias: str,
    bias_evidence: list[Evidence],
    objects: list[dict[str, Any]],
    liquidity_data: dict[str, Any],
    sessions: list[str],
    price: float,
) -> dict[str, Any]:
    evidence: list[Evidence] = []
    if direction == global_bias:
        evidence.extend(bias_evidence)
        evidence.append(Evidence("GLOBAL_ALIGNMENT", f"Direction aligns with {global_bias} global delivery", 15, direction, "ranges"))

    point_a, active_continuation, pending_h4_reaction, reaction_evidence = meaningful_reaction(
        objects,
        direction,
        price,
    )
    evidence.extend(reaction_evidence)
    opposing, transition_evidence = opposing_fvg_transition(objects, direction, price)
    evidence.extend(transition_evidence)
    counter_transition, counter_evidence = counter_h1_transition(objects, direction, price)
    evidence.extend(counter_evidence)

    key_targets = objective_candidates(objects, direction, price)
    liq_targets = liquidity_objectives(liquidity_data, direction, price)
    target = choose_target(key_targets, liq_targets)
    if target:
        evidence.append(Evidence("OPEN_DRAW", f"Open draw: {target['name']} at {target['price']}", 15, direction, target["source"]))

    ignored = {str(opposing.get("object_id"))} if opposing else set()
    blockers = find_blockers(objects, direction, price, float(target["price"]), ignored) if target else []
    route_checkpoints = find_route_checkpoints(objects, direction, price, float(target["price"])) if target else []
    unresolved_checkpoints = [item for item in route_checkpoints if item.get("requires_acceptance")]
    next_route_checkpoint = dict(route_checkpoints[0]) if route_checkpoints else None
    next_decision_zone = dict(blockers[0]) if blockers else None
    next_decision_state = decision_zone_state(next_decision_zone, direction, price)
    current_reaction_zone = find_current_reaction_zone(objects, direction, price)
    pullback_zone = find_pullback_zone(objects, direction, price)
    conditions = hypothesis_conditions(
        direction,
        target,
        next_decision_zone,
        next_decision_state,
        pullback_zone,
        unresolved_checkpoints[0] if unresolved_checkpoints else next_route_checkpoint,
    )
    lower_tf_pa = lower_tf_observations(objects, direction, price, float(target["price"])) if target else []
    if target and not blockers and not unresolved_checkpoints:
        evidence.append(Evidence("PATH_CLEAR", "No unresolved FVG or key-level decision before target", 15, direction, "level_state"))

    if sessions:
        evidence.append(Evidence("SESSION_CONTEXT", f"Active session: {', '.join(sessions)}", 3, direction, "session_context"))

    raw_score = sum(item.weight for item in evidence)
    score = max(0, min(100, raw_score))
    h1_transition_confirmed = any(item.code == "H1_OPPOSING_FVG_LOST" for item in evidence)
    has_reaction = point_a is not None
    has_target = target is not None
    path_clear = has_target and len(blockers) <= MAX_BLOCKERS_TO_CONFIRM and not unresolved_checkpoints

    if score >= CONFIRMED_SCORE and has_reaction and has_target and path_clear and h1_transition_confirmed:
        status = "confirmed"
    elif score >= BUILDING_SCORE and has_reaction and has_target:
        status = "building"
    elif score >= OBSERVE_SCORE and has_reaction and has_target:
        status = "observe"
    else:
        status = "silent"

    return {
        "direction": direction,
        "status": status,
        "score": score,
        "global_alignment": direction == global_bias,
        "point_a": point_a,
        "active_continuation": active_continuation,
        "pending_h4_reaction": pending_h4_reaction,
        "opposing_zone": opposing,
        "counter_transition_zone": counter_transition,
        "point_b": target,
        "path_clear": path_clear,
        "next_decision_zone": next_decision_zone,
        "next_decision_state": next_decision_state,
        "current_reaction_zone": current_reaction_zone,
        "pullback_zone": pullback_zone,
        "conditions": conditions,
        "blockers": blockers,
        "route_checkpoints": route_checkpoints,
        "next_route_checkpoint": next_route_checkpoint,
        "lower_tf_pa": lower_tf_pa,
        "evidence": [asdict(item) for item in evidence],
        "notification_level": status,
        "telegram_eligible": status in {"observe", "building", "confirmed"},
    }


def evidence_codes(branch: dict[str, Any] | None) -> set[str]:
    if not isinstance(branch, dict):
        return set()
    return {
        str(item.get("code"))
        for item in branch.get("evidence") or []
        if isinstance(item, dict) and item.get("code")
    }


def branch_map(branches: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(branch.get("direction")): branch
        for branch in branches
        if branch.get("direction") in {"bullish", "bearish"}
    }


def load_previous_primary_branch() -> dict[str, Any] | None:
    previous = load_json(OUTPUT_FILE, required=False)
    primary = previous.get("primary_scenario")
    return primary if isinstance(primary, dict) else None


def load_previous_primary_direction() -> str | None:
    primary = load_previous_primary_branch()
    if primary is None:
        return None
    direction = str(primary.get("direction") or "")
    return direction if direction in {"bullish", "bearish"} else None


def freeze_structural_invalidation(
    primary: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    transition: dict[str, Any],
) -> None:
    """Keep the branch invalidation fixed until a closed H1 transition.

    Fresh intrabar FVG/VI objects may provide execution levels, but they must
    not trail the structural invalidation behind price.
    """
    if not isinstance(primary, dict) or not isinstance(previous, dict):
        return
    if primary.get("direction") != previous.get("direction"):
        return
    if transition.get("switch_confirmed"):
        return
    old_conditions = previous.get("conditions") if isinstance(previous.get("conditions"), dict) else {}
    new_conditions = primary.get("conditions") if isinstance(primary.get("conditions"), dict) else {}
    old_invalidation = str(old_conditions.get("invalidation") or "")
    new_invalidation = str(new_conditions.get("invalidation") or "")
    # Lock only an already-established numeric H1 acceptance boundary. A
    # generic transition sentence may still be upgraded once to a real level.
    if not old_invalidation.startswith("IF H1 accepts") or not re.search(r"\d+(?:\.\d+)?", old_invalidation):
        return
    if old_invalidation == new_invalidation:
        return
    new_conditions["dynamic_execution_invalidation"] = new_invalidation
    new_conditions["invalidation"] = old_invalidation
    primary["conditions"] = new_conditions
    primary["structural_invalidation_locked"] = True


def freeze_structural_identity(
    primary: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    transition: dict[str, Any],
    price: float,
) -> None:
    """Keep structural A/B immutable until B is reached or the branch switches."""
    if not isinstance(primary, dict) or not isinstance(previous, dict):
        return
    if primary.get("direction") != previous.get("direction") or transition.get("switch_confirmed"):
        return
    direction = str(primary.get("direction") or "")
    old_target = previous.get("point_b") if isinstance(previous.get("point_b"), dict) else None
    old_target_price = number((old_target or {}).get("price"))
    target_reached = bool(
        old_target_price is not None
        and (
            (direction == "bearish" and price <= old_target_price)
            or (direction == "bullish" and price >= old_target_price)
        )
    )
    if target_reached:
        return
    if isinstance(previous.get("point_a"), dict):
        primary["point_a"] = dict(previous["point_a"])
        primary["structural_point_a_locked"] = True
    if old_target:
        primary["point_b"] = dict(old_target)
        primary["structural_point_b_locked"] = True


def refresh_locked_branch_geometry(
    primary: dict[str, Any] | None,
    objects: list[dict[str, Any]],
    session_data: dict[str, Any],
    price: float,
) -> None:
    """Rebuild route/conditions around a frozen structural Point B."""
    if not isinstance(primary, dict) or not primary.get("structural_point_b_locked"):
        return
    direction = str(primary.get("direction") or "")
    target = primary.get("point_b") if isinstance(primary.get("point_b"), dict) else None
    target_price = number((target or {}).get("price"))
    if direction not in {"bullish", "bearish"} or target_price is None:
        return
    blockers = find_blockers(objects, direction, price, target_price, set())
    checkpoints = find_route_checkpoints(objects, direction, price, target_price)
    checkpoints.extend(session_liquidity_checkpoints(session_data, direction, price, target_price))
    checkpoints.sort(key=lambda item: number(item.get("distance_percent")) or 999.0)
    first = next((item for item in checkpoints if item.get("status") != "passed"), None)
    for item in checkpoints:
        item["route_role"] = "FTA" if item is first else "TA"
    decision = dict(blockers[0]) if blockers else None
    decision_state = decision_zone_state(decision, direction, price)
    pullback = find_pullback_zone(objects, direction, price)
    unresolved = next((item for item in checkpoints if item.get("requires_acceptance")), None)
    primary.update({
        "blockers": blockers,
        "route_checkpoints": checkpoints,
        "next_route_checkpoint": dict(checkpoints[0]) if checkpoints else None,
        "next_decision_zone": decision,
        "next_decision_state": decision_state,
        "current_reaction_zone": find_current_reaction_zone(objects, direction, price),
        "pullback_zone": pullback,
        "conditions": hypothesis_conditions(
            direction, target, decision, decision_state, pullback, unresolved or first,
        ),
    })


def switch_is_confirmed(
    previous_branch: dict[str, Any] | None,
    candidate_branch: dict[str, Any] | None,
) -> tuple[bool, list[str]]:
    """Require closed-H1 evidence before replacing a live hypothesis.

    Scores, LTF pulse, USD breadth and a mere zone test can describe context,
    but they cannot switch the primary branch.
    """
    if not isinstance(candidate_branch, dict):
        return False, ["NO_CANDIDATE_BRANCH"]
    candidate_codes = evidence_codes(candidate_branch)
    previous_codes = evidence_codes(previous_branch)
    reasons: list[str] = []
    if "H1_OPPOSING_FVG_LOST" not in candidate_codes:
        reasons.append("NO_CLOSED_H1_ACCEPTANCE_FOR_CANDIDATE")
    if isinstance(previous_branch, dict):
        previous_live = str(previous_branch.get("status") or "silent") != "silent"
        previous_invalidated = "H1_COUNTER_TRANSITION" in previous_codes
        if previous_live and not previous_invalidated:
            reasons.append("PREVIOUS_BRANCH_NOT_INVALIDATED")
    if candidate_branch.get("point_a") is None:
        reasons.append("NO_CONFIRMED_REACTION_SOURCE")
    if candidate_branch.get("point_b") is None:
        reasons.append("NO_VALID_POINT_B")
    return not reasons, reasons


def select_primary_stateful(
    branches: list[dict[str, Any]],
    delivery_direction: str,
    previous_direction: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Keep one market hypothesis until structural evidence invalidates it."""
    by_direction = branch_map(branches)
    publishable = [branch for branch in branches if branch.get("status") != "silent"]
    ranked = sorted(publishable, key=lambda item: int(item.get("score") or 0), reverse=True)
    candidate = ranked[0] if ranked else None

    if previous_direction not in by_direction:
        initial = by_direction.get(delivery_direction)
        if not initial or initial.get("status") == "silent":
            initial = candidate
        return initial, {
            "state": "initialized" if initial else "silent",
            "previous_direction": previous_direction,
            "candidate_direction": (candidate or {}).get("direction"),
            "switch_confirmed": False,
            "reason_codes": ["INITIAL_STRUCTURAL_SELECTION"] if initial else ["NO_PUBLISHABLE_BRANCH"],
        }

    previous_branch = by_direction.get(previous_direction)
    if not previous_branch:
        return candidate, {
            "state": "recovered",
            "previous_direction": previous_direction,
            "candidate_direction": (candidate or {}).get("direction"),
            "switch_confirmed": False,
            "reason_codes": ["PREVIOUS_BRANCH_MISSING"],
        }

    candidate_direction = str((candidate or {}).get("direction") or "")
    if not candidate or candidate_direction == previous_direction:
        return previous_branch, {
            "state": "active",
            "previous_direction": previous_direction,
            "candidate_direction": candidate_direction or None,
            "switch_confirmed": False,
            "reason_codes": ["PRIMARY_BRANCH_HELD"],
        }

    confirmed, reasons = switch_is_confirmed(previous_branch, candidate)
    if confirmed:
        return candidate, {
            "state": "confirmed_transition",
            "previous_direction": previous_direction,
            "candidate_direction": candidate_direction,
            "switch_confirmed": True,
            "reason_codes": ["CLOSED_H1_TRANSITION_CONFIRMED"],
        }

    return previous_branch, {
        "state": "alternative_testing",
        "previous_direction": previous_direction,
        "candidate_direction": candidate_direction,
        "switch_confirmed": False,
        "reason_codes": reasons or ["CANDIDATE_NOT_CONFIRMED"],
    }


def nearest_market_location(
    objects: list[dict[str, Any]],
    price: float,
) -> dict[str, Any] | None:
    """Find where price is now, independent of the projected route."""
    tf_rank = {"W1": 4, "D1": 3, "H4": 2, "H1": 1}
    candidates: list[tuple[int, float, int, dict[str, Any]]] = []
    for item in objects:
        if item.get("kind") not in {"fvg", "volume_imbalance", "gap", "key_level"}:
            continue
        tf = object_timeframe(item)
        if tf not in tf_rank:
            continue
        low = number(item.get("low"))
        high = number(item.get("high"))
        if low is None:
            continue
        if high is None:
            high = low
        low, high = sorted((low, high))
        inside = low <= price <= high
        distance = 0.0 if inside else min(abs(low / price - 1), abs(high / price - 1)) * 100
        if not inside and distance > 0.20:
            continue
        h1 = layer_state(item, "observation_h1") or str(item.get("state") or "unknown")
        candidates.append((0 if inside else 1, distance, -tf_rank[tf], {
            "name": item.get("name"),
            "kind": item.get("kind"),
            "timeframe": tf,
            "low": low,
            "high": high,
            "state": h1,
            "interaction": "inside" if inside else "nearby",
            "distance_percent": round(distance, 4),
        }))
    candidates.sort(key=lambda value: (value[0], value[1], value[2]))
    return candidates[0][3] if candidates else None


def active_leg_context(
    order_flow_context: dict[str, Any],
    primary: dict[str, Any] | None,
    current_location: dict[str, Any] | None,
) -> dict[str, Any]:
    phases = order_flow_context.get("phases")
    phases = phases if isinstance(phases, dict) else {}
    h1 = phases.get("H1")
    h1 = h1 if isinstance(h1, dict) else {}
    active_leg = h1.get("active_leg")
    active_leg = active_leg if isinstance(active_leg, dict) else {}
    anchor = active_leg.get("anchor")
    anchor = anchor if isinstance(anchor, dict) else {}
    target = (primary or {}).get("point_b")
    target = target if isinstance(target, dict) else {}
    return {
        "direction": active_leg.get("direction") or h1.get("delivery") or "unknown",
        "state": "confirmed" if active_leg.get("confirmed") else "unconfirmed",
        "phase": h1.get("phase") or "unknown",
        "point_a": {
            "time": anchor.get("time"),
            "type": anchor.get("type"),
            "price": anchor.get("price"),
        } if anchor else None,
        "point_b": target or None,
        "current_location": current_location,
        "latest_closed_h1": h1.get("latest_closed_time") or None,
    }


def volume_hypothesis_context(
    volume_payload: dict[str, Any],
    direction: str | None,
    primary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Translate effort/result observations into hypothesis context, never score."""
    frames = volume_payload.get("timeframes")
    frames = frames if isinstance(frames, dict) else {}
    stale_timeframes = [
        tf for tf, item in frames.items()
        if isinstance(item, dict) and item.get("reason") == "STALE_CLOSED_BAR"
    ]
    observations = []
    relation = "unconfirmed"
    explanation = "No closed H1/H4 volume observation is available."
    point_a = (primary or {}).get("point_a") if isinstance((primary or {}).get("point_a"), dict) else {}
    source_tf = str(point_a.get("timeframe") or "H1").upper()
    for tf in ("W1", "D1", "H4", "H1", "M15", "M5"):
        item = frames.get(tf)
        if not isinstance(item, dict) or not item.get("available"):
            continue
        observation_direction = str(item.get("direction") or "unknown")
        observations.append({
            "observation_id": item.get("observation_id") or f"{tf}:latest_closed",
            "timeframe": tf,
            "closed_bar_time": item.get("closed_bar_time"),
            "direction": observation_direction,
            "state": item.get("state"),
            "volume_ratio": item.get("tick_volume_ratio"),
            "spread_ratio": item.get("spread_ratio"),
            "effort_result": item.get("effort_result"),
            "sequence": item.get("sequence"),
            "participation_change": item.get("participation_change"),
            "result_change": item.get("result_change"),
            "body_efficiency": item.get("body_efficiency"),
            "leg_attribution": (
                "primary_structural_leg" if observation_direction == direction
                else "opposite_direction_evidence" if observation_direction in {"bullish", "bearish"}
                else "neutral"
            ),
            "confirmation_scope": (
                "source_timeframe" if tf == source_tf
                else "execution_confirmation" if tf == "H1" and observation_direction == direction
                else "candidate_evidence" if tf == "H1" and observation_direction in {"bullish", "bearish"}
                else "timing_only" if tf in {"M15", "M5"}
                else "context_only"
            ),
        })

    h1 = frames.get("H1") if isinstance(frames.get("H1"), dict) else {}
    h4 = frames.get("H4") if isinstance(frames.get("H4"), dict) else {}
    if direction in {"bullish", "bearish"}:
        opposite = "bearish" if direction == "bullish" else "bullish"
        if (
            h1.get("state") == "low_volume_pullback"
            and h1.get("direction") == opposite
        ):
            relation = "supports"
            explanation = f"H1 {opposite} pullback has low participation; {direction} hypothesis retains control."
        elif (
            h1.get("direction") == direction
            and h1.get("state") in {"expansion", "high_volume_acceptance_candidate"}
            and h1.get("effort_result") == "aligned"
        ):
            relation = "supports_execution" if source_tf != "H1" else "supports"
            explanation = (
                f"H1 effort/result aligns with the {direction} execution leg; "
                f"it does not confirm the {source_tf} structural source by itself."
                if source_tf != "H1"
                else f"H1 effort and result align with the H1 {direction} hypothesis."
            )
        elif h1.get("state") in {"possible_absorption", "possible_exhaustion"}:
            relation = "warning"
            explanation = "H1 effort is not producing proportional result; watch for absorption/exhaustion."
        elif h4.get("direction") == direction and h4.get("effort_result") == "aligned":
            relation = "supports" if source_tf in {"H4", "H1"} else "supports_context"
            explanation = f"Closed H4 effort/result aligns with the {direction} leg (source TF: {source_tf})."
        elif observations:
            explanation = "Closed volume is present but does not yet confirm either hypothesis."
    return {
        "available": bool(observations),
        "relation_to_primary": relation,
        "explanation": explanation,
        "observations": observations,
        "source_timeframe": source_tf,
        "attribution_rule": "Only source-TF volume confirms structural effort; H1 confirms execution; M15/M5 time reactions.",
        "source": volume_payload.get("source") or "broker tick volume",
        "data_quality": "partial_stale" if stale_timeframes and observations else "stale" if stale_timeframes else "current",
        "stale_timeframes": stale_timeframes,
        "scoring_applied": False,
    }


def volume_leg_attribution(
    volume_context: dict[str, Any],
    market_state: dict[str, Any],
) -> dict[str, Any]:
    """Attribute closed-volume evidence to structural and execution legs.

    Volume is supporting evidence only.  It may strengthen or warn about an
    already observed leg, but it cannot create a leg, switch the primary, or
    become an entry trigger by itself.
    """
    result = dict(volume_context or {})
    observations = [
        dict(item) for item in result.get("observations") or []
        if isinstance(item, dict)
    ]
    primary_leg = market_state.get("primary_structural_leg")
    primary_leg = primary_leg if isinstance(primary_leg, dict) else {}
    counter_leg = market_state.get("active_counter_leg")
    counter_leg = counter_leg if isinstance(counter_leg, dict) else {}
    if not counter_leg:
        counter_leg = market_state.get("counter_candidate")
        counter_leg = counter_leg if isinstance(counter_leg, dict) else {}

    primary_direction = str(primary_leg.get("direction") or (market_state.get("primary") or {}).get("direction") or "unknown")
    counter_direction = str(counter_leg.get("direction") or "unknown")
    source = primary_leg.get("point_a") if isinstance(primary_leg.get("point_a"), dict) else {}
    source_tf = str(source.get("timeframe") or result.get("source_timeframe") or "H1").upper()

    def frame(tf: str) -> dict[str, Any]:
        return next((item for item in observations if item.get("timeframe") == tf), {})

    def aligned_expansion(item: dict[str, Any], direction: str) -> bool:
        return (
            item.get("direction") == direction
            and item.get("state") in {"expansion", "high_volume_acceptance_candidate"}
            and item.get("effort_result") == "aligned"
        )

    def stalled_effort(item: dict[str, Any], direction: str) -> bool:
        return (
            item.get("direction") == direction
            and (
                item.get("state") in {"possible_absorption", "possible_exhaustion"}
                or item.get("sequence") in {"absorption_building", "fading_effort"}
            )
        )

    source_observation = frame(source_tf) if source_tf in {"H1", "H4", "D1", "W1"} else {}
    h1 = frame("H1")
    structural_relation = "unconfirmed"
    structural_reason = f"Нет закрытого объёмного подтверждения на source TF {source_tf}."
    if source_observation:
        if aligned_expansion(source_observation, primary_direction):
            structural_relation = "supports"
            structural_reason = f"{source_tf}: усилие и результат согласованы с {primary_direction} structural leg."
        elif stalled_effort(source_observation, primary_direction):
            structural_relation = "warning"
            structural_reason = f"{source_tf}: усилие {primary_direction} не даёт пропорционального результата; возможны absorption/exhaustion."
        elif (
            source_observation.get("direction") not in {primary_direction, "neutral", "unknown"}
            and source_observation.get("state") == "low_volume_pullback"
        ):
            structural_relation = "supports"
            structural_reason = f"{source_tf}: противоположное движение идёт на низком участии; {primary_direction} structural leg сохраняет контроль."
        elif (
            source_observation.get("direction") not in {primary_direction, "neutral", "unknown"}
            and source_observation.get("state") in {"expansion", "high_volume_acceptance_candidate"}
            and source_observation.get("effort_result") == "aligned"
        ):
            structural_relation = "conflicts"
            structural_reason = f"{source_tf}: закрытое объёмное наблюдение направлено против {primary_direction} structural leg."
    elif source_tf in {"D1", "W1"} and frame("H4"):
        structural_relation = "context_only"
        structural_reason = f"{source_tf} объём недоступен; H4 объём остаётся только контекстом и не подтверждает источник {source_tf}."
    elif source_tf not in {"H1", "H4", "D1", "W1"} and frame("H4"):
        structural_relation = "context_only"
        structural_reason = f"H4 объём доступен только как контекст и не подтверждает источник {source_tf}."

    execution_relation = "not_applicable"
    execution_reason = "Активная вложенная реакция отсутствует."
    if counter_direction in {"bullish", "bearish"}:
        execution_relation = "unconfirmed"
        execution_reason = "H1 объём пока не подтверждает вложенную реакцию."
        if aligned_expansion(h1, counter_direction):
            execution_relation = "supports"
            execution_reason = f"H1: усилие и результат согласованы с {counter_direction} вложенной реакцией."
        elif stalled_effort(h1, primary_direction):
            execution_relation = "supports_candidate"
            execution_reason = f"H1: {primary_direction} усилие перестало давать результат; это поддерживает наблюдение {counter_direction} реакции, но не подтверждает вход."
        elif aligned_expansion(h1, primary_direction):
            execution_relation = "conflicts"
            execution_reason = f"H1: объём подтверждает {primary_direction} продолжение и противоречит {counter_direction} реакции."
        elif h1.get("direction") == counter_direction and h1.get("state") == "low_volume_pullback":
            execution_relation = "weak"
            execution_reason = f"H1: движение {counter_direction} идёт на низком участии; реакция остаётся слабой."

    for item in observations:
        tf = str(item.get("timeframe") or "")
        if tf == source_tf and tf in {"H1", "H4", "D1", "W1"}:
            item["leg_attribution"] = "primary_structural_leg"
            item["confirmation_scope"] = "source_timeframe"
        elif tf == "H1" and counter_direction in {"bullish", "bearish"}:
            item["leg_attribution"] = "active_counter_leg"
            item["confirmation_scope"] = "execution_confirmation"
        elif tf in {"M15", "M5"}:
            item["leg_attribution"] = "timing_context"
            item["confirmation_scope"] = "timing_only"
        else:
            item["leg_attribution"] = "higher_timeframe_context"
            item["confirmation_scope"] = "context_only"

    result.update({
        "observations": observations,
        "relation_to_primary": structural_relation,
        "relation_to_counter": execution_relation,
        "explanation": structural_reason,
        "leg_evaluations": {
            "primary_structural_leg": {
                "direction": primary_direction,
                "source_timeframe": source_tf,
                "relation": structural_relation,
                "reason": structural_reason,
            },
            "active_counter_leg": {
                "direction": counter_direction,
                "source_timeframe": "H1",
                "relation": execution_relation,
                "reason": execution_reason,
            },
        },
        "entry_permission": False,
        "direction_switch_permission": False,
        "telegram_eligible": False,
        "scoring_applied": False,
    })
    return result


def news_hypothesis_context(
    driver: dict[str, Any],
    direction: str | None,
    macro_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    intermarket = (macro_context or {}).get("intermarket")
    intermarket = intermarket if isinstance(intermarket, dict) else {}
    causal_drivers = [item for item in intermarket.get("drivers") or [] if isinstance(item, dict)]
    supports = [item for item in causal_drivers if item.get("effect_xauusd") == direction]
    conflicts = [
        item for item in causal_drivers
        if item.get("effect_xauusd") in {"bullish", "bearish"} and item.get("effect_xauusd") != direction
    ]
    if not driver.get("available"):
        if causal_drivers:
            role = "correlated_driver" if supports and not conflicts else "conflicting_driver" if conflicts and not supports else "mixed_driver"
            return {
                "available": True,
                "role": role,
                "drivers": causal_drivers,
                "explanation": (
                    f"Intermarket context: {len(supports)} supports, {len(conflicts)} conflicts; "
                    "causality remains correlated/inferred until PA confirms."
                ),
                "scoring_applied": False,
            }
        return {
            "available": False,
            "role": "none",
            "explanation": "No verified relevant driver is attached.",
            "scoring_applied": False,
        }
    status = str(driver.get("status") or "scheduled")
    effect = str(driver.get("effect") or "risk_only")
    reaction = str(driver.get("reaction_status") or "pending")
    if status != "released" or effect == "risk_only":
        role = "scheduled_risk"
        explanation = "Scheduled news is volatility risk, not directional evidence."
    elif effect == direction and reaction == "confirmed":
        role = "confirmed_driver"
        explanation = f"Released news and observed USD reaction support the {direction} hypothesis."
    elif effect in {"bullish", "bearish"} and effect != direction:
        role = "conflicting_driver"
        explanation = f"Released news conflicts with the {direction} hypothesis; PA confirmation is required."
    else:
        role = "unconfirmed_driver"
        explanation = "The release is known, but market reaction has not confirmed its directional effect."
    return {
        "available": True,
        "role": role,
        "title": driver.get("title"),
        "effect": effect,
        "reaction_status": reaction,
        "explanation": explanation,
        "drivers": causal_drivers,
        "intermarket_supports": len(supports),
        "intermarket_conflicts": len(conflicts),
        "scoring_applied": False,
    }


def concise_console(
    primary: dict[str, Any] | None,
    branches: list[dict[str, Any]],
    price: float,
    bias: str,
    order_flow_context: dict[str, Any],
) -> None:
    print(f"CURRENT_PRICE: {price:.5f}")
    print(f"GLOBAL_BIAS: {bias}")
    if order_flow_context.get("available"):
        print(
            f"MARKET_STRUCTURE: W1={order_flow_context.get('weekly_narrative')} | "
            f"delivery={order_flow_context.get('delivery_status')} | "
            f"structure={order_flow_context.get('structure_status')} | score_impact=0"
        )
        phases = order_flow_context.get("phases") or {}
        print(
            "DELIVERY_PHASES: "
            + " | ".join(
                f"{tf}={phases.get(tf, {}).get('delivery', 'unknown')}/"
                f"{phases.get(tf, {}).get('phase', 'unknown')}"
                for tf in ("D1", "H4", "H1")
            )
        )
    print("\nINTERNAL BRANCHES:")
    for branch in branches:
        target = branch.get("point_b") or {}
        decision = branch.get("next_decision_zone") or {}
        print(
            f"{branch['direction']:<7} | {branch['status']:<9} | score={branch['score']:>3} | "
            f"B={target.get('name', 'none')} {target.get('price', '')} | "
            f"FTA={len(branch['blockers'])}"
        )

    print("\nSCORE BREAKDOWN:")
    for branch in branches:
        print(f"\n{branch['direction'].upper()} | total={branch['score']} | status={branch['status']}")
        origin = branch.get("point_a") or {}
        continuation = branch.get("active_continuation") or {}
        pending = branch.get("pending_h4_reaction") or {}
        target = branch.get("point_b") or {}
        decision = branch.get("next_decision_zone") or {}
        print(f"  ORIGIN_A: {origin.get('name', 'none')} {origin.get('low', '')}-{origin.get('high', '')}")
        print(
            f"  ACTIVE_CONTINUATION: {continuation.get('name', 'none')} "
            f"{continuation.get('low', '')}-{continuation.get('high', '')}"
        )
        print(
            f"  PENDING_H4_REACTION: {pending.get('name', 'none')} "
            f"{pending.get('low', '')}-{pending.get('high', '')}"
        )
        print(f"  PROJECTED_B: {target.get('name', 'none')} {target.get('price', '')}")
        print(
            f"  NEXT_DECISION_ZONE: {decision.get('name', 'none')} "
            f"{decision.get('low', '')}-{decision.get('high', '')} | "
            f"state={branch.get('next_decision_state', 'none')}"
        )
        pullback = branch.get("pullback_zone") or {}
        current_reaction = branch.get("current_reaction_zone") or {}
        print(
            f"  CURRENT_REACTION_ZONE: {current_reaction.get('name', 'none')} "
            f"{current_reaction.get('low', '')}-{current_reaction.get('high', '')} | "
            f"state={current_reaction.get('h1_state', 'none')} | "
            f"interaction={current_reaction.get('interaction', 'none')}"
        )
        print(
            f"  PULLBACK_ZONE: {pullback.get('name', 'none')} "
            f"{pullback.get('low', '')}-{pullback.get('high', '')}"
        )
        conditions = branch.get("conditions") or {}
        print(f"  EXPECTATION: {conditions.get('expectation', 'none')}")
        print(f"  CONFIRMATION: {conditions.get('confirmation', 'none')}")
        print(f"  PULLBACK_CASE: {conditions.get('pullback_case', 'none')}")
        print(f"  INVALIDATION: {conditions.get('invalidation', 'none')}")
        print(f"  ALTERNATIVE_TRIGGER: {conditions.get('alternative_trigger', 'none')}")
        branch_evidence = branch.get("evidence") or []
        if not branch_evidence:
            print("  no positive evidence")
        for item in branch_evidence:
            print(
                f"  {int(item.get('weight', 0)):+3} | "
                f"{item.get('code', 'UNKNOWN'):<25} | {item.get('text', '')}"
            )

        checks = {
            "reaction": branch.get("point_a") is not None,
            "target": branch.get("point_b") is not None,
            "path_clear": bool(branch.get("path_clear")),
            "h1_transition": any(
                item.get("code") == "H1_OPPOSING_FVG_LOST"
                for item in branch_evidence
            ),
        }
        print(
            "  checks | "
            + " | ".join(f"{name}={'yes' if passed else 'no'}" for name, passed in checks.items())
        )

        blockers = branch.get("blockers") or []
        if blockers:
            print(f"  FTA / blockers ({len(blockers)}):")
            for index, blocker in enumerate(blockers, start=1):
                print(
                    f"    {index}. {blocker.get('name', 'FVG')} "
                    f"{float(blocker.get('low', 0)):.5f}-{float(blocker.get('high', 0)):.5f} | "
                    f"H1={blocker.get('h1_state') or 'n/a'} | "
                    f"to_edge={float(blocker.get('distance_percent', 0)):.3f}% | "
                    f"width={float(blocker.get('width_percent', 0)):.3f}%"
                )
        else:
            print("  FTA / blockers: none")

        lower_tf_pa = branch.get("lower_tf_pa") or []
        h1_pa = [item for item in lower_tf_pa if item.get("timeframe") == "H1"]
        if h1_pa:
            print(f"  H1 PA / observation ({len(h1_pa)}):")
            for index, item in enumerate(h1_pa, start=1):
                print(
                    f"    {index}. {item.get('name', 'FVG')} "
                    f"{float(item.get('low', 0)):.5f}-{float(item.get('high', 0)):.5f} | "
                    f"H1={item.get('h1_state') or 'n/a'} | "
                    f"to_edge={float(item.get('distance_percent', 0)):.3f}% | "
                    f"width={float(item.get('width_percent', 0)):.3f}%"
                )
        else:
            print("  H1 PA / observation: none")
    print("\nPUBLISHED CONTEXT:")
    if primary is None:
        print("SILENT — no branch has sufficient confirmed context")
        return
    print(f"{primary['direction'].upper()} {primary['status'].upper()} | score={primary['score']}")
    print(f"POINT_B: {primary['point_b']['name']} {primary['point_b']['price']}")
    decision = primary.get("next_decision_zone") or {}
    print(
        f"NEXT_DECISION_ZONE: {decision.get('name', 'none')} "
        f"{decision.get('low', '')}-{decision.get('high', '')} | "
        f"state={primary.get('next_decision_state', 'none')}"
    )
    pullback = primary.get("pullback_zone") or {}
    current_reaction = primary.get("current_reaction_zone") or {}
    print(
        f"CURRENT_REACTION_ZONE: {current_reaction.get('name', 'none')} "
        f"{current_reaction.get('low', '')}-{current_reaction.get('high', '')} | "
        f"state={current_reaction.get('h1_state', 'none')}"
    )
    print(
        f"PULLBACK_ZONE: {pullback.get('name', 'none')} "
        f"{pullback.get('low', '')}-{pullback.get('high', '')}"
    )
    conditions = primary.get("conditions") or {}
    print(f"EXPECTATION: {conditions.get('expectation', 'none')}")
    print(f"CONFIRMATION: {conditions.get('confirmation', 'none')}")
    print(f"PULLBACK_CASE: {conditions.get('pullback_case', 'none')}")
    print(f"INVALIDATION: {conditions.get('invalidation', 'none')}")
    print(f"ALTERNATIVE_TRIGGER: {conditions.get('alternative_trigger', 'none')}")
    print(f"PATH_CLEAR: {primary['path_clear']}")


def main() -> None:
    state_data = load_json(STATE_FILE)
    range_data = load_json(RANGE_FILE)
    liquidity_data = load_json(LIQUIDITY_FILE, required=False)
    session_data = load_json(SESSION_FILE, required=False)
    order_flow_data = load_json(ORDER_FLOW_FILE, required=False)
    news_driver_data = load_json(NEWS_DRIVER_FILE, required=False)
    usd_context_data = load_json(USD_CONTEXT_FILE, required=False)
    macro_context_data = load_json(MACRO_CONTEXT_FILE, required=False)
    pulse_context_data = load_json(PULSE_CONTEXT_FILE, required=False)
    volume_context_data = load_json(VOLUME_CONTEXT_FILE, required=False)

    objects = state_objects(state_data)
    if not objects:
        raise RuntimeError("No objects found in level-state JSON")
    price = current_price(objects)
    sessions = current_session_names(session_data)
    global_bias, bias_evidence = extract_range_bias(range_data)
    order_flow_context = extract_order_flow_context(order_flow_data)
    driver_context = news_driver_data.get("driver_context")
    if not isinstance(driver_context, dict):
        driver_context = {"available": False, "reason": "NO_DRIVER_DATA", "scoring_applied": False}
    usd_context = usd_context_data.get("context")
    if not isinstance(usd_context, dict):
        usd_context = {"available": False, "reason": "NO_USD_CONTEXT", "scoring_applied": False}
    macro_context = macro_context_data.get("context")
    if not isinstance(macro_context, dict):
        macro_context = {"available": False, "reason": "NO_MACRO_CONTEXT", "scoring_applied": False}

    previous_payload = load_json(OUTPUT_FILE, required=False)
    previous_primary = previous_payload.get("primary_scenario")
    previous_primary = previous_primary if isinstance(previous_primary, dict) else None
    previous_market_state = previous_payload.get("market_state")
    previous_market_state = previous_market_state if isinstance(previous_market_state, dict) else None
    previous_primary_direction = (
        str(previous_primary.get("direction"))
        if isinstance(previous_primary, dict) and previous_primary.get("direction") in {"bullish", "bearish"}
        else None
    )
    branches = [
        build_direction("bearish", global_bias, bias_evidence, objects, liquidity_data, sessions, price),
        build_direction("bullish", global_bias, bias_evidence, objects, liquidity_data, sessions, price),
    ]
    branches.sort(key=lambda item: item["score"], reverse=True)

    pulse_context = pulse_context_data.get("pulse")
    if not isinstance(pulse_context, dict):
        pulse_context = {"available": False, "reason": "NO_PULSE_CONTEXT"}
    volume_context = volume_context_data.get("volume_context")
    if not isinstance(volume_context, dict):
        volume_context = {"available": False, "reason": "NO_VOLUME_CONTEXT"}

    delivery_direction = resolve_delivery_direction(order_flow_context, global_bias)
    delivery_branch = next((item for item in branches if item.get("direction") == delivery_direction), None)
    delivery_target = (delivery_branch or {}).get("point_b")
    delivery_target_price = number(delivery_target.get("price")) if isinstance(delivery_target, dict) else None
    delivery_route = list((delivery_branch or {}).get("route_checkpoints") or [])
    delivery_route.extend(session_liquidity_checkpoints(session_data, delivery_direction, price, delivery_target_price))
    delivery_route.sort(key=lambda item: number(item.get("distance_percent")) or 999.0)
    first_delivery_checkpoint = next((item for item in delivery_route if item.get("status") != "passed"), None)
    for item in delivery_route:
        item["route_role"] = "FTA" if item is first_delivery_checkpoint else "TA"
    current_delivery = {
        "direction": delivery_direction,
        "state": str(pulse_context.get("state") or "structural"),
        "structure": str(order_flow_context.get("structure_status") or "unknown"),
        "route_checkpoints": delivery_route,
        "point_b": delivery_target,
        "pulse": pulse_context,
        "volume": volume_context,
        "scoring_applied": False,
    }

    primary, hypothesis_transition = select_primary_stateful(
        branches,
        delivery_direction,
        previous_primary_direction,
    )
    freeze_structural_identity(primary, previous_primary, hypothesis_transition, price)
    refresh_locked_branch_geometry(primary, objects, session_data, price)
    freeze_structural_invalidation(primary, previous_primary, hypothesis_transition)
    volume_hypothesis = volume_hypothesis_context(
        volume_context_data, (primary or {}).get("direction"), primary
    )
    news_hypothesis = news_hypothesis_context(
        driver_context, (primary or {}).get("direction"), macro_context
    )
    market_state = build_reasoning_state(
        objects=objects,
        session_data=session_data,
        order_flow=order_flow_data,
        branches=branches,
        primary=primary,
        transition=hypothesis_transition,
        volume_context=volume_hypothesis,
        news_context=news_hypothesis,
        price=price,
        previous_state=previous_market_state,
    )
    volume_hypothesis = volume_leg_attribution(volume_hypothesis, market_state)
    primary_market = market_state.get("primary") if isinstance(market_state.get("primary"), dict) else {}
    causality = primary_market.get("causality") if isinstance(primary_market.get("causality"), dict) else {}
    conflicts = [
        item for item in causality.get("conflicts") or []
        if not str(item).startswith("volume=")
    ]
    volume_relation = str(volume_hypothesis.get("relation_to_primary") or "unconfirmed")
    if volume_relation not in {"supports", "confirmed"}:
        conflicts.append(f"volume={volume_relation}")
    causality["conflicts"] = conflicts
    if primary_market:
        primary_market["causality"] = causality
    # Compatibility aliases for downstream engines during the v2 migration.
    market_state["primary_hypothesis"] = (primary or {}).get("direction")
    market_state["alternative_hypothesis"] = (market_state.get("alternative") or {}).get("direction")
    market_state["hypothesis_transition"] = hypothesis_transition
    market_state["volume_context"] = volume_hypothesis
    market_state["news_context"] = news_hypothesis
    publication_state = f"{primary['status']}_context" if primary else "silent"

    payload = {
        "schema_version": 2,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "current_price": price,
        "global_bias": global_bias,
        "active_sessions": sessions,
        "order_flow_context": order_flow_context,
        "current_delivery": current_delivery,
        "market_state": market_state,
        "driver_context": driver_context,
        "usd_context": usd_context,
        "macro_context": macro_context,
        "publication_state": publication_state,
        "primary_scenario": primary,
        "internal_branches": branches,
        "rules": {
            "confirmed_score": CONFIRMED_SCORE,
            "building_score": BUILDING_SCORE,
            "observe_score": OBSERVE_SCORE,
            "telegram_levels": ["observe", "building", "confirmed"],
            "execution_is_manual": True,
        },
    }
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    HYPOTHESIS_RUNTIME_FILE.parent.mkdir(parents=True, exist_ok=True)
    with HYPOTHESIS_RUNTIME_FILE.open("w", encoding="utf-8") as fh:
        json.dump({
            "updated_at_utc": payload["generated_at_utc"],
            "primary_direction": (primary or {}).get("direction"),
            "primary_status": (primary or {}).get("status"),
            "transition": hypothesis_transition,
            "current_location": market_state.get("current_location"),
            "active_leg": market_state["active_leg"],
            "primary_structural_leg": market_state.get("primary_structural_leg"),
            "active_counter_leg": market_state.get("active_counter_leg"),
            "continuation_setup": market_state.get("continuation_setup"),
            "structural_invalidation": ((primary or {}).get("conditions") or {}).get("invalidation"),
        }, fh, ensure_ascii=False, indent=2)

    concise_console(primary, branches, price, global_bias, order_flow_context)
    print(f"\nSCENARIO_SAVED: {OUTPUT_FILE}")
    print("SCENARIO_STATE_ENGINE: PASSED")


if __name__ == "__main__":
    main()
