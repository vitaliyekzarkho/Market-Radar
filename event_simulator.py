from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import event_monitor


OUTPUT_FILE = Path("data") / "event_simulation" / "report.json"


def branch(
    direction: str = "bullish",
    status: str = "building",
    score: int = 68,
) -> dict[str, Any]:
    return {
        "direction": direction,
        "status": status,
        "score": score,
        "point_b": {"name": "PWH", "price": 4442.98},
        "decision_zone": {"name": "H4 bearish FVG", "low": 4400.25, "high": 4405.73},
        "decision_state": "approaching",
        "pullback_zone": {"name": "H1 bullish FVG", "low": 4325.06, "high": 4343.68},
        "confirmation": "IF H1 accepts above 4405.73, THEN bullish continuation remains active",
        "invalidation": "IF H1 accepts below 4325.06, THEN downgrade bullish",
    }


def snapshot(primary: dict[str, Any] | None = None) -> dict[str, Any]:
    primary = deepcopy(primary or branch())
    alternative_direction = "bearish" if primary.get("direction") == "bullish" else "bullish"
    alternative = branch(alternative_direction, "observe", 38)
    return {
        "symbol": "GOLD.pro",
        "current_price": 4378.25,
        "global_bias": "bullish",
        "primary": primary,
        "branches": {
            str(primary.get("direction")): deepcopy(primary),
            alternative_direction: alternative,
        },
        # Deliberately outside EventMonitor's current snapshot schema.
        "order_flow_context": {
            "delivery_status": "ALIGNED_BULLISH",
            "structure_status": "REVERSAL_BUILDING",
        },
    }


def changed(base: dict[str, Any], mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    value = deepcopy(base)
    mutator(value)
    direction = str((value.get("primary") or {}).get("direction") or "")
    if direction:
        value.setdefault("branches", {})[direction] = deepcopy(value["primary"])
    return value


def event_types(events: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("type")) for item in events]


def simulate_case(
    name: str,
    previous: dict[str, Any],
    current: dict[str, Any],
    expected_types: set[str],
    expected_notify: bool,
    note: str,
) -> dict[str, Any]:
    events = event_monitor.compare_snapshots(previous, current)
    types = set(event_types(events))
    alert_events = [
        item for item in events
        if item.get("severity") in event_monitor.ALERTABLE_SEVERITIES
    ]
    should_notify = bool(alert_events)
    passed = expected_types <= types and should_notify == expected_notify
    return {
        "name": name,
        "passed": passed,
        "expected_event_types": sorted(expected_types),
        "actual_event_types": event_types(events),
        "expected_notify": expected_notify,
        "actual_notify": should_notify,
        "events": events,
        "note": note,
    }


def build_cases() -> list[dict[str, Any]]:
    base = snapshot()
    cases: list[dict[str, Any]] = []

    cases.append(simulate_case(
        "unchanged",
        base,
        deepcopy(base),
        set(),
        False,
        "An identical cycle must stay silent.",
    ))
    cases.append(simulate_case(
        "small_score_change",
        base,
        changed(base, lambda value: value["primary"].update(score=73)),
        set(),
        False,
        "A score move below the material threshold must not alert.",
    ))
    cases.append(simulate_case(
        "material_score_change",
        base,
        changed(base, lambda value: value["primary"].update(score=80)),
        {"PRIMARY_SCORE_SHIFT"},
        True,
        "A material score move is currently alertable.",
    ))
    cases.append(simulate_case(
        "observe_to_building",
        snapshot(branch(status="observe", score=50)),
        base,
        {"PRIMARY_STATUS_CHANGED"},
        True,
        "Promotion into building is an important event.",
    ))
    cases.append(simulate_case(
        "direction_flip",
        base,
        snapshot(branch(direction="bearish", status="building", score=66)),
        {"PRIMARY_DIRECTION_CHANGED"},
        True,
        "A primary direction flip must alert.",
    ))
    cases.append(simulate_case(
        "decision_zone_testing",
        base,
        changed(base, lambda value: value["primary"].update(decision_state="testing")),
        {"DECISION_STATE_CHANGED"},
        True,
        "Entering a decision zone is important.",
    ))
    cases.append(simulate_case(
        "point_b_changed",
        base,
        changed(base, lambda value: value["primary"].update(point_b={"name": "PMH", "price": 4697.11})),
        {"POINT_B_CHANGED"},
        True,
        "A changed structural objective must alert.",
    ))
    cases.append(simulate_case(
        "pullback_zone_changed",
        base,
        changed(base, lambda value: value["primary"].update(
            pullback_zone={"name": "H1 bullish FVG", "low": 4358.10, "high": 4381.69}
        )),
        {"PULLBACK_ZONE_CHANGED"},
        True,
        "A changed pullback plan is currently alertable.",
    ))
    cases.append(simulate_case(
        "invalidation_text_only",
        base,
        changed(base, lambda value: value["primary"].update(
            invalidation="IF H1 accepts below 4305.57, THEN downgrade bullish"
        )),
        {"INVALIDATION_CHANGED"},
        False,
        "Invalidation wording alone is recorded as info, not sent.",
    ))
    cases.append(simulate_case(
        "alternative_branch_promoted",
        base,
        changed(base, lambda value: value["branches"]["bearish"].update(status="building")),
        {"BRANCH_STATUS_CHANGED"},
        True,
        "An alternative branch promotion is currently alertable.",
    ))

    # Current reaction is not part of EventMonitor's snapshot yet. This case
    # documents that deliberate behaviour rather than silently assuming it.
    reaction_before = deepcopy(base)
    reaction_after = deepcopy(base)
    reaction_before["primary"]["current_reaction_zone"] = None
    reaction_after["primary"]["current_reaction_zone"] = {
        "name": "H1 bullish FVG", "low": 4358.10, "high": 4381.69, "h1_state": "testing"
    }
    cases.append(simulate_case(
        "current_reaction_changed",
        reaction_before,
        reaction_after,
        set(),
        False,
        "Current reaction changes are descriptive and do not alert yet.",
    ))

    context_after = deepcopy(base)
    context_after["order_flow_context"] = {
        "delivery_status": "ALIGNED_BEARISH",
        "structure_status": "REVERSAL_BUILDING",
    }
    cases.append(simulate_case(
        "structure_context_changed",
        base,
        context_after,
        set(),
        False,
        "Order-flow context is not an independent alert trigger yet.",
    ))

    silent = deepcopy(base)
    silent["primary"] = {}
    cases.append(simulate_case(
        "building_to_silent",
        base,
        silent,
        {"PRIMARY_DIRECTION_CHANGED", "PRIMARY_STATUS_CHANGED"},
        True,
        "Current policy alerts when a published scenario disappears.",
    ))
    cases.append(simulate_case(
        "silent_to_building_flap",
        silent,
        base,
        {"PRIMARY_DIRECTION_CHANGED", "PRIMARY_STATUS_CHANGED"},
        True,
        "Current policy alerts again immediately if the scenario returns; no cooldown exists.",
    ))
    return cases


def main() -> int:
    cases = build_cases()
    passed = sum(bool(item["passed"]) for item in cases)
    alerting = sum(bool(item["actual_notify"]) for item in cases)
    report = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "production_code_modified": False,
        "case_count": len(cases),
        "passed": passed,
        "failed": len(cases) - passed,
        "alerting_cases": alerting,
        "cases": cases,
        "observations": {
            "cooldown_present": False,
            "status_flapping_can_repeat_alerts": True,
            "current_reaction_alertable": False,
            "structure_context_alertable": False,
        },
    }
    event_monitor.atomic_write_json(OUTPUT_FILE, report)

    print("EVENT_SIMULATOR: CURRENT POLICY")
    for index, item in enumerate(cases, start=1):
        state = "PASS" if item["passed"] else "FAIL"
        types = ",".join(item["actual_event_types"]) or "none"
        print(
            f"{index:>2}. {state:<4} | {item['name']:<29} | "
            f"notify={str(item['actual_notify']):<5} | events={types}"
        )
    print(
        f"EVENT_SIMULATOR: {'PASSED' if passed == len(cases) else 'FAILED'} | "
        f"cases={len(cases)} | alerting={alerting}"
    )
    print(f"EVENT_SIMULATION_REPORT: {OUTPUT_FILE}")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
