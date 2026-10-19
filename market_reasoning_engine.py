from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable


TF_RANK = {"W1": 4, "D1": 3, "H4": 2, "H1": 1, "SESSION": 0}
PA_ORDER = {
    "approaching": 0,
    "arrived": 1,
    "testing": 2,
    "reaction_building": 3,
    "displacement": 4,
    "confirmed": 5,
    "delivering": 6,
    "fta_reached": 7,
    "completed": 8,
    "invalidated": 9,
}


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def timeframe(item: dict[str, Any]) -> str:
    explicit = str(item.get("timeframe") or item.get("tf") or "").upper()
    if explicit:
        return explicit
    name = str(item.get("name") or "").upper()
    return name.split()[0] if name else ""


def layer_state(item: dict[str, Any], layer: str = "observation_h1") -> str:
    value = item.get(layer)
    if isinstance(value, dict):
        return str(value.get("state") or "")
    return str(item.get("state") or "")


def opposite(direction: str) -> str:
    return "bearish" if direction == "bullish" else "bullish"


def branch_by_direction(branches: list[dict[str, Any]], direction: str) -> dict[str, Any] | None:
    return next(
        (branch for branch in branches if str(branch.get("direction")) == direction),
        None,
    )


def latest_htf_reaction(objects: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates: list[tuple[datetime, int, dict[str, Any]]] = []
    for item in objects:
        tf = timeframe(item)
        if item.get("kind") != "fvg" or tf not in {"W1", "D1", "H4"}:
            continue
        native = item.get("native_confirmation")
        if not isinstance(native, dict) or not native.get("confirmed"):
            continue
        direction = str(native.get("rejection_direction") or "").lower()
        candle = native.get("rejection_candle")
        candle = candle if isinstance(candle, dict) else {}
        event_time = parse_time(candle.get("time"))
        if not event_time or direction not in {"bullish", "bearish"}:
            continue
        low = number(item.get("low"))
        high = number(item.get("high"))
        if low is None or high is None:
            continue
        source_price = number(candle.get("low" if direction == "bullish" else "high"))
        candidates.append((event_time, TF_RANK[tf], {
            "name": item.get("name"),
            "kind": item.get("kind"),
            "timeframe": tf,
            "direction": direction,
            "low": min(low, high),
            "high": max(low, high),
            "price": source_price if source_price is not None else (low + high) / 2.0,
            "confirmed_at": event_time.isoformat(),
            "confirmation": "native closed rejection",
        }))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return candidates[0][2]


def timeframe_payload(order_flow: dict[str, Any], tf: str) -> dict[str, Any]:
    frames = order_flow.get("timeframes")
    frames = frames if isinstance(frames, dict) else {}
    item = frames.get(tf)
    return item if isinstance(item, dict) else {}


def last_pivot_source(order_flow: dict[str, Any], direction: str) -> dict[str, Any] | None:
    wanted_type = "low" if direction == "bullish" else "high"
    candidates: list[tuple[datetime, int, dict[str, Any]]] = []
    for tf in ("H4", "H1"):
        frame = timeframe_payload(order_flow, tf)
        for structure_name in ("structural_structure", "internal_structure"):
            structure = frame.get(structure_name)
            structure = structure if isinstance(structure, dict) else {}
            pivots = structure.get("recent_pivots")
            pivots = pivots if isinstance(pivots, list) else []
            for pivot in pivots:
                if not isinstance(pivot, dict) or pivot.get("type") != wanted_type:
                    continue
                event_time = parse_time(pivot.get("time"))
                price = number(pivot.get("price"))
                if not event_time or price is None:
                    continue
                candidates.append((event_time, TF_RANK[tf], {
                    "name": f"{tf} fractal {wanted_type}",
                    "kind": "fractal",
                    "timeframe": tf,
                    "direction": direction,
                    "price": price,
                    "confirmed_at": event_time.isoformat(),
                    "confirmation": f"{tf} confirmed pivot",
                }))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return candidates[0][2]


def structural_leg(order_flow: dict[str, Any]) -> dict[str, Any]:
    h1 = timeframe_payload(order_flow, "H1")
    active = h1.get("active_leg")
    active = active if isinstance(active, dict) else {}
    anchor = active.get("anchor")
    anchor = anchor if isinstance(anchor, dict) else {}
    internal = h1.get("internal_structure")
    internal = internal if isinstance(internal, dict) else {}
    return {
        "direction": str(active.get("direction") or h1.get("delivery_direction") or "unknown"),
        "confirmed": bool(active.get("confirmed")),
        "started_at": active.get("start_time"),
        "point_a": {
            "name": "H1 structural anchor",
            "kind": "fractal",
            "timeframe": "H1",
            "type": anchor.get("type"),
            "price": anchor.get("price"),
            "confirmed_at": anchor.get("time"),
        } if anchor else None,
        "phase": str(h1.get("current_phase") or "unknown"),
        "internal_direction": str(internal.get("bias") or "unknown"),
        "displacement": active.get("displacement") if isinstance(active.get("displacement"), dict) else {},
        "latest_closed_h1": ((h1.get("candle_liquidity") or {}).get("latest_closed_time")),
    }


def effective_leg(
    objects: list[dict[str, Any]],
    order_flow: dict[str, Any],
) -> dict[str, Any]:
    structural = structural_leg(order_flow)
    latest_reaction = latest_htf_reaction(objects)
    structural_start = parse_time(structural.get("started_at"))
    reaction_time = parse_time((latest_reaction or {}).get("confirmed_at"))

    if latest_reaction and reaction_time and (not structural_start or reaction_time >= structural_start):
        direction = str(latest_reaction.get("direction"))
        return {
            "direction": direction,
            "kind": "htf_reaction",
            "phase": "delivery",
            "point_a": latest_reaction,
            "confirmation_timeframe": "H1",
            "confirmation": "HTF rejection is closed; H1 route must confirm continuation",
            "structural_parent": structural,
        }

    internal_direction = str(structural.get("internal_direction") or "unknown")
    structural_direction = str(structural.get("direction") or "unknown")
    if (
        structural.get("phase") == "counter_move"
        and internal_direction in {"bullish", "bearish"}
        and internal_direction != structural_direction
    ):
        return {
            "direction": internal_direction,
            "kind": "counter_move",
            "phase": "reaction",
            "point_a": last_pivot_source(order_flow, internal_direction),
            "confirmation_timeframe": "H1",
            "confirmation": f"H1 internal structure is {internal_direction} against the structural leg",
            "structural_parent": structural,
        }

    return {
        "direction": structural_direction,
        "kind": "structural_delivery",
        "phase": structural.get("phase"),
        "point_a": structural.get("point_a"),
        "confirmation_timeframe": "H1",
        "confirmation": "H1 displacement closed through a structural pivot",
        "structural_parent": structural,
    }


def session_levels(session_data: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    sessions = session_data.get("sessions")
    sessions = sessions if isinstance(sessions, list) else []
    for session in sessions:
        if not isinstance(session, dict) or session.get("status") not in {"active", "completed"}:
            continue
        data = session.get("market_data")
        data = data if isinstance(data, dict) else {}
        for boundary in ("high", "low", "open"):
            price = number(data.get(boundary))
            if price is None:
                continue
            output.append({
                "name": f"{session.get('name')} {boundary}",
                "kind": "session_liquidity",
                "timeframe": "SESSION",
                "low": price,
                "high": price,
                "state": "session liquidity",
            })
    return output


def location_cluster(
    objects: list[dict[str, Any]],
    session_data: dict[str, Any],
    price: float,
    proximity_percent: float = 0.28,
    merge_gap_percent: float = 0.20,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for item in [*objects, *session_levels(session_data)]:
        if item.get("kind") not in {"fvg", "volume_imbalance", "gap", "key_level", "session_liquidity"}:
            continue
        tf = timeframe(item)
        if tf not in TF_RANK:
            continue
        low = number(item.get("low"))
        high = number(item.get("high"))
        if low is None:
            continue
        if high is None:
            high = low
        low, high = sorted((low, high))
        distance = 0.0 if low <= price <= high else min(abs(low / price - 1), abs(high / price - 1)) * 100
        if distance > proximity_percent:
            continue
        candidates.append({
            "name": item.get("name"),
            "kind": item.get("kind"),
            "timeframe": tf,
            "low": low,
            "high": high,
            "state": layer_state(item),
            "distance_percent": round(distance, 4),
        })
    if not candidates:
        return None

    candidates.sort(key=lambda item: (item["low"], item["high"]))
    clusters: list[list[dict[str, Any]]] = []
    max_gap = price * merge_gap_percent / 100.0
    for item in candidates:
        proposed_low = min([item["low"], *[member["low"] for member in clusters[-1]]]) if clusters else item["low"]
        proposed_high = max([item["high"], *[member["high"] for member in clusters[-1]]]) if clusters else item["high"]
        span_too_wide = (proposed_high - proposed_low) > price * 0.35 / 100.0
        if not clusters or item["low"] > max(member["high"] for member in clusters[-1]) + max_gap or span_too_wide:
            clusters.append([item])
        else:
            clusters[-1].append(item)

    def cluster_distance(members: list[dict[str, Any]]) -> tuple[int, float, int]:
        low = min(item["low"] for item in members)
        high = max(item["high"] for item in members)
        inside = low <= price <= high
        distance = 0.0 if inside else min(abs(low / price - 1), abs(high / price - 1)) * 100
        rank = max(TF_RANK.get(item["timeframe"], 0) for item in members)
        return (0 if inside else 1, distance, -rank)

    selected = min(clusters, key=cluster_distance)
    low = min(item["low"] for item in selected)
    high = max(item["high"] for item in selected)
    inside = low <= price <= high
    selected.sort(key=lambda item: (-TF_RANK.get(item["timeframe"], 0), item["distance_percent"]))
    states = sorted({str(item.get("state") or "unknown") for item in selected})
    def enrich(member: dict[str, Any]) -> dict[str, Any]:
        member = dict(member)
        width = member["high"] - member["low"]
        role = next((
            str(source.get("directional_role") or "neutral")
            for source in objects
            if source.get("name") == member.get("name")
            and number(source.get("low")) == member["low"]
            and number(source.get("high")) == member["high"]
        ), "neutral")
        member["directional_role"] = role
        member["ce_0_5"] = (member["low"] + member["high"]) / 2.0
        if width > 0 and member["low"] <= price <= member["high"]:
            raw_position = (price - member["low"]) / width
            penetration = 1.0 - raw_position if role == "support" else raw_position if role == "resistance" else None
            member["position_from_low"] = round(raw_position, 3)
            member["penetration_from_entry_edge"] = round(penetration, 3) if penetration is not None else None
            member["depth_state"] = (
                "near_edge" if penetration is not None and penetration < 0.25
                else "before_ce" if penetration is not None and penetration < 0.5
                else "beyond_ce" if penetration is not None and penetration < 0.9
                else "near_far_edge" if penetration is not None
                else "inside"
            )
            member["far_edge"] = member["low"] if role == "support" else member["high"] if role == "resistance" else None
        else:
            member["position_from_low"] = None
            member["penetration_from_entry_edge"] = None
            member["depth_state"] = "outside"
            member["far_edge"] = member["low"] if role == "support" else member["high"] if role == "resistance" else None
        return member

    enriched_members = [enrich(member) for member in selected]
    zone_stack = [
        enrich(member) for member in sorted(
            (item for item in candidates if item["low"] <= price <= item["high"]),
            key=lambda item: (-TF_RANK.get(item["timeframe"], 0), item["high"] - item["low"]),
        )
    ]
    dominant = enriched_members[0]
    return {
        "cluster_id": hashlib.sha1(
            json.dumps([(item["name"], round(item["low"], 2), round(item["high"], 2)) for item in selected]).encode()
        ).hexdigest()[:12],
        "name": " / ".join(dict.fromkeys(str(item["name"]) for item in selected)),
        "low": low,
        "high": high,
        "interaction": "inside" if inside else "approaching",
        "states": states,
        "members": enriched_members,
        "dominant_timeframe": dominant["timeframe"],
        "zone_position": {
            "ce_0_5": dominant.get("ce_0_5"),
            "far_edge": dominant.get("far_edge"),
            "penetration": dominant.get("penetration_from_entry_edge"),
            "depth_state": dominant.get("depth_state"),
            "rule": "A wide HTF zone is context, not a reversal signal; evaluate CE, far edge and closed lower-TF reaction.",
        },
        "zone_stack": zone_stack,
        "reaction_reference_order": [
            {
                "name": item.get("name"), "timeframe": item.get("timeframe"),
                "ce_0_5": item.get("ce_0_5"), "far_edge": item.get("far_edge"),
                "depth_state": item.get("depth_state"),
            }
            for item in zone_stack
        ],
    }


def decision_geometry(
    location: dict[str, Any] | None,
    primary_direction: str,
    price: float,
) -> dict[str, Any] | None:
    """Turn nested zones into an ordered traversal map for the live route."""
    if not isinstance(location, dict) or primary_direction not in {"bullish", "bearish"}:
        return None
    stack = location.get("zone_stack") if isinstance(location.get("zone_stack"), list) else []
    opposing_role = "support" if primary_direction == "bearish" else "resistance"
    relevant = [
        dict(item) for item in stack
        if isinstance(item, dict) and item.get("directional_role") == opposing_role
    ]
    if not relevant:
        return None

    boundaries: list[dict[str, Any]] = []
    zones: list[dict[str, Any]] = []
    for item in relevant:
        ce = number(item.get("ce_0_5"))
        far = number(item.get("far_edge"))
        zone_boundaries = []
        for boundary_type, level in (("CE", ce), ("far_edge", far)):
            if level is None:
                continue
            ahead = level < price if primary_direction == "bearish" else level > price
            state = "ahead" if ahead else "passed"
            boundary = {
                "name": item.get("name"), "timeframe": item.get("timeframe"),
                "boundary_type": boundary_type, "price": level, "state": state,
                "distance_percent": round(abs(level / price - 1) * 100, 4),
            }
            zone_boundaries.append(boundary)
            if ahead:
                boundaries.append(boundary)
        zones.append({
            "name": item.get("name"), "timeframe": item.get("timeframe"),
            "depth_state": item.get("depth_state"),
            "penetration": item.get("penetration_from_entry_edge"),
            "ce_state": next((entry["state"] for entry in zone_boundaries if entry["boundary_type"] == "CE"), "unknown"),
            "far_edge_state": next((entry["state"] for entry in zone_boundaries if entry["boundary_type"] == "far_edge"), "unknown"),
            "boundaries": zone_boundaries,
        })
    boundaries.sort(key=lambda item: item["distance_percent"])
    execution = min(relevant, key=lambda item: TF_RANK.get(str(item.get("timeframe")), 99))
    htf_backstops = [
        item for item in boundaries
        if item["boundary_type"] == "far_edge" and item["timeframe"] in {"W1", "D1", "H4"}
    ]
    return {
        "traversal_direction": primary_direction,
        "opposing_zone_role": opposing_role,
        "active_execution_zone": {
            "name": execution.get("name"), "timeframe": execution.get("timeframe"),
            "low": execution.get("low"), "high": execution.get("high"),
            "depth_state": execution.get("depth_state"),
        },
        "next_boundary": boundaries[0] if boundaries else None,
        "boundary_ladder": boundaries,
        "htf_backstops": htf_backstops,
        "zones": zones,
        "reaction_status": "location_only_pending_closed_confirmation",
        "rule": "CE/far edge marks reaction geography; only closed lower-TF evidence promotes a reversal or counter leg.",
    }


def timeframe_context(
    order_flow: dict[str, Any],
    primary_direction: str,
    counter_leg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a parent-child control ladder, not four independent bias labels."""
    frames: dict[str, dict[str, Any]] = {}
    timeframes = ("W1", "D1", "H4", "H1")
    functions = {"W1": "external_narrative", "D1": "structural_driver", "H4": "setup_context", "H1": "execution"}
    parent_direction: str | None = None
    for tf in timeframes:
        payload = timeframe_payload(order_flow, tf)
        structural = payload.get("structural_structure")
        structural = structural if isinstance(structural, dict) else {}
        internal = payload.get("internal_structure")
        internal = internal if isinstance(internal, dict) else {}
        delivery = str(payload.get("delivery_direction") or "unknown")
        delivery_state = str(payload.get("delivery_state") or "unknown")
        structure = str(structural.get("bias") or "unknown")
        structure_state = str(structural.get("state") or "unknown")
        internal_direction = str(internal.get("bias") or "unknown")
        if structure in {"bullish", "bearish"} and structure_state == "protected":
            control_direction, control_source = structure, "protected_structural_bias"
        elif delivery in {"bullish", "bearish"} and delivery_state == "active":
            control_direction, control_source = delivery, "active_delivery"
        elif internal_direction in {"bullish", "bearish"} and str(internal.get("state") or "unknown") == "protected":
            control_direction, control_source = internal_direction, "protected_internal_structure"
        else:
            control_direction, control_source = "transition", "no_protected_directional_control"
        frames[tf] = {
            "structural_narrative": structure,
            "delivery_direction": delivery,
            "delivery_state": delivery_state,
            "phase": str(payload.get("current_phase") or "unknown"),
            "internal_direction": internal_direction,
            "control_direction": control_direction,
            "control_source": control_source,
            "function": functions[tf],
            "relation_to_parent": (
                "root" if parent_direction is None
                else "aligned" if control_direction == parent_direction
                else "counter" if control_direction in {"bullish", "bearish"} and parent_direction in {"bullish", "bearish"}
                else "transition"
            ),
            "relation_to_primary": (
                "aligned" if control_direction == primary_direction
                else "opposed" if control_direction in {"bullish", "bearish"}
                else "unknown"
            ),
            "delivery_relation_to_primary": (
                "aligned" if delivery == primary_direction
                else "opposed" if delivery in {"bullish", "bearish"}
                else "unknown"
            ),
            "structural_state": structure_state,
        }
        if control_direction in {"bullish", "bearish"}:
            parent_direction = control_direction
    sync = order_flow.get("timeframe_sync")
    sync = sync if isinstance(sync, dict) else {}
    external = frames["W1"]["control_direction"]
    counter_direction = str((counter_leg or {}).get("direction") or "unknown")
    counter_role = str((counter_leg or {}).get("leg_role") or "none")
    aligned_controls = [tf for tf in timeframes if frames[tf]["control_direction"] == primary_direction]
    summary = f"{'/'.join(aligned_controls) or 'No timeframe'} {primary_direction} control"
    parent_counter_frames = [tf for tf in timeframes if frames[tf]["relation_to_parent"] == "counter"]
    if parent_counter_frames:
        details = ", ".join(f"{tf} {frames[tf]['control_direction']}" for tf in parent_counter_frames)
        summary += f"; {details} control is counter to its parent"
    if counter_direction in {"bullish", "bearish"} and counter_direction != primary_direction:
        summary += f"; H1 {counter_direction} {counter_role} is nested against the parent route"
    return {
        "frames": frames,
        "external_narrative": external,
        "external_narrative_source": frames["W1"]["control_source"],
        "dominant_delivery": primary_direction,
        "delivery_sync": ((sync.get("delivery_sync") or {}).get("status")) if isinstance(sync.get("delivery_sync"), dict) else "unknown",
        "interpretation": summary,
        "nested_counter": {
            "direction": counter_direction,
            "role": counter_role,
            "status": "nested_not_reversal" if counter_direction in {"bullish", "bearish"} and counter_direction != primary_direction else "none",
        },
        "rule": "Structural narrative is context; active delivery and its parent-child hierarchy control the live route.",
    }


def ordered_route(branch: dict[str, Any] | None, price: float) -> list[dict[str, Any]]:
    if not isinstance(branch, dict):
        return []
    direction = str(branch.get("direction") or "")
    checkpoints = [dict(item) for item in branch.get("route_checkpoints") or [] if isinstance(item, dict)]
    target_price = number((branch.get("point_b") or {}).get("price")) if isinstance(branch.get("point_b"), dict) else None

    def bounds(item: dict[str, Any]) -> tuple[float | None, float | None]:
        low = number(item.get("low"))
        high = number(item.get("high"))
        level = number(item.get("price"))
        if low is None:
            low = level
        if high is None:
            high = level
        return (min(low, high), max(low, high)) if low is not None and high is not None else (None, None)

    def is_future(item: dict[str, Any]) -> bool:
        low, high = bounds(item)
        if low is None or high is None:
            return False
        # A zone containing price is the current decision area, not a future
        # objective.  A zone behind delivery is already passed even if its
        # legacy status has not yet been rewritten by the level-state engine.
        if low <= price <= high:
            item["route_position"] = "current"
            return False
        ahead = high < price if direction == "bearish" else low > price
        before_target = True
        if target_price is not None:
            before_target = low > target_price if direction == "bearish" else high < target_price
        item["route_position"] = "ahead" if ahead else "passed"
        return ahead and before_target and item.get("status") != "passed"

    checkpoints.sort(key=lambda item: abs((number(item.get("price")) or price) - price))
    unresolved = [item for item in checkpoints if is_future(item)]
    for index, item in enumerate(unresolved):
        item["route_role"] = "FTA" if index == 0 else "TA"
    target = branch.get("point_b")
    if isinstance(target, dict):
        target_price = number(target.get("price"))
        reached = bool(
            target_price is not None
            and (
                (direction == "bearish" and price <= target_price)
                or (direction == "bullish" and price >= target_price)
            )
        )
        unresolved.append({
            **target,
            "kind": "point_b",
            "route_role": "POINT_B",
            "status": "reached" if reached else "target",
            "state": "reached" if reached else target.get("state", "untouched"),
        })
    return unresolved


def leg_lifecycle_id(direction: str, point_a: Any, parent_id: str | None = None) -> str:
    point_a = point_a if isinstance(point_a, dict) else {}
    material = {
        "direction": direction,
        "point_a_price": number(point_a.get("price")),
        "point_a_time": point_a.get("confirmed_at") or point_a.get("time"),
        "point_a_name": point_a.get("name"),
        "parent_id": parent_id,
    }
    return hashlib.sha1(json.dumps(material, sort_keys=True).encode()).hexdigest()[:16]


def counter_leg_route(
    objects: list[dict[str, Any]], direction: str, price: float,
) -> list[dict[str, Any]]:
    """Build only the local reaction route, never the structural Point B."""
    opposing_role = "resistance" if direction == "bullish" else "support"
    candidates: list[dict[str, Any]] = []
    for item in objects:
        kind = str(item.get("kind") or "")
        if kind not in {"fvg", "volume_imbalance", "gap", "key_level"}:
            continue
        if kind != "key_level" and str(item.get("directional_role") or "") != opposing_role:
            continue
        tf = timeframe(item)
        if tf not in {"D1", "H4", "H1", "SESSION"}:
            continue
        low = number(item.get("low"))
        high = number(item.get("high"))
        level = number(item.get("price"))
        if low is None:
            low = level
        if high is None:
            high = level
        if low is None or high is None:
            continue
        low, high = sorted((low, high))
        ahead = low > price if direction == "bullish" else high < price
        if not ahead:
            continue
        state = layer_state(item)
        passed = "accepted_above" if direction == "bullish" else "accepted_below"
        if state == passed or str(item.get("validity_status") or "valid") == "invalidated":
            continue
        edge = low if direction == "bullish" else high
        candidates.append({
            "name": item.get("name") or kind,
            "kind": kind,
            "timeframe": tf,
            "low": low,
            "high": high,
            "price": edge,
            "state": state or item.get("state") or "unknown",
            "status": "checkpoint",
            "distance_percent": round(abs(edge / price - 1) * 100, 4),
        })
    candidates.sort(key=lambda item: item["distance_percent"])
    for index, item in enumerate(candidates[:3]):
        item["route_role"] = "FTA" if index == 0 else "TA"
    return candidates[:3]


def structural_leg_state(
    primary: dict[str, Any] | None,
    route: list[dict[str, Any]],
    stage: str,
) -> dict[str, Any] | None:
    if not isinstance(primary, dict):
        return None
    direction = str(primary.get("direction") or "unknown")
    raw_point_a = primary.get("point_a") if isinstance(primary.get("point_a"), dict) else None
    point_a = normalize_structural_point_a(raw_point_a, direction)
    point_b = primary.get("point_b") if isinstance(primary.get("point_b"), dict) else None
    lifecycle_id = leg_lifecycle_id(direction, point_a)
    return {
        "lifecycle_id": lifecycle_id,
        "leg_role": "primary_structural",
        "direction": direction,
        "state": stage,
        "point_a": point_a,
        "minimum_target": next((item for item in route if item.get("route_role") in {"FTA", "POINT_B"}), None),
        "point_b": point_b,
        "route": route,
        "confirmation": ((primary.get("conditions") or {}).get("confirmation")),
        "invalidation": ((primary.get("conditions") or {}).get("invalidation")),
    }


def normalize_structural_point_a(point_a: dict[str, Any] | None, direction: str) -> dict[str, Any] | None:
    if not isinstance(point_a, dict):
        return None
    result = dict(point_a)
    native = result.get("native_confirmation") if isinstance(result.get("native_confirmation"), dict) else {}
    candle = native.get("rejection_candle") if isinstance(native.get("rejection_candle"), dict) else {}
    # A confirmed rejection owns Point A.  The source-zone edge is context,
    # not the origin of the delivered move.
    anchor = number(candle.get("high" if direction == "bearish" else "low")) if native.get("confirmed") else None
    if anchor is None:
        anchor = number(result.get("price"))
    if anchor is None:
        anchor = number(result.get("low" if direction == "bullish" else "high"))
    result["source_zone"] = {"name": result.get("name"), "timeframe": timeframe(result), "low": number(result.get("low")), "high": number(result.get("high"))}
    result["price"] = anchor
    result["anchor_type"] = "rejection_high" if direction == "bearish" else "rejection_low"
    result["confirmed_at"] = candle.get("time") or result.get("confirmed_at") or result.get("lifecycle_start")
    return result


def reaction_source_zone(location: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(location, dict):
        return None
    stack = location.get("zone_stack") if isinstance(location.get("zone_stack"), list) else []
    members = location.get("members") if isinstance(location.get("members"), list) else []
    pool = stack or members
    ranked = sorted(
        (item for item in pool if isinstance(item, dict) and item.get("directional_role") in {"support", "resistance"}),
        key=lambda item: (
            TF_RANK.get(str(item.get("timeframe")), 99),
            float(item.get("distance_percent") or 0),
            float(item.get("high", 0)) - float(item.get("low", 0)),
        ),
    )
    return ranked[0] if ranked else None


def location_reaction_direction(location: dict[str, Any] | None) -> str | None:
    source = reaction_source_zone(location)
    if not source:
        return None
    return "bullish" if source.get("directional_role") == "support" else "bearish"


def counter_candidate_state(location, primary_leg, objects, order_flow, price, previous_state):
    primary_direction = str((primary_leg or {}).get("direction") or "unknown")
    source = reaction_source_zone(location)
    direction = location_reaction_direction(location)
    if direction not in {"bullish", "bearish"} or direction == primary_direction:
        return None
    states = set((location or {}).get("states") or [])
    source_state = str((source or {}).get("state") or "unknown")
    evidence_states = {"swept_rejected", "reaction_building", "reclaimed_above", "reclaimed_below", "swept", "awaiting_closed_observation"}
    has_reaction_evidence = bool(states & evidence_states or source_state in evidence_states)
    source_tf = str((source or {}).get("timeframe") or "unknown")
    early_local_watch = (
        source_tf in {"H1", "H4"}
        and str((location or {}).get("interaction") or "") in {"approaching", "inside"}
    )
    if not has_reaction_evidence and not early_local_watch:
        return None
    parent_id = (primary_leg or {}).get("lifecycle_id")
    previous = (previous_state or {}).get("counter_candidate")
    previous = previous if isinstance(previous, dict) else None
    same = bool(previous and previous.get("direction") == direction and previous.get("parent_lifecycle_id") == parent_id)
    point_a = previous.get("point_a") if same else (
        last_pivot_source(order_flow, direction) if has_reaction_evidence else None
    )
    if not point_a:
        local = source
        if local:
            # Before touch, Point A is explicitly provisional and anchored at
            # the entry edge. A confirmed counter leg will later own its pivot.
            point_a = {
                "name": f"{local.get('timeframe')} provisional reaction anchor",
                "timeframe": local.get("timeframe"), "kind": "zone_anchor",
                "price": local.get("high" if direction == "bullish" else "low"),
                "source_zone": {k: local.get(k) for k in ("name", "timeframe", "low", "high")},
            }
    route = counter_leg_route(objects, direction, price)
    minimum = next((item for item in route if item.get("route_role") == "FTA"), None)
    if not has_reaction_evidence and minimum is None:
        # An early heads-up must describe where the reaction could travel.
        # Location without a balanced FTA is context, not an opportunity.
        return None
    opportunity_stage = (
        "approaching" if not has_reaction_evidence and (location or {}).get("interaction") == "approaching"
        else "testing" if not has_reaction_evidence
        else "reaction_building"
    )
    return {
        "lifecycle_id": previous.get("lifecycle_id") if same else leg_lifecycle_id(direction, point_a, parent_id),
        "parent_lifecycle_id": parent_id, "leg_role": "counter_candidate",
        "direction": direction, "kind": "provisional_htf_reaction",
        "state": "watching" if opportunity_stage == "approaching" else "reaction_testing",
        "opportunity_stage": opportunity_stage,
        "source_zone": {k: (source or {}).get(k) for k in ("name", "timeframe", "low", "high", "state")},
        "point_a": point_a, "minimum_target": minimum, "point_b": minimum, "route": route,
        "confirmation": f"Require closed H1 {direction} displacement/acceptance",
        "invalidation": f"Closed H1 {primary_direction} continuation through the reaction source cancels the candidate",
    }


def counter_leg_state(
    candidate: dict[str, Any],
    primary_leg: dict[str, Any] | None,
    objects: list[dict[str, Any]],
    price: float,
    stage: str,
    previous_state: dict[str, Any] | None,
) -> dict[str, Any] | None:
    primary_direction = str((primary_leg or {}).get("direction") or "unknown")
    direction = str(candidate.get("direction") or "unknown")
    if direction not in {"bullish", "bearish"} or direction == primary_direction:
        return None
    previous = (previous_state or {}).get("active_counter_leg")
    previous = previous if isinstance(previous, dict) else None
    parent_id = (primary_leg or {}).get("lifecycle_id")
    same_lifecycle = bool(
        previous
        and previous.get("direction") == direction
        and previous.get("parent_lifecycle_id") == parent_id
        and previous.get("state") not in {"completed", "invalidated", "failed"}
    )
    point_a = previous.get("point_a") if same_lifecycle else candidate.get("point_a")
    route = counter_leg_route(objects, direction, price)
    minimum = next((item for item in route if item.get("route_role") == "FTA"), None)
    lifecycle_id = previous.get("lifecycle_id") if same_lifecycle else leg_lifecycle_id(direction, point_a, parent_id)
    point_a_price = number((point_a or {}).get("price")) if isinstance(point_a, dict) else None
    invalidation = None
    if point_a_price is not None:
        side = "below" if direction == "bullish" else "above"
        invalidation = f"IF H1 accepts {side} {point_a_price:.5f}, THEN the counter leg fails"
    return {
        "lifecycle_id": lifecycle_id,
        "parent_lifecycle_id": parent_id,
        "leg_role": "active_counter",
        "direction": direction,
        "kind": candidate.get("kind"),
        "state": stage,
        "point_a": point_a,
        "minimum_target": minimum,
        "point_b": minimum,
        "route": route,
        "confirmation": candidate.get("confirmation"),
        "invalidation": invalidation,
    }


def continuation_setup_state(
    candidate: dict[str, Any],
    primary_leg: dict[str, Any] | None,
    route: list[dict[str, Any]],
    stage: str,
    previous_state: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Create a fresh execution leg after a material counter-leg pullback.

    The structural A->B identity stays frozen.  This local leg only resets
    entry timing so a valid continuation is not labelled late from the old A.
    """
    primary_direction = str((primary_leg or {}).get("direction") or "unknown")
    candidate_direction = str(candidate.get("direction") or "unknown")
    previous_state = previous_state or {}
    previous_counter = previous_state.get("active_counter_leg")
    previous_counter = previous_counter if isinstance(previous_counter, dict) else None
    previous_setup = previous_state.get("continuation_setup")
    previous_setup = previous_setup if isinstance(previous_setup, dict) else None
    parent_id = (primary_leg or {}).get("lifecycle_id")
    same_setup = bool(
        previous_setup
        and previous_setup.get("direction") == primary_direction
        and previous_setup.get("parent_lifecycle_id") == parent_id
        and previous_setup.get("state") not in {"completed", "invalidated", "failed"}
    )
    # A new execution leg is justified only by an observed counter leg in the
    # preceding state.  Normal continuation must not reset on every candle.
    if not same_setup and not previous_counter:
        return None
    if candidate_direction != primary_direction or primary_direction not in {"bullish", "bearish"}:
        return previous_setup if same_setup else None
    point_a = previous_setup.get("point_a") if same_setup else candidate.get("point_a")
    lifecycle_id = (
        previous_setup.get("lifecycle_id")
        if same_setup
        else leg_lifecycle_id(primary_direction, point_a, parent_id)
    )
    return {
        "lifecycle_id": lifecycle_id,
        "parent_lifecycle_id": parent_id,
        "rebalanced_counter_leg_id": (previous_counter or {}).get("lifecycle_id"),
        "leg_role": "continuation_setup",
        "direction": primary_direction,
        "kind": candidate.get("kind") or "post_pullback_continuation",
        "state": stage,
        "point_a": point_a,
        "minimum_target": next(
            (item for item in route if item.get("route_role") in {"FTA", "POINT_B"}), None,
        ),
        "point_b": (primary_leg or {}).get("point_b"),
        "route": route,
        "confirmation": candidate.get("confirmation") or (primary_leg or {}).get("confirmation"),
        "invalidation": (primary_leg or {}).get("invalidation"),
    }


def pa_stage(
    location: dict[str, Any] | None,
    leg: dict[str, Any],
    transition: dict[str, Any],
    route: list[dict[str, Any]],
    price: float,
) -> str:
    if transition.get("state") == "confirmed_transition":
        return "confirmed"
    if location and location_reaction_direction(location) == str(leg.get("direction") or "unknown"):
        states = set(location.get("states") or [])
        if states & {"swept_rejected", "reaction_building", "reclaimed_above", "reclaimed_below"}:
            return "reaction_building"
        if location.get("interaction") == "inside":
            return "testing"
        if location.get("interaction") == "approaching":
            return "approaching"
    first = next((item for item in route if item.get("route_role") == "FTA"), None)
    if first:
        low = number(first.get("low"))
        high = number(first.get("high"))
        if low is None:
            low = number(first.get("price"))
        if high is None:
            high = low
        if low is not None and high is not None and min(low, high) <= price <= max(low, high):
            return "fta_reached"
    if leg.get("kind") == "htf_reaction":
        return "displacement"
    return "delivering"


def opportunity_state(
    leg: dict[str, Any],
    stage: str,
    route: list[dict[str, Any]],
    price: float,
) -> dict[str, Any]:
    point_a = leg.get("point_a")
    point_a = point_a if isinstance(point_a, dict) else {}
    start = number(point_a.get("price"))
    first = next((item for item in route if item.get("route_role") in {"FTA", "POINT_B"}), None)
    first_price = number((first or {}).get("price"))
    progress = None
    if start is not None and first_price is not None and start != first_price:
        progress = abs(price - start) / abs(first_price - start)

    # Geometry wins over the descriptive PA stage.  Price may still be
    # "testing" a wide HTF zone after most of the tradable A->FTA move has
    # already happened; that is late, not a forming setup.
    if stage == "approaching":
        # Price has not activated Point A yet; geometric progress from a
        # provisional zone edge must not turn an early watch into LATE.
        progress = None
        state, reason = "early_attention", "Price is approaching the decision cluster."
    elif progress is not None and progress >= 1.0:
        state, reason = "missed", "The minimum reaction target has already been delivered."
    elif progress is not None and progress >= 0.35:
        state, reason = "late", "A material part of the route to the minimum objective has already been delivered."
    elif stage in {"arrived", "testing", "reaction_building"}:
        state, reason = "setup_forming", "Reaction is developing; structural confirmation is pending."
    elif stage == "fta_reached":
        state, reason = "missed", "The minimum reaction target is reached or nearly reached."
    elif stage in {"confirmed", "displacement"} and (progress is None or progress <= 0.35):
        state, reason = "active", "The leg is confirmed and meaningful space remains to FTA."
    else:
        state, reason = "observe", "The route exists, but timing quality is not established."
    return {
        "state": state,
        "reason": reason,
        "progress_to_fta": round(progress, 3) if progress is not None else None,
        "entry_instruction": "attention only; execution remains manual",
    }


def condition_level(condition: Any) -> float | None:
    match = re.search(r"\b(?:above|below)\s+(-?\d+(?:\.\d+)?)", str(condition or ""), re.I)
    return number(match.group(1)) if match else None


def hypothesis_controls(
    primary: dict[str, Any] | None,
    alternative: dict[str, Any] | None,
    location: dict[str, Any] | None,
    price: float,
) -> dict[str, Any]:
    primary = primary or {}
    alternative = alternative or {}
    conditions = primary.get("conditions") if isinstance(primary.get("conditions"), dict) else {}
    alternative_conditions = alternative.get("conditions") if isinstance(alternative.get("conditions"), dict) else {}
    confirmation = str(conditions.get("confirmation") or "")
    invalidation = str(conditions.get("invalidation") or "")
    alternative_trigger = str(alternative_conditions.get("confirmation") or "")
    target = primary.get("point_b") if isinstance(primary.get("point_b"), dict) else {}
    target_price = number(target.get("price"))
    direction = str(primary.get("direction") or "unknown")

    if location and target_price is not None:
        low = number(location.get("low"))
        high = number(location.get("high"))
        if low is not None and high is not None and low <= price <= high and low <= target_price <= high:
            confirmation = (
                f"Primary {direction} delivery is already inside {location.get('name')}; "
                f"continuation toward {target.get('name')} {target_price:.2f} remains active while closed H1 structure stays {direction}. "
                "A new entry requires a balanced pullback or a new local acceptance, not the old zone edge."
            )

    invalidation_level = condition_level(invalidation)
    alternative_level = condition_level(alternative_trigger)
    transition_band = None
    if invalidation_level is not None and alternative_level is not None and invalidation_level != alternative_level:
        transition_band = {
            "low": min(invalidation_level, alternative_level),
            "high": max(invalidation_level, alternative_level),
            "meaning": "primary downgraded, opposite branch not yet confirmed",
        }
    return {
        "confirmation": confirmation,
        "invalidation": invalidation,
        "alternative_trigger": alternative_trigger,
        "transition_band": transition_band,
    }


def causal_hypothesis(
    primary: dict[str, Any] | None,
    primary_leg: dict[str, Any] | None,
    route: list[dict[str, Any]],
    volume_context: dict[str, Any],
    news_context: dict[str, Any],
) -> dict[str, Any]:
    """Turn detected facts into an auditable hypothesis, not a slogan."""
    primary = primary or {}
    primary_leg = primary_leg or {}
    point_a = primary_leg.get("point_a") if isinstance(primary_leg.get("point_a"), dict) else {}
    point_b = primary.get("point_b") if isinstance(primary.get("point_b"), dict) else {}
    evidence = [item for item in primary.get("evidence") or [] if isinstance(item, dict)]
    codes = {str(item.get("code") or "") for item in evidence}
    source_tf = str(point_a.get("timeframe") or "unknown")
    source = {
        "claim": f"{source_tf} source {point_a.get('name', 'structural origin')} at {number(point_a.get('price'))}",
        "status": "observed" if point_a else "unavailable",
        "evidence": point_a,
    }
    reaction_confirmed = bool(point_a.get("confirmation") or point_a.get("confirmed_at"))
    reaction = {
        "claim": str(point_a.get("confirmation") or "No closed source reaction attached"),
        "status": "observed" if reaction_confirmed else "inferred",
    }
    h1_confirmed = "H1_OPPOSING_FVG_LOST" in codes
    confirmation = {
        "claim": str(primary_leg.get("confirmation") or "H1 confirmation pending"),
        "status": "verified" if h1_confirmed else "pending",
        "timeframe": "H1",
    }
    first = next((item for item in route if item.get("route_role") in {"FTA", "POINT_B"}), None)
    path = {
        "claim": f"Space remains toward {(first or point_b).get('name', 'target')}",
        "status": "observed" if first or point_b else "unavailable",
        "first_objective": first,
        "structural_objective": point_b,
        "clear": bool(primary.get("path_clear")),
    }
    conflicts: list[str] = []
    volume_relation = str(volume_context.get("relation_to_primary") or "unconfirmed")
    if volume_relation not in {"supports", "confirmed"}:
        conflicts.append(f"volume={volume_relation}")
    news_role = str(news_context.get("role") or "none")
    if news_role not in {"supports", "driver"}:
        conflicts.append(f"news={news_role}")
    missing = [
        label for label, condition in (
            ("source", not point_a), ("H1 confirmation", not h1_confirmed),
            ("objective", not point_b), ("path", not (first or point_b)),
        ) if condition
    ]
    status = "confirmed" if not missing and h1_confirmed else "forming" if point_a and point_b else "incomplete"
    return {
        "direction": primary.get("direction"),
        "status": status,
        "source": source,
        "reaction": reaction,
        "lower_tf_confirmation": confirmation,
        "path": path,
        "conflicts": conflicts,
        "missing": missing,
        "summary": (
            f"{source['claim']} → reaction {reaction['status']} → "
            f"H1 {confirmation['status']} → {(path.get('first_objective') or point_b).get('name', 'no target')}"
        ),
        "rule": "No causal claim may be promoted from price location alone.",
    }


def build_reasoning_state(
    objects: list[dict[str, Any]],
    session_data: dict[str, Any],
    order_flow: dict[str, Any],
    branches: list[dict[str, Any]],
    primary: dict[str, Any] | None,
    transition: dict[str, Any],
    volume_context: dict[str, Any],
    news_context: dict[str, Any],
    price: float,
    previous_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    observed_leg = effective_leg(objects, order_flow)
    # The active leg explains what price is doing now.  It must not silently
    # replace the stateful primary hypothesis: a pullback/counter move is not
    # a new route until the transition state machine confirms it.
    causal_primary = primary
    primary_direction = str((causal_primary or {}).get("direction") or "unknown")
    alternative = branch_by_direction(branches, opposite(primary_direction)) if primary_direction in {"bullish", "bearish"} else None
    route = ordered_route(causal_primary, price)
    location = location_cluster(objects, session_data, price)
    geometry = decision_geometry(location, primary_direction, price)
    if location and geometry:
        location["decision_geometry"] = geometry
    provisional_counter_route = (
        counter_leg_route(objects, str(observed_leg.get("direction")), price)
        if str(observed_leg.get("direction")) != primary_direction
        else []
    )
    active_route = provisional_counter_route or route
    stage = pa_stage(location, observed_leg, transition, active_route, price)
    structural_stage = "pullback" if provisional_counter_route else stage
    primary_leg = structural_leg_state(causal_primary, route, structural_stage)
    counter_leg = counter_leg_state(
        observed_leg, primary_leg, objects, price, stage, previous_state,
    )
    counter_candidate = None if counter_leg else counter_candidate_state(location, primary_leg, objects, order_flow, price, previous_state)
    continuation_setup = continuation_setup_state(
        observed_leg, primary_leg, route, stage, previous_state,
    ) if not counter_leg else None
    if counter_candidate:
        structural_stage = "pullback"
        if primary_leg:
            primary_leg["state"] = structural_stage
    structural_opportunity = opportunity_state(primary_leg or {}, structural_stage, route, price)
    counter_opportunity = (
        opportunity_state(counter_leg, stage, counter_leg.get("route") or [], price)
        if counter_leg else None
    )
    candidate_opportunity = opportunity_state(
        counter_candidate,
        str(counter_candidate.get("opportunity_stage") or "reaction_building"),
        counter_candidate.get("route") or [],
        price,
    ) if counter_candidate else None
    continuation_opportunity = (
        opportunity_state(continuation_setup, stage, route, price)
        if continuation_setup else None
    )
    opportunity = counter_opportunity or candidate_opportunity or continuation_opportunity or structural_opportunity
    controls = hypothesis_controls(causal_primary, alternative, location, price)
    causality = causal_hypothesis(causal_primary, primary_leg, route, volume_context, news_context)
    route_id = hashlib.sha1(json.dumps({
        "direction": primary_direction,
        "a": ((primary_leg or {}).get("point_a") or {}).get("price") if isinstance((primary_leg or {}).get("point_a"), dict) else None,
        "b": ((causal_primary or {}).get("point_b") or {}).get("price") if isinstance((causal_primary or {}).get("point_b"), dict) else None,
    }, sort_keys=True).encode()).hexdigest()[:12]
    return {
        "schema_version": 1,
        "route_id": route_id,
        "primary": {
            "direction": primary_direction,
            "status": "confirmed" if transition.get("switch_confirmed") else stage,
            "point_a": (primary_leg or {}).get("point_a"),
            "minimum_target": next((item for item in route if item.get("route_role") in {"FTA", "POINT_B"}), None),
            "point_b": (causal_primary or {}).get("point_b"),
            "route": route,
            "confirmation": controls["confirmation"],
            "invalidation": controls["invalidation"],
            "transition_band": controls["transition_band"],
            "score": (causal_primary or {}).get("score"),
            "causality": causality,
        },
        "alternative": {
            "direction": (alternative or {}).get("direction"),
            "status": "testing" if transition.get("state") == "alternative_testing" else "dormant",
            "trigger": ((alternative or {}).get("conditions") or {}).get("confirmation"),
            "point_b": (alternative or {}).get("point_b"),
        } if alternative else None,
        "primary_structural_leg": primary_leg,
        "active_counter_leg": counter_leg,
        "counter_candidate": counter_candidate,
        "continuation_setup": continuation_setup,
        # Compatibility alias: downstream code sees the leg price is acting
        # on now, while the primary structural leg remains separately stored.
        "active_leg": counter_leg or counter_candidate or continuation_setup or primary_leg or observed_leg,
        "timeframe_hierarchy": timeframe_context(order_flow, primary_direction, counter_leg or counter_candidate),
        "current_location": location,
        "pa_state": {
            "stage": stage,
            "sequence": "approaching -> arrived -> testing -> reaction_building -> displacement -> confirmed -> delivering -> FTA reached",
            "transition": transition,
        },
        "opportunity": opportunity,
        "structural_opportunity": structural_opportunity,
        "counter_opportunity": counter_opportunity,
        "counter_candidate_opportunity": candidate_opportunity,
        "continuation_opportunity": continuation_opportunity,
        "volume": volume_context,
        "news": news_context,
        "score_policy": "diagnostic_only",
    }
