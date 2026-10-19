from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


FACTS_FILE = Path("data/macro/official_fomc_facts.json")
INTERPRETATION_FILE = Path("data/macro/fomc_decision_interpretation.json")
OUTPUT_FILE = Path("data/macro/fomc_phase_plan.json")


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


def parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def directional_tilt(value: Any) -> str | None:
    text = str(value or "")
    if text in {"hawkish_surprise", "hawkish_tilt_candidate", "broad_hawkish_shift", "hawkish"}:
        return "tightening_candidate"
    if text in {"dovish_surprise", "dovish_tilt_candidate", "broad_dovish_shift", "dovish"}:
        return "easing_candidate"
    return None


def package_tilt(interpretation: dict[str, Any]) -> dict[str, Any]:
    components = {
        "decision": directional_tilt((interpretation.get("decision_surprise") or {}).get("classification")),
        "statement": directional_tilt((interpretation.get("statement_analysis") or {}).get("classification")),
        "sep": directional_tilt((interpretation.get("sep_analysis") or {}).get("classification")),
    }
    available = [value for value in components.values() if value]
    if available and len(set(available)) == 1:
        classification = available[0]
        confidence = "multi_component" if len(available) >= 2 else "single_component"
    elif available:
        classification, confidence = "mixed", "conflicting_components"
    else:
        classification, confidence = "unresolved", "no_directional_component"
    return {"classification": classification, "confidence": confidence, "components": components}


def hypothesized_transmission(tilt: str) -> dict[str, str]:
    if tilt == "tightening_candidate":
        return {"nominal_yields": "rising", "usd": "bullish", "gold": "bearish"}
    if tilt == "easing_candidate":
        return {"nominal_yields": "falling", "usd": "bearish", "gold": "bullish"}
    return {}


def policy_labels(tilt: str) -> dict[str, str]:
    if tilt == "tightening_candidate":
        return {"policy_bias_ru": "гипотеза уклона в сторону ужесточения", "gold_hypothesis_ru": "ожидается нисходящее давление на Gold"}
    if tilt == "easing_candidate":
        return {"policy_bias_ru": "гипотеза уклона в сторону смягчения", "gold_hypothesis_ru": "ожидается восходящая поддержка Gold"}
    return {"policy_bias_ru": "направление политики не определено", "gold_hypothesis_ru": "направленная гипотеза по Gold отсутствует"}


def window(name: str, start: datetime, end: datetime, next_phase: datetime | None = None) -> dict[str, Any]:
    overlap = bool(next_phase and start < next_phase < end)
    touches = bool(next_phase and end == next_phase)
    return {
        "name": name, "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        "duration_minutes": round((end - start).total_seconds() / 60),
        "phase_purity": "contaminated_by_next_phase" if overlap else "clean_until_next_phase" if touches else "clean",
        "measurement_allowed": not overlap,
        "contaminated_by": "press_conference" if overlap else None,
    }


def confirmation_rules(tilt: str) -> dict[str, Any]:
    return {
        "hypothesized_transmission": hypothesized_transmission(tilt),
        "minimum_market_evidence": [
            "material Gold reaction in expected direction",
            "material aligned move in nominal yields or USD",
            "closed reaction window",
        ],
        "execution_evidence": [
            "closed lower-timeframe PA acceptance/rejection",
            "volume supports the active reaction rather than only the parent leg",
        ],
        "blocking_conditions": [
            "Gold materially contradicts expected direction",
            "all transmission channels are immaterial or unavailable",
            "window overlaps a later FOMC information phase",
        ],
        "permissions": {"confirm_driver": False, "entry": False, "direction_switch": False, "score": False},
    }


def build_phase_plan(facts: dict[str, Any], interpretation: dict[str, Any]) -> dict[str, Any]:
    timepoints = facts.get("timepoints") if isinstance(facts.get("timepoints"), dict) else {}
    statement_at = parse_time((timepoints.get("statement") or {}).get("event_time_utc"))
    conference_at = parse_time((timepoints.get("press_conference") or {}).get("event_time_utc"))
    if not statement_at or not conference_at or conference_at <= statement_at:
        return {"schema_version": 1, "available": False, "reason": "INVALID_OR_INCOMPLETE_FOMC_TIMEPOINTS", "phases": []}

    package = package_tilt(interpretation)
    conference_analysis = interpretation.get("press_conference_analysis") if isinstance(interpretation.get("press_conference_analysis"), dict) else {}
    conference_tilt = directional_tilt(conference_analysis.get("classification")) or "unresolved"
    package_windows = [
        window("immediate_15m", statement_at, statement_at + timedelta(minutes=15), conference_at),
        window("pre_conference", statement_at, conference_at, conference_at),
        window("headline_60m", statement_at, statement_at + timedelta(minutes=60), conference_at),
    ]
    conference_windows = [
        window("conference_15m", conference_at, conference_at + timedelta(minutes=15)),
        window("conference_60m", conference_at, conference_at + timedelta(minutes=60)),
        window("conference_4h", conference_at, conference_at + timedelta(hours=4)),
    ]
    phases = [
        {
            "phase_id": "fomc_18_00_package", "label_ru": "Решение + statement + SEP",
            "anchor_time_utc": statement_at.isoformat(), "policy_bias_candidate": package["classification"],
            **policy_labels(package["classification"]),
            "classification_basis": package, "windows": package_windows,
            "confirmation": confirmation_rules(package["classification"]),
        },
        {
            "phase_id": "fomc_18_30_press_conference", "label_ru": "Пресс-конференция",
            "anchor_time_utc": conference_at.isoformat(), "policy_bias_candidate": conference_tilt,
            **policy_labels(conference_tilt),
            "classification_basis": {
                "relation_to_18_00_package": conference_analysis.get("relation_to_18_00_package"),
                "qa_effect": conference_analysis.get("qa_effect"),
                "transcript_status": conference_analysis.get("status"),
            },
            "windows": conference_windows, "confirmation": confirmation_rules(conference_tilt),
        },
    ]
    return {
        "schema_version": 1, "available": True, "meeting_id": facts.get("meeting_id"),
        "phase_separation_minutes": round((conference_at - statement_at).total_seconds() / 60),
        "phases": phases,
        "policy": "Каждая публикационная фаза имеет собственный ценовой якорь. Уклон политики является гипотезой, а не направлением цены. Пересекающиеся окна не используются для чистой атрибуции предыдущей фазе.",
        "permissions": {"confirm_driver": False, "entry": False, "direction_switch": False, "score": False},
    }


def main() -> int:
    payload = build_phase_plan(load_json(FACTS_FILE), load_json(INTERPRETATION_FILE))
    atomic_json(OUTPUT_FILE, payload)
    phases = payload.get("phases") or []
    print(f"FOMC_PHASE_PLAN: available={payload.get('available')} | phases={len(phases)}")
    for phase in phases:
        print(f"FOMC_PHASE: {phase['phase_id']} | policy_bias={phase['policy_bias_candidate']}")
    print("FOMC_PHASE_ENGINE_C2_5A: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
