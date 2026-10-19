from __future__ import annotations

import argparse
import copy
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from notification_builder import build_message
from telegram_notifier import CHAT_ID_ENV, TOKEN_ENV, build_inline_keyboard, send_telegram


SYMBOL = "GOLD.pro"
SYMBOL_FILE = SYMBOL.replace(".", "_")
SCENARIO_FILE = Path("data") / "scenario_states" / f"{SYMBOL_FILE}.json"


@dataclass(frozen=True)
class SimulationCase:
    key: str
    title: str
    notification_type: str
    priority: str
    headline: str
    status: str | None = None
    direction: str | None = None
    decision_state: str | None = None
    reaction_state: str | None = None
    target_name: str | None = None
    target_price: float | None = None


CASES = (
    SimulationCase(
        "new", "New scenario", "NEW_SCENARIO", "important",
        "New bullish scenario is active", status="observe",
    ),
    SimulationCase(
        "promoted", "Observe to building", "STATUS_PROMOTED", "important",
        "Scenario promoted: observe -> building", status="building",
    ),
    SimulationCase(
        "decision", "Decision zone test", "DECISION_EVENT", "important",
        "Price is testing the H4 decision zone", decision_state="testing",
    ),
    SimulationCase(
        "reaction", "Pullback reaction", "REACTION_EVENT", "important",
        "Reaction detected inside the expected H1 zone", reaction_state="reaction_building",
    ),
    SimulationCase(
        "direction", "Direction change", "DIRECTION_CHANGED", "critical",
        "Primary direction changed: bullish -> bearish", direction="bearish",
        target_name="PDL", target_price=4257.60,
    ),
    SimulationCase(
        "target", "Target change", "TARGET_CHANGED", "watch",
        "Scenario objective changed", target_name="PMH", target_price=4697.11,
    ),
    SimulationCase(
        "invalidated", "Scenario invalidated", "SCENARIO_INVALIDATED", "critical",
        "Bullish scenario invalidated by H1 acceptance below its boundary",
    ),
    SimulationCase(
        "paused", "Scenario paused", "SCENARIO_PAUSED", "watch",
        "Active scenario paused; waiting for a new valid branch", status="silent",
    ),
)


def load_scenario(path: Path = SCENARIO_FILE) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing scenario file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict) or not isinstance(value.get("primary_scenario"), dict):
        raise ValueError(f"Invalid scenario payload: {path}")
    return value


def simulated_scenario(base: dict[str, Any], case: SimulationCase) -> dict[str, Any]:
    scenario = copy.deepcopy(base)
    primary = scenario["primary_scenario"]
    if case.status is not None:
        primary["status"] = case.status
    if case.direction is not None:
        primary["direction"] = case.direction
        if case.direction == "bearish":
            primary["next_decision_zone"] = {
                "name": "H4 bullish FVG", "low": 4335.43, "high": 4351.37,
            }
            primary["current_reaction_zone"] = None
            primary["pullback_zone"] = None
            conditions = primary.get("conditions")
            if isinstance(conditions, dict):
                conditions["confirmation"] = (
                    "IF H1 accepts below 4335.43, THEN continuation toward PDL 4257.60 remains active"
                )
                conditions["invalidation"] = (
                    "IF H1 accepts above 4351.37, THEN downgrade the bearish branch"
                )
    if case.decision_state is not None:
        primary["next_decision_state"] = case.decision_state
        decision = primary.get("next_decision_zone")
        if case.key == "decision" and isinstance(decision, dict):
            low = float(decision.get("low") or 0)
            high = float(decision.get("high") or 0)
            scenario["current_price"] = round((low + high) / 2, 2)
    if case.reaction_state is not None:
        reaction = primary.get("current_reaction_zone")
        if not isinstance(reaction, dict):
            reaction = {"name": "H1 reaction zone", "low": 0, "high": 0}
            primary["current_reaction_zone"] = reaction
        reaction["h1_state"] = case.reaction_state
    if case.target_name is not None and case.target_price is not None:
        primary["point_b"] = {"name": case.target_name, "price": case.target_price}
        conditions = primary.get("conditions")
        if isinstance(conditions, dict) and case.key == "target":
            conditions["confirmation"] = (
                f"IF H1 accepts above 4405.73, THEN continuation toward "
                f"{case.target_name} {case.target_price:.2f} remains active"
            )
    if case.key == "invalidated":
        primary["status"] = "silent"
    if case.key == "paused":
        primary["score"] = 0
    return scenario


def policy_payload(case: SimulationCase) -> dict[str, Any]:
    return {
        "should_notify": True,
        "notification_type": case.notification_type,
        "priority": case.priority,
        "headline": case.headline,
        "included_events": [],
    }


def build_cases(base: dict[str, Any]) -> list[tuple[SimulationCase, str]]:
    rendered: list[tuple[SimulationCase, str]] = []
    for case in CASES:
        scenario = simulated_scenario(base, case)
        message = build_message(scenario, policy_payload(case), preview=False)
        rendered.append((case, message))
    return rendered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview MarketRadar Telegram notification architecture.")
    parser.add_argument("--send", action="store_true", help="Send every simulated case to the configured bot.")
    parser.add_argument("--case", choices=[item.key for item in CASES], help="Preview only one case.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base = load_scenario()
    rendered = build_cases(base)
    if args.case:
        rendered = [item for item in rendered if item[0].key == args.case]

    token = os.environ.get(TOKEN_ENV)
    chat_id = os.environ.get(CHAT_ID_ENV)
    if args.send and (not token or not chat_id):
        print("NOTIFICATION_SIMULATOR: FAILED — Telegram credentials are not available")
        return 1

    total = len(rendered)
    for index, (case, message) in enumerate(rendered, start=1):
        framed = (
            f"[SIMULATION {index}/{total}] {case.title}\n"
            f"Priority: {case.priority.upper()}\n\n{message}"
        )
        print("\n" + "=" * 72)
        print(framed)
        if args.send:
            result = send_telegram(
                str(token),
                str(chat_id),
                framed,
                reply_markup=build_inline_keyboard({"symbol": base.get("symbol", SYMBOL)}),
            )
            print(f"SENT: {case.key} | message_id={result.get('message_id')}")
            if index < total:
                time.sleep(0.4)

    mode = "SENT" if args.send else "PREVIEW"
    print(f"\nNOTIFICATION_SIMULATOR: {mode} | cases={total}")
    print("PRODUCTION_STATE: UNCHANGED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
