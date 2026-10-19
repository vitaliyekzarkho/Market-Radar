from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SYMBOL = "GOLD.pro"
SYMBOL_FILE = SYMBOL.replace(".", "_")
DATA_DIR = Path("data")

SCENARIO_FILE = DATA_DIR / "scenario_states" / f"{SYMBOL_FILE}.json"
STATE_FILE = DATA_DIR / "runtime" / f"{SYMBOL_FILE}_event_state.json"
EVENT_FILE = DATA_DIR / "events" / f"{SYMBOL_FILE}.json"
HISTORY_FILE = DATA_DIR / "events" / f"{SYMBOL_FILE}_history.jsonl"

SCORE_SHIFT_THRESHOLD = 10
ALERTABLE_SEVERITIES = {"watch", "important"}


def load_json(path: Path, required: bool = True) -> dict[str, Any]:
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"Missing required file: {path}")
        return {}
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    temporary.replace(path)


def number(value: Any) -> float | None:
    try:
        return round(float(value), 5)
    except (TypeError, ValueError):
        return None


def zone_snapshot(zone: Any) -> dict[str, Any] | None:
    if not isinstance(zone, dict) or not zone:
        return None
    return {
        "name": zone.get("name"),
        "low": number(zone.get("low")),
        "high": number(zone.get("high")),
    }


def target_snapshot(target: Any) -> dict[str, Any] | None:
    if not isinstance(target, dict) or not target:
        return None
    return {
        "name": target.get("name"),
        "price": number(target.get("price")),
    }


def branch_snapshot(branch: Any) -> dict[str, Any]:
    branch = branch if isinstance(branch, dict) else {}
    conditions = branch.get("conditions") if isinstance(branch.get("conditions"), dict) else {}
    checkpoints = branch.get("route_checkpoints") if isinstance(branch.get("route_checkpoints"), list) else []
    return {
        "direction": branch.get("direction"),
        "status": branch.get("status", "silent"),
        "score": int(branch.get("score") or 0),
        "point_b": target_snapshot(branch.get("point_b")),
        "decision_zone": zone_snapshot(branch.get("next_decision_zone")),
        "decision_state": branch.get("next_decision_state", "none"),
        "pullback_zone": zone_snapshot(branch.get("pullback_zone")),
        "confirmation": conditions.get("confirmation"),
        "invalidation": conditions.get("invalidation"),
        "route_checkpoints": [
            {
                "name": item.get("name"), "kind": item.get("kind"),
                "low": number(item.get("low")), "high": number(item.get("high")),
                "price": number(item.get("price")), "status": item.get("status"),
                "route_role": item.get("route_role"),
            }
            for item in checkpoints if isinstance(item, dict)
        ],
    }


def driver_snapshot(driver: Any) -> dict[str, Any]:
    driver = driver if isinstance(driver, dict) else {}
    return {
        "available": bool(driver.get("available")),
        "title": driver.get("title"),
        "status": driver.get("status"),
        "effect": driver.get("effect"),
        "confidence": driver.get("confidence"),
        "reaction_status": driver.get("reaction_status"),
        "event_time_utc": driver.get("event_time_utc"),
        "actual": driver.get("actual"),
        "forecast": driver.get("forecast"),
        "importance": driver.get("importance"),
    }


def scenario_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    branches: dict[str, dict[str, Any]] = {}
    raw_branches = payload.get("internal_branches") or []
    if isinstance(raw_branches, list):
        for raw in raw_branches:
            branch = branch_snapshot(raw)
            direction = str(branch.get("direction") or "")
            if direction:
                branches[direction] = branch
    primary = branch_snapshot(payload.get("primary_scenario"))
    trade_payload = payload.get("trade_ideas") if isinstance(payload.get("trade_ideas"), dict) else {}
    raw_ideas = trade_payload.get("ideas") if isinstance(trade_payload.get("ideas"), list) else []
    ideas = {
        str(item.get("idea_id")): {
            "type": item.get("type"), "direction": item.get("direction"),
            "state": item.get("state"), "quality": item.get("quality"),
            "objectives": [target_snapshot(target) for target in item.get("objectives", []) if isinstance(target, dict)],
        }
        for item in raw_ideas if isinstance(item, dict) and item.get("idea_id")
    }
    market = payload.get("market_state") if isinstance(payload.get("market_state"), dict) else {}
    structural = market.get("primary_structural_leg") if isinstance(market.get("primary_structural_leg"), dict) else {}
    counter = market.get("active_counter_leg") if isinstance(market.get("active_counter_leg"), dict) else {}
    candidate = market.get("counter_candidate") if isinstance(market.get("counter_candidate"), dict) else {}
    continuation = market.get("continuation_setup") if isinstance(market.get("continuation_setup"), dict) else {}
    return {
        "symbol": payload.get("symbol", SYMBOL),
        "scenario_generated_at_utc": payload.get("generated_at_utc"),
        "current_price": number(payload.get("current_price")),
        "global_bias": payload.get("global_bias"),
        "primary": primary,
        "branches": branches,
        "driver": driver_snapshot(payload.get("driver_context")),
        "ideas": ideas,
        "leg_lifecycle": {
            "structural": structural.get("lifecycle_id"),
            "counter": counter.get("lifecycle_id"),
            "counter_candidate": candidate.get("lifecycle_id"),
            "continuation": continuation.get("lifecycle_id"),
            "pa_stage": ((market.get("pa_state") or {}).get("stage")) if isinstance(market.get("pa_state"), dict) else None,
            "opportunity": ((market.get("opportunity") or {}).get("state")) if isinstance(market.get("opportunity"), dict) else None,
        },
    }


def event(event_type: str, severity: str, text: str, before: Any, after: Any) -> dict[str, Any]:
    return {
        "type": event_type,
        "severity": severity,
        "text": text,
        "before": before,
        "after": after,
    }


def compare_primary(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    old_direction = previous.get("direction")
    new_direction = current.get("direction")
    if old_direction != new_direction:
        events.append(event(
            "PRIMARY_DIRECTION_CHANGED",
            "important",
            f"Primary direction changed: {old_direction or 'none'} -> {new_direction or 'none'}",
            old_direction,
            new_direction,
        ))

    old_status = previous.get("status", "silent")
    new_status = current.get("status", "silent")
    if old_status != new_status:
        events.append(event(
            "PRIMARY_STATUS_CHANGED",
            "important",
            f"Primary status changed: {old_status} -> {new_status}",
            old_status,
            new_status,
        ))

    old_score = int(previous.get("score") or 0)
    new_score = int(current.get("score") or 0)
    if abs(new_score - old_score) >= SCORE_SHIFT_THRESHOLD:
        events.append(event(
            "PRIMARY_SCORE_SHIFT",
            "watch",
            f"Primary score changed materially: {old_score} -> {new_score}",
            old_score,
            new_score,
        ))

    for key, event_type, label, severity in (
        ("point_b", "POINT_B_CHANGED", "Point B", "important"),
        ("decision_zone", "DECISION_ZONE_CHANGED", "Decision zone", "watch"),
        ("pullback_zone", "PULLBACK_ZONE_CHANGED", "Pullback zone", "watch"),
        ("route_checkpoints", "ROUTE_CHECKPOINTS_CHANGED", "Route checkpoints", "watch"),
    ):
        before = previous.get(key)
        after = current.get(key)
        if before != after:
            events.append(event(event_type, severity, f"{label} changed", before, after))

    old_decision_state = previous.get("decision_state", "none")
    new_decision_state = current.get("decision_state", "none")
    if old_decision_state != new_decision_state:
        severity = "important" if new_decision_state in {"testing", "accepted", "rejected"} else "watch"
        events.append(event(
            "DECISION_STATE_CHANGED",
            severity,
            f"Decision state changed: {old_decision_state} -> {new_decision_state}",
            old_decision_state,
            new_decision_state,
        ))

    if previous.get("invalidation") != current.get("invalidation"):
        events.append(event(
            "INVALIDATION_CHANGED",
            "info",
            "Invalidation condition changed with the scenario structure",
            previous.get("invalidation"),
            current.get("invalidation"),
        ))
    return events


def compare_branches(
    previous: dict[str, Any],
    current: dict[str, Any],
    primary_direction: str | None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    directions = sorted(set(previous) | set(current))
    for direction in directions:
        # Primary status changes are already emitted with higher priority.
        if direction == primary_direction:
            continue
        old_branch = previous.get(direction) or {}
        new_branch = current.get(direction) or {}
        old_status = old_branch.get("status", "silent")
        new_status = new_branch.get("status", "silent")
        if old_status != new_status:
            events.append(event(
                "BRANCH_STATUS_CHANGED",
                "watch",
                f"{direction.capitalize()} branch changed: {old_status} -> {new_status}",
                {"direction": direction, "status": old_status},
                {"direction": direction, "status": new_status},
            ))
    return events


def compare_snapshots(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    events = compare_primary(previous.get("primary") or {}, current.get("primary") or {})
    primary_direction = str((current.get("primary") or {}).get("direction") or "") or None
    events.extend(compare_branches(
        previous.get("branches") or {},
        current.get("branches") or {},
        primary_direction,
    ))
    old_lifecycle = previous.get("leg_lifecycle") if isinstance(previous.get("leg_lifecycle"), dict) else {}
    new_lifecycle = current.get("leg_lifecycle") if isinstance(current.get("leg_lifecycle"), dict) else {}
    if old_lifecycle and new_lifecycle:
        if old_lifecycle.get("counter") != new_lifecycle.get("counter") and new_lifecycle.get("counter"):
            events.append(event(
                "REACTION_LIFECYCLE_STARTED", "important", "New counter-reaction lifecycle started",
                old_lifecycle, new_lifecycle,
            ))
        if old_lifecycle.get("continuation") != new_lifecycle.get("continuation") and new_lifecycle.get("continuation"):
            events.append(event(
                "CONTINUATION_LIFECYCLE_STARTED", "important", "New post-pullback continuation lifecycle started",
                old_lifecycle, new_lifecycle,
            ))
    old_driver = previous.get("driver") or {}
    new_driver = current.get("driver") or {}
    old_available = bool(old_driver.get("available"))
    new_available = bool(new_driver.get("available"))
    same_event = (
        old_driver.get("title"), old_driver.get("event_time_utc")
    ) == (
        new_driver.get("title"), new_driver.get("event_time_utc")
    )
    if new_available and (not old_available or not same_event):
        event_type = "NEWS_RELEASED" if new_driver.get("status") == "released" else "NEWS_RISK_UPCOMING"
        severity = "important" if new_driver.get("importance") == "high" or event_type == "NEWS_RELEASED" else "watch"
        events.append(event(event_type, severity, f"News driver: {new_driver.get('title')}", old_driver, new_driver))
    elif new_available and same_event:
        if old_driver.get("actual") != new_driver.get("actual") or old_driver.get("status") != new_driver.get("status"):
            events.append(event("NEWS_RELEASED", "important", f"News released: {new_driver.get('title')}", old_driver, new_driver))
        elif old_driver.get("reaction_status") != new_driver.get("reaction_status"):
            reaction = new_driver.get("reaction_status")
            if reaction in {"confirmed", "contradicted"}:
                events.append(event(
                    "NEWS_REACTION_CONFIRMED" if reaction == "confirmed" else "NEWS_REACTION_CONTRADICTED",
                    "important",
                    f"News reaction {reaction}: {new_driver.get('title')}",
                    old_driver,
                    new_driver,
                ))
    old_ideas = previous.get("ideas")
    new_ideas = current.get("ideas")
    # Missing old ideas means schema migration: establish a baseline silently.
    if isinstance(old_ideas, dict) and isinstance(new_ideas, dict):
        for idea_id in sorted(set(old_ideas) | set(new_ideas)):
            before, after = old_ideas.get(idea_id) or {}, new_ideas.get(idea_id) or {}
            if before.get("state") != after.get("state"):
                new_state = after.get("state")
                severity = "important" if new_state in {"forming", "active"} else "watch"
                events.append(event(
                    "TRADE_IDEA_STATE_CHANGED", severity,
                    f"Trade idea {idea_id}: {before.get('state', 'none')} -> {new_state}", before, after,
                ))
            elif before.get("objectives") != after.get("objectives") and after.get("state") in {"forming", "active"}:
                events.append(event(
                    "TRADE_IDEA_TARGETS_CHANGED", "info",
                    f"Trade idea objectives changed: {idea_id}", before.get("objectives"), after.get("objectives"),
                ))
    return events


def append_history(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def main() -> None:
    scenario = load_json(SCENARIO_FILE)
    current = scenario_snapshot(scenario)
    previous_payload = load_json(STATE_FILE, required=False)
    previous = previous_payload.get("snapshot") if isinstance(previous_payload.get("snapshot"), dict) else None
    now = datetime.now(timezone.utc).isoformat()

    initialized = previous is None
    events = [] if initialized else compare_snapshots(previous, current)
    alert_events = [item for item in events if item.get("severity") in ALERTABLE_SEVERITIES]
    output = {
        "schema_version": 1,
        "generated_at_utc": now,
        "symbol": SYMBOL,
        "initialized": initialized,
        "should_notify": bool(alert_events),
        "event_count": len(events),
        "alert_event_count": len(alert_events),
        "events": events,
        "current_snapshot": current,
    }

    atomic_write_json(EVENT_FILE, output)
    atomic_write_json(STATE_FILE, {"updated_at_utc": now, "snapshot": current})
    if events:
        append_history(HISTORY_FILE, output)

    if initialized:
        print("EVENT_MONITOR: INITIALIZED — baseline saved, no alert")
    elif not events:
        print("EVENT_MONITOR: NO_CHANGE")
    else:
        print(
            f"EVENT_MONITOR: CHANGED | events={len(events)} | "
            f"alert_events={len(alert_events)} | notify={bool(alert_events)}"
        )
        for item in events:
            print(f"  {item['severity'].upper():<9} | {item['type']:<28} | {item['text']}")
    print(f"EVENTS_SAVED: {EVENT_FILE}")
    print("EVENT_MONITOR_ENGINE: PASSED")


if __name__ == "__main__":
    main()
