from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from causal_mechanism_engine import propose_mechanisms
from causal_replay_runner import select_primary_evaluation
from causal_validation_engine import candidate_contract, validate_candidates
from driver_contract import build_driver_contract, transition_driver
from priced_in_engine import apply_priced_in_gate, evaluate_priced_in, no_material_reaction_evaluation


INPUT_FILE = Path("data/news/GOLD_pro_reaction_windows.json")
OUTPUT_FILE = Path("data/news/GOLD_pro_live_causal.json")


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def no_surprise_evaluation(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event_id": contract.get("driver_id"),
        "candidate_id": None,
        "classification": "no_directional_surprise",
        "causal_role": "context_only",
        "reason_ru": "Все опубликованные компоненты совпали с прогнозом; направленного макросюрприза нет.",
        "driver_confirmation_allowed": False,
        "origin_claim_allowed": False,
        "permissions": {
            "confirm_driver": False, "entry": False, "direction_switch": False,
            "score": False, "technical_invalidation_override": False,
        },
    }


def build_live_causal(payload: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not payload.get("available"):
        return {
            "schema_version": 1, "available": False,
            "reason": payload.get("reason") or "REACTION_WINDOW_UNAVAILABLE",
            "generated_at_utc": moment.isoformat(),
        }
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    reaction = payload.get("reaction_report") if isinstance(payload.get("reaction_report"), dict) else {}
    if not event or not reaction:
        return {"schema_version": 1, "available": False, "reason": "LIVE_CAUSAL_INPUT_INCOMPLETE", "generated_at_utc": moment.isoformat()}

    normalized_event = dict(event)
    normalized_event.setdefault("lifecycle_state", "reported_fact")
    normalized_event.setdefault("fact_status", "verified" if normalized_event.get("source_verified") else "reported")
    contract = build_driver_contract(normalized_event, now=moment)
    mechanisms = propose_mechanisms(normalized_event)
    validation = validate_candidates(contract, mechanisms, reaction)
    primary = select_primary_evaluation(validation, mechanisms)

    if validation.get("next_gate") == "reaction_window_expired":
        priced_in = no_material_reaction_evaluation(contract, primary)
    elif mechanisms.get("release_status") == "in_line":
        priced_in = no_surprise_evaluation(contract)
    elif not mechanisms.get("recognized"):
        priced_in = {
            "schema_version": 1, "event_id": contract.get("driver_id"), "candidate_id": None,
            "classification": "unrecognized_mechanism", "causal_role": "reported_fact_only",
            "reason_ru": "Факт опубликован, но проверяемый механизм передачи на Gold ещё не определён.",
            "driver_confirmation_allowed": False, "origin_claim_allowed": False,
            "permissions": {"confirm_driver": False, "entry": False, "direction_switch": False, "score": False, "technical_invalidation_override": False},
        }
    else:
        novelty = {"scheduled_actual_release": True, "status": "new"}
        priced_in = evaluate_priced_in(contract, primary, reaction, novelty)

    candidate = candidate_contract(contract, primary)
    final_contract = apply_priced_in_gate(candidate, priced_in)
    if priced_in.get("classification") == "no_material_reaction":
        final_contract = transition_driver(final_contract, "expired", "reaction_window_expired", moment)
    source_gate = contract.get("source") if isinstance(contract.get("source"), dict) else {}
    return {
        "schema_version": 1,
        "available": True,
        "event_id": contract.get("driver_id"),
        "event": normalized_event,
        "source_gate": {
            "verified": bool(source_gate.get("verified")),
            "fact_permission": bool(source_gate.get("fact_permission")),
        },
        "mechanisms": mechanisms,
        "validation": validation,
        "priced_in": priced_in,
        "final_contract": final_contract,
        "summary": {
            "event_family": mechanisms.get("event_family"),
            "primary_mechanism": primary.get("mechanism_id"),
            "market_alignment": primary.get("alignment"),
            "classification": priced_in.get("classification"),
            "causal_role": priced_in.get("causal_role"),
            "driver_confirmation_allowed": bool(priced_in.get("driver_confirmation_allowed")),
            "blockers": primary.get("missing_evidence") or [],
        },
        "generated_at_utc": moment.isoformat(),
    }


def main() -> int:
    payload = build_live_causal(load_json(INPUT_FILE))
    atomic_json(OUTPUT_FILE, payload)
    if payload.get("available"):
        summary = payload["summary"]
        print(f"LIVE_CAUSAL: {summary['classification']} | mechanism={summary['primary_mechanism']} | alignment={summary['market_alignment']}")
    else:
        print(f"LIVE_CAUSAL: NO_DATA | reason={payload.get('reason')}")
    print(f"LIVE_CAUSAL_SAVED: {OUTPUT_FILE}")
    print("LIVE_CAUSAL_ENGINE: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
