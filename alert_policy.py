from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


SYMBOL = "GOLD.pro"
SYMBOL_FILE = SYMBOL.replace(".", "_")
DATA_DIR = Path("data")

SCENARIO_FILE = DATA_DIR / "scenario_states" / f"{SYMBOL_FILE}.json"
EVENT_FILE = DATA_DIR / "events" / f"{SYMBOL_FILE}.json"
STATE_FILE = DATA_DIR / "runtime" / f"{SYMBOL_FILE}_alert_policy_state.json"
OUTPUT_FILE = DATA_DIR / "alert_policy" / f"{SYMBOL_FILE}.json"

ACTIVE_STATUSES = {"observe", "building", "confirmed"}
STATUS_RANK = {"silent": 0, "observe": 1, "building": 2, "confirmed": 3}
DECISION_ALERT_STATES = {"testing", "accepted", "rejected"}
REACTION_ALERT_STATES = {
    "testing", "swept", "reaction_building", "reaction_confirmed",
    "accepted_above", "accepted_below", "reclaimed_above", "reclaimed_below",
}
ROUTE_ALERT_COOLDOWN = timedelta(minutes=15)
SCENARIO_ALERT_COOLDOWN = timedelta(minutes=60)
CLUSTER_GAP_PERCENT = 0.12
NON_ACTIONABLE_OPPORTUNITIES = {"late", "missed"}
OPPORTUNITY_LIFECYCLES = {
    "early_attention": "watch",
    "setup_forming": "forming",
    "active": "ready",
}
OPPORTUNITY_LIFECYCLE_RANK = {
    "none": 0,
    "watch": 1,
    "forming": 2,
    "ready": 3,
    "manage": 4,
    "confirmed": 5,
    "non_actionable": -1,
}


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


def zone_snapshot(zone: Any, include_state: bool = False) -> dict[str, Any] | None:
    if not isinstance(zone, dict) or not zone:
        return None
    result = {
        "name": zone.get("name"),
        "low": number(zone.get("low")),
        "high": number(zone.get("high")),
    }
    if include_state:
        result["state"] = zone.get("h1_state") or zone.get("state") or "none"
    return result


def target_snapshot(target: Any) -> dict[str, Any] | None:
    if not isinstance(target, dict) or not target:
        return None
    return {"name": target.get("name"), "price": number(target.get("price"))}


def alert_lifecycle_key(direction: Any, point_b: Any) -> str:
    """Stable P0 alert identity for one structural route.

    P1 will replace this coarse route identity with an immutable reaction/leg
    identity tied to causal Point A. For P0 it intentionally ignores price,
    PA labels, local targets, checkpoints and regenerated route IDs.
    """
    target = point_b if isinstance(point_b, dict) else {}
    material = {
        "direction": direction,
        "point_b_name": target.get("name"),
        "point_b_price": number(target.get("price")),
    }
    encoded = json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def canonical_route_clusters(checkpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse nearby route objects into stable, order-independent clusters."""
    areas: list[dict[str, Any]] = []
    for item in checkpoints:
        if not isinstance(item, dict):
            continue
        low = number(item.get("low"))
        high = number(item.get("high"))
        price = number(item.get("price"))
        if low is None:
            low = price
        if high is None:
            high = price
        if low is None or high is None:
            continue
        low, high = sorted((low, high))
        areas.append({
            "low": low, "high": high,
            "members": {str(item.get("name") or item.get("kind") or "level")},
        })
    areas.sort(key=lambda item: (item["low"], item["high"]))
    clusters: list[dict[str, Any]] = []
    for area in areas:
        if not clusters:
            clusters.append(area)
            continue
        previous = clusters[-1]
        reference = max(abs(previous["high"]), abs(area["low"]), 1.0)
        allowed_gap = reference * CLUSTER_GAP_PERCENT / 100.0
        if area["low"] <= previous["high"] + allowed_gap:
            previous["low"] = min(previous["low"], area["low"])
            previous["high"] = max(previous["high"], area["high"])
            previous["members"].update(area["members"])
        else:
            clusters.append(area)
    return [
        {
            "low": round(item["low"], 2),
            "high": round(item["high"], 2),
            "members": sorted(item["members"]),
        }
        for item in clusters
    ]


def route_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    target = snapshot.get("point_b") or {}
    clusters = snapshot.get("route_clusters") or []
    direction = snapshot.get("direction")
    first_cluster = None
    if clusters:
        first_cluster = clusters[-1] if direction == "bearish" else clusters[0]
    return {
        "direction": direction,
        "target": (target.get("name"), target.get("price")),
        "first_cluster": first_cluster,
    }


def same_route_context(before: Any, after: dict[str, Any]) -> bool:
    if not isinstance(before, dict):
        return False
    old_target = tuple(before.get("target") or ())
    new_target = tuple(after.get("target") or ())
    if before.get("direction") != after.get("direction") or old_target != new_target:
        return False
    old_cluster = before.get("first_cluster")
    new_cluster = after.get("first_cluster")
    if not isinstance(old_cluster, dict) or not isinstance(new_cluster, dict):
        return old_cluster == new_cluster
    old_low, old_high = number(old_cluster.get("low")), number(old_cluster.get("high"))
    new_low, new_high = number(new_cluster.get("low")), number(new_cluster.get("high"))
    if None in {old_low, old_high, new_low, new_high}:
        return False
    reference = max(abs(old_high), abs(new_high), 1.0)
    allowed_gap = reference * CLUSTER_GAP_PERCENT / 100.0
    return bool(new_low <= old_high + allowed_gap and old_low <= new_high + allowed_gap)


def parse_utc(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def opportunity_lifecycle(value: Any) -> str:
    """Return the stable, user-actionable phase of an opportunity.

    ``late`` and ``missed`` remain available in the raw snapshot for audit and
    statistics, but deliberately collapse into one internal non-alerting phase.
    """
    state = str(value or "observe").strip().lower()
    if state in NON_ACTIONABLE_OPPORTUNITIES:
        return "non_actionable"
    return OPPORTUNITY_LIFECYCLES.get(state, "none")


def policy_snapshot(scenario: dict[str, Any]) -> dict[str, Any]:
    primary = scenario.get("primary_scenario")
    primary = primary if isinstance(primary, dict) else {}
    status = str(primary.get("status") or "silent").lower()
    driver = scenario.get("driver_context") if isinstance(scenario.get("driver_context"), dict) else {}
    checkpoints = primary.get("route_checkpoints") if isinstance(primary.get("route_checkpoints"), list) else []
    route_items = [
        {
            "name": item.get("name"), "kind": item.get("kind"),
            "low": number(item.get("low")), "high": number(item.get("high")),
            "price": number(item.get("price")), "status": item.get("status"),
            "route_role": item.get("route_role"),
        }
        for item in checkpoints if isinstance(item, dict)
    ]
    market = scenario.get("market_state") if isinstance(scenario.get("market_state"), dict) else {}
    opportunity = market.get("opportunity") if isinstance(market.get("opportunity"), dict) else {}
    pa = market.get("pa_state") if isinstance(market.get("pa_state"), dict) else {}
    location = market.get("current_location") if isinstance(market.get("current_location"), dict) else {}
    point_b = target_snapshot(primary.get("point_b"))
    counter_leg = market.get("active_counter_leg") if isinstance(market.get("active_counter_leg"), dict) else {}
    counter_candidate = market.get("counter_candidate") if isinstance(market.get("counter_candidate"), dict) else {}
    continuation_setup = market.get("continuation_setup") if isinstance(market.get("continuation_setup"), dict) else {}
    structural_leg = market.get("primary_structural_leg") if isinstance(market.get("primary_structural_leg"), dict) else {}
    opportunity_leg = counter_leg or counter_candidate or continuation_setup or structural_leg
    opportunity_leg_id = opportunity_leg.get("lifecycle_id")
    opportunity_direction = opportunity_leg.get("direction") or primary.get("direction")
    return {
        "direction": primary.get("direction"),
        "status": status,
        "active": status in ACTIVE_STATUSES,
        "score": int(primary.get("score") or 0),
        "point_b": point_b,
        "decision_zone": zone_snapshot(primary.get("next_decision_zone")),
        "decision_state": primary.get("next_decision_state") or "none",
        "current_reaction": zone_snapshot(primary.get("current_reaction_zone"), include_state=True),
        "pullback_zone": zone_snapshot(primary.get("pullback_zone")),
        "route_checkpoints": route_items,
        "route_clusters": canonical_route_clusters(route_items),
        "route_id": market.get("route_id"),
        "location_cluster_id": location.get("cluster_id"),
        "pa_stage": pa.get("stage"),
        # Preserve the engine's raw classification for diagnostics while the
        # alert policy works with a stable lifecycle classification.
        "opportunity_state": opportunity.get("state"),
        "opportunity_lifecycle": opportunity_lifecycle(opportunity.get("state")),
        "alert_lifecycle_key": opportunity_leg_id or alert_lifecycle_key(primary.get("direction"), point_b),
        "opportunity_leg_id": opportunity_leg_id,
        "opportunity_leg_role": opportunity_leg.get("leg_role"),
        "opportunity_direction": opportunity_direction,
        "driver": {
            "available": bool(driver.get("available")),
            "title": driver.get("title"),
            "status": driver.get("status"),
            "effect": driver.get("effect"),
            "reaction_status": driver.get("reaction_status"),
            "event_time_utc": driver.get("event_time_utc"),
            "actual": driver.get("actual"),
            "importance": driver.get("importance"),
        },
    }


def reaction_direction(snapshot: dict[str, Any]) -> str | None:
    """Direction of the counter-move, not the primary delivery.

    A reaction direction must come from an actual counter leg/candidate.  It
    must never be inferred merely by flipping the primary direction.
    """
    if snapshot.get("opportunity_leg_role") not in {"active_counter", "counter_candidate"}:
        return None
    explicit = snapshot.get("opportunity_direction")
    if explicit in {"bullish", "bearish"} and explicit != snapshot.get("direction"):
        return str(explicit)
    return None


def core_signature(snapshot: dict[str, Any]) -> tuple[Any, ...]:
    target = snapshot.get("point_b") or {}
    decision = snapshot.get("decision_zone") or {}
    return (
        snapshot.get("direction"),
        target.get("name"), target.get("price"),
        decision.get("name"), decision.get("low"), decision.get("high"),
    )


def event_types(event_payload: dict[str, Any]) -> set[str]:
    events = event_payload.get("events") or []
    if not isinstance(events, list):
        return set()
    return {
        str(item.get("type"))
        for item in events
        if isinstance(item, dict) and item.get("type")
    }


def event_items(event_payload: dict[str, Any], selected: set[str]) -> list[dict[str, Any]]:
    events = event_payload.get("events") or []
    if not isinstance(events, list):
        return []
    return [item for item in events if isinstance(item, dict) and item.get("type") in selected]


def fingerprint(notification_type: str, snapshot: dict[str, Any]) -> str:
    # Deliberately exclude current price, dynamic target lists, route roles and
    # local FVG/VI churn. They describe the same scenario, not a new event.
    material = {
        "type": notification_type,
        "core": core_signature(snapshot),
        "status": snapshot.get("status"),
    }
    if notification_type in {"EARLY_ATTENTION", "REACTION_EVENT"}:
        # PA labels, decision-state labels and locally regenerated route IDs
        # can oscillate intrabar while the tradable reaction is unchanged.
        material.update({
            "alert_lifecycle_key": snapshot.get("alert_lifecycle_key"),
            "opportunity_lifecycle": (
                None
                if snapshot.get("opportunity_lifecycle") == "non_actionable"
                else snapshot.get("opportunity_lifecycle")
            ),
            "reaction_direction": reaction_direction(snapshot),
        })
    else:
        material.update({
            "decision_state": snapshot.get("decision_state"),
            "route_id": snapshot.get("route_id"),
            "pa_stage": snapshot.get("pa_stage"),
        })
    encoded = json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def policy_decision(
    previous_state: dict[str, Any],
    scenario: dict[str, Any],
    event_payload: dict[str, Any],
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Collapse raw engine changes into at most one user-facing alert."""
    moment = now or datetime.now(timezone.utc)
    current = policy_snapshot(scenario)
    previous = previous_state.get("snapshot")
    previous = previous if isinstance(previous, dict) else None
    last_active = previous_state.get("last_active_snapshot")
    last_active = last_active if isinstance(last_active, dict) else None
    raw_types = event_types(event_payload)

    notification_type = "NONE"
    priority = "none"
    headline = "No material alert"
    reason_codes: list[str] = []
    included_types: set[str] = set()

    if previous is None:
        reason_codes = ["POLICY_INITIALIZED"]
    else:
        was_active = bool(previous.get("active"))
        is_active = bool(current.get("active"))

        if was_active and not is_active:
            notification_type = "SCENARIO_PAUSED"
            priority = "watch"
            headline = "Active scenario paused"
            reason_codes = ["ACTIVE_TO_SILENT"]
            included_types = raw_types
        elif not was_active and is_active:
            reference = last_active or previous
            if last_active and core_signature(reference) == core_signature(current):
                reason_codes = ["FLAP_RESUME_SUPPRESSED"]
            else:
                notification_type = "SCENARIO_RESUMED" if last_active else "NEW_SCENARIO"
                priority = "important"
                headline = "Scenario resumed with material changes" if last_active else "New active scenario"
                reason_codes = ["SILENT_TO_ACTIVE", "CORE_SCENARIO_CHANGED"]
                included_types = raw_types
        elif is_active:
            old_direction = previous.get("direction")
            new_direction = current.get("direction")
            old_rank = STATUS_RANK.get(str(previous.get("status") or "silent"), 0)
            new_rank = STATUS_RANK.get(str(current.get("status") or "silent"), 0)
            reaction_changed = previous.get("current_reaction") != current.get("current_reaction")
            reaction = current.get("current_reaction") or {}
            old_opportunity = previous.get("opportunity_lifecycle") or opportunity_lifecycle(
                previous.get("opportunity_state")
            )
            new_opportunity = current.get("opportunity_lifecycle") or opportunity_lifecycle(
                current.get("opportunity_state")
            )
            opportunity_changed = old_opportunity != new_opportunity
            opportunity_promoted = (
                new_opportunity not in {"none", "non_actionable"}
                and OPPORTUNITY_LIFECYCLE_RANK.get(new_opportunity, 0)
                > OPPORTUNITY_LIFECYCLE_RANK.get(old_opportunity, 0)
            )
            counter_opportunity = current.get("opportunity_leg_role") in {"active_counter", "counter_candidate"}

            if old_direction != new_direction:
                notification_type = "DIRECTION_CHANGED"
                priority = "critical"
                headline = f"Primary direction changed: {old_direction or 'none'} -> {new_direction or 'none'}"
                reason_codes = ["PRIMARY_DIRECTION_CHANGED"]
                included_types = raw_types
            elif new_rank > old_rank:
                notification_type = "STATUS_PROMOTED"
                priority = "important"
                headline = f"Scenario promoted: {previous.get('status')} -> {current.get('status')}"
                reason_codes = ["PRIMARY_STATUS_PROMOTED"]
                included_types = raw_types
            elif opportunity_promoted:
                notification_type = (
                    "EARLY_ATTENTION" if counter_opportunity and new_opportunity == "watch"
                    else "REACTION_EVENT" if counter_opportunity
                    else "SETUP_FORMING"
                )
                priority = "important" if new_opportunity in {"ready", "manage", "confirmed"} else "watch"
                headline = f"Opportunity lifecycle: {old_opportunity} -> {new_opportunity}"
                reason_codes = ["OPPORTUNITY_LIFECYCLE_PROMOTED"]
                included_types = {"TRADE_IDEA_STATE_CHANGED"} & raw_types
            elif new_opportunity == "non_actionable" and (
                opportunity_changed or "TRADE_IDEA_STATE_CHANGED" in raw_types
            ):
                reason_codes = ["NON_ACTIONABLE_OPPORTUNITY_SUPPRESSED"]
            elif (
                previous.get("decision_state") != current.get("decision_state")
                and current.get("decision_state") in DECISION_ALERT_STATES
            ):
                notification_type = "DECISION_EVENT"
                priority = "important"
                headline = f"Decision zone: {current.get('decision_state')}"
                reason_codes = ["DECISION_STATE_MATERIAL"]
                included_types = {"DECISION_STATE_CHANGED", "DECISION_ZONE_CHANGED"} & raw_types
            elif previous.get("point_b") != current.get("point_b"):
                notification_type = "TARGET_CHANGED"
                priority = "watch"
                headline = "Scenario objective changed"
                reason_codes = ["POINT_B_CHANGED"]
                included_types = {"POINT_B_CHANGED"} & raw_types
            elif "route_clusters" not in previous:
                reason_codes = ["ROUTE_CLUSTER_BASELINE_INITIALIZED"]
            elif previous.get("route_clusters") != current.get("route_clusters"):
                notification_type = "ROUTE_UPDATED"
                priority = "important"
                headline = "Route checkpoints changed"
                reason_codes = ["ROUTE_CHECKPOINTS_CHANGED"]
                included_types = {"ROUTE_CHECKPOINTS_CHANGED"} & raw_types
            elif counter_opportunity and reaction_changed and reaction.get("state") in REACTION_ALERT_STATES:
                notification_type = "REACTION_EVENT"
                priority = "important"
                headline = f"Reaction zone: {reaction.get('state')}"
                reason_codes = ["EXPECTED_REACTION_CHANGED"]
            else:
                reason_codes = ["CONTEXT_ONLY_OR_DUPLICATE"]

        news_released = {
            "NEWS_RELEASED", "NEWS_REACTION_CONFIRMED", "NEWS_REACTION_CONTRADICTED",
        } & raw_types
        news_upcoming = "NEWS_RISK_UPCOMING" in raw_types
        # Objective churn is diagnostic only. A trade idea may alert only when
        # its lifecycle state changes; changing a local FVG/VI target must not
        # manufacture a new reaction event.
        idea_events = {"TRADE_IDEA_STATE_CHANGED"} & raw_types
        if notification_type == "NONE" and news_released:
            notification_type = "NEWS_DRIVER"
            priority = "important"
            headline = "News driver updated"
            reason_codes = sorted(news_released)
            included_types = news_released
        elif notification_type == "NONE" and news_upcoming:
            notification_type = "NEWS_RISK"
            priority = "important" if (current.get("driver") or {}).get("importance") == "high" else "watch"
            headline = "Upcoming macro risk"
            reason_codes = ["NEWS_RISK_UPCOMING"]
            included_types = {"NEWS_RISK_UPCOMING"}
        elif notification_type == "NONE" and idea_events:
            # A raw idea-state mutation is not, by itself, a user event. The
            # stable opportunity lifecycle above is the only technical trigger.
            # This prevents local idea churn from bypassing P0 suppression.
            if "NON_ACTIONABLE_OPPORTUNITY_SUPPRESSED" not in reason_codes:
                reason_codes = ["TRADE_IDEA_CHURN_SUPPRESSED"]

    notify = notification_type != "NONE"
    current_route_context = route_context(current)
    if notify and notification_type == "ROUTE_UPDATED":
        last_route_context = previous_state.get("last_notified_route_context")
        last_route_at = parse_utc(previous_state.get("last_route_alert_at_utc"))
        if same_route_context(last_route_context, current_route_context):
            notify = False
            reason_codes.append("SAME_ROUTE_CLUSTER_SUPPRESSED")
        elif last_route_at and moment - last_route_at < ROUTE_ALERT_COOLDOWN:
            notify = False
            reason_codes.append("ROUTE_COOLDOWN_SUPPRESSED")
    current_fingerprint = fingerprint(notification_type, current) if notify else None
    if notify and current_fingerprint == previous_state.get("last_notified_fingerprint"):
        notify = False
        reason_codes.append("DUPLICATE_ALERT_SUPPRESSED")
    current_alert_key = current_fingerprint
    last_alert_at = parse_utc(previous_state.get("last_alert_at_utc"))
    if (
        notify
        and current_alert_key == previous_state.get("last_alert_key")
        and last_alert_at
        and moment - last_alert_at < SCENARIO_ALERT_COOLDOWN
    ):
        notify = False
        reason_codes.append("SAME_LIFECYCLE_COOLDOWN_SUPPRESSED")

    included = event_items(event_payload, included_types)
    suppressed = event_items(event_payload, raw_types - included_types)
    output = {
        "schema_version": 1,
        "generated_at_utc": moment.isoformat(),
        "symbol": scenario.get("symbol", SYMBOL),
        "should_notify": notify,
        "notification_type": notification_type,
        "priority": priority,
        "headline": headline,
        "reaction_direction": reaction_direction(current) if notification_type in {"EARLY_ATTENTION", "REACTION_EVENT"} else None,
        "reason_codes": reason_codes,
        "included_events": included,
        "suppressed_events": suppressed,
        "raw_event_count": len(event_payload.get("events") or []),
        "current_snapshot": current,
    }

    next_state = {
        "updated_at_utc": output["generated_at_utc"],
        "snapshot": current,
        "last_active_snapshot": current if current.get("active") else last_active,
        "last_notified_fingerprint": (
            current_fingerprint if notify else previous_state.get("last_notified_fingerprint")
        ),
        "last_alert_key": current_alert_key if notify else previous_state.get("last_alert_key"),
        "last_alert_at_utc": moment.isoformat() if notify else previous_state.get("last_alert_at_utc"),
        "last_notified_route_context": (
            current_route_context
            if notify and notification_type == "ROUTE_UPDATED"
            else previous_state.get("last_notified_route_context")
        ),
        "last_route_alert_at_utc": (
            moment.isoformat()
            if notify and notification_type == "ROUTE_UPDATED"
            else previous_state.get("last_route_alert_at_utc")
        ),
    }
    return output, next_state


def main() -> None:
    scenario = load_json(SCENARIO_FILE)
    event_payload = load_json(EVENT_FILE, required=False)
    previous_state = load_json(STATE_FILE, required=False)
    output, next_state = policy_decision(previous_state, scenario, event_payload)
    atomic_write_json(OUTPUT_FILE, output)
    atomic_write_json(STATE_FILE, next_state)

    if output["should_notify"]:
        print(
            f"ALERT_POLICY: NOTIFY | {output['priority'].upper()} | "
            f"{output['notification_type']} | {output['headline']}"
        )
    else:
        print(f"ALERT_POLICY: SUPPRESSED | reasons={','.join(output['reason_codes'])}")
    print(
        f"ALERT_POLICY_EVENTS: included={len(output['included_events'])} | "
        f"suppressed={len(output['suppressed_events'])}"
    )
    print(f"ALERT_POLICY_SAVED: {OUTPUT_FILE}")
    print("ALERT_POLICY_ENGINE: PASSED")


if __name__ == "__main__":
    main()
