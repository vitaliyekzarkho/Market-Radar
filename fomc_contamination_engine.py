from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fomc_phase_engine import parse_time


PLAN_FILE = Path("data/macro/fomc_phase_plan.json")
REACTION_FILE = Path("data/macro/fomc_market_reaction.json")
CALENDAR_FILE = Path("data/news/verified_events.json")
OFFICIAL_RELEASES_FILE = Path("data/macro/official_releases.json")
HISTORICAL_CALENDAR_FILE = Path("data/macro/fomc_historical_calendar.json")
OUTPUT_FILE = Path("data/macro/fomc_contamination.json")

FOMC_TERMS = ("fomc", "federal reserve", "economic projections", "powell press conference")
SYSTEMIC_TERMS = (
    "treasury", "yield", "bond", "iran", "hormuz", "war", "ceasefire", "sanction",
    "oil", "brent", "trump", "cpi", "ppi", "payroll", "employment", "gdp",
)


def load_json(path: Path) -> dict[str, Any]:
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


def event_time(event: dict[str, Any]) -> datetime | None:
    return parse_time(event.get("published_at_utc") or event.get("event_time_utc") or event.get("time_utc"))


def normalize_events(calendar: dict[str, Any], official: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    rows.extend(("calendar", item) for item in calendar.get("events") or [] if isinstance(item, dict))
    rows.extend(("official_release", item) for item in official.get("recent_releases") or [] if isinstance(item, dict))
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source_kind, item in rows:
        timestamp = event_time(item)
        title = str(item.get("title") or "").strip()
        if not timestamp or not title:
            continue
        identity = (title.casefold(), timestamp.isoformat())
        if identity in seen:
            continue
        seen.add(identity)
        currencies = [str(value).upper() for value in item.get("currencies") or []]
        verified = bool(item.get("verified_primary_source") or item.get("authoritative_fact"))
        normalized.append({
            "title": title, "time_utc": timestamp.isoformat(), "importance": str(item.get("importance") or "unknown").lower(),
            "currencies": currencies, "status": item.get("status"), "source": item.get("source"),
            "source_url": item.get("source_url"), "source_kind": source_kind, "verified_primary_source": verified,
        })
    return sorted(normalized, key=lambda item: item["time_utc"])


def coverage_contract(calendar: dict[str, Any], supplied: dict[str, Any] | None = None) -> dict[str, Any]:
    """Legacy caches do not prove that a historical day was completely covered."""
    scheduled = dict(supplied) if supplied and supplied.get("status") == "complete" and parse_time(supplied.get("start_utc")) and parse_time(supplied.get("end_utc")) else {
        "status": "unknown", "start_utc": None, "end_utc": None,
        "reason": "LEGACY_CALENDAR_CACHE_HAS_NO_EXPLICIT_REQUESTED_RANGE",
        "provider": calendar.get("provider"), "fetched_at_utc": calendar.get("fetched_at_utc"),
    }
    return {
        "status": "incomplete" if scheduled.get("status") == "complete" else "unknown",
        "domains": {
            "scheduled_macro": scheduled,
            "unscheduled_news": {"status": "unknown", "start_utc": None, "end_utc": None, "reason": "HISTORICAL_HEADLINE_COVERAGE_NOT_CONNECTED"},
        },
    }


def window_is_covered(start: datetime, end: datetime, coverage: dict[str, Any]) -> bool:
    domains = coverage.get("domains") if isinstance(coverage.get("domains"), dict) else {"legacy": coverage}
    if not domains:
        return False
    for item in domains.values():
        if not isinstance(item, dict):
            return False
        covered_start, covered_end = parse_time(item.get("start_utc")), parse_time(item.get("end_utc"))
        if not (item.get("status") == "complete" and covered_start and covered_end and covered_start <= start and covered_end >= end):
            return False
    return True


def planned_fomc_events(plan: dict[str, Any]) -> list[dict[str, Any]]:
    labels = {
        "fomc_18_00_package": "Пакет решения FOMC",
        "fomc_18_30_press_conference": "Пресс-конференция FOMC",
    }
    result = []
    for phase in plan.get("phases") or []:
        timestamp = parse_time(phase.get("anchor_time_utc"))
        if timestamp:
            result.append({
                "title": labels.get(str(phase.get("phase_id")), str(phase.get("phase_id") or "FOMC phase")),
                "time_utc": timestamp.isoformat(), "importance": "high", "currencies": ["USD"],
                "status": "verified_phase_anchor", "source": "fomc_phase_plan", "source_kind": "verified_phase_plan",
                "verified_primary_source": True,
            })
    return result


def is_expected_fomc_event(event: dict[str, Any], meeting_id: str) -> bool:
    title = str(event.get("title") or "").casefold()
    timestamp = event_time(event)
    return bool(timestamp and timestamp.date().isoformat() == meeting_id and any(term in title for term in FOMC_TERMS))


def is_relevant_competitor(event: dict[str, Any]) -> bool:
    title = str(event.get("title") or "").casefold()
    importance = str(event.get("importance") or "unknown").lower()
    currencies = {str(value).upper() for value in event.get("currencies") or []}
    return bool(
        event.get("verified_primary_source")
        or "USD" in currencies
        or importance == "high"
        or any(term in title for term in SYSTEMIC_TERMS)
    )


def classify_window(
    start: datetime,
    end: datetime,
    meeting_id: str,
    events: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    inside = [item for item in events if (timestamp := event_time(item)) and start <= timestamp < end]
    expected = [item for item in inside if is_expected_fomc_event(item, meeting_id)]
    competitors = [item for item in inside if item not in expected and is_relevant_competitor(item)]
    verified = [item for item in competitors if item.get("verified_primary_source")]
    covered = window_is_covered(start, end, coverage)
    if verified:
        status = "contaminated"
        reason = "VERIFIED_COMPETING_EVENT_INSIDE_WINDOW"
    elif competitors:
        status = "mixed"
        reason = "RELEVANT_REPORTED_OR_SCHEDULED_EVENT_INSIDE_WINDOW"
    elif not covered:
        status = "insufficient_coverage"
        reason = "NO_COMPLETE_EXTERNAL_EVENT_COVERAGE"
    else:
        status = "clean"
        reason = "COMPLETE_COVERAGE_AND_NO_COMPETING_EVENT"
    return {
        "status": status, "reason": reason, "calendar_coverage_complete": covered,
        "expected_fomc_events": expected, "competing_events": competitors,
        "causal_promotion_allowed": status == "clean",
    }


def build_contamination_report(
    plan: dict[str, Any],
    reaction: dict[str, Any],
    events: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    if not plan.get("available") or not reaction.get("available"):
        return {"schema_version": 1, "available": False, "reason": "FOMC_PHASE_OR_REACTION_UNAVAILABLE"}
    reaction_phases = {str(item.get("phase_id")): item for item in reaction.get("phases") or []}
    reports: list[dict[str, Any]] = []
    for phase in plan.get("phases") or []:
        phase_id = str(phase.get("phase_id") or "")
        measured = {str(item.get("name")): item for item in (reaction_phases.get(phase_id) or {}).get("windows") or []}
        windows: list[dict[str, Any]] = []
        for spec in phase.get("windows") or []:
            name = str(spec.get("name") or "")
            market = measured.get(name) or {}
            if not spec.get("measurement_allowed"):
                windows.append({"name": name, "status": "excluded_by_phase_plan", "reason": "KNOWN_PHASE_OVERLAP"})
                continue
            start, end = parse_time(spec.get("start_utc")), parse_time(spec.get("end_utc"))
            if not start or not end:
                continue
            assessment = classify_window(start, end, str(plan.get("meeting_id") or ""), events, coverage)
            windows.append({
                "name": name, "start_utc": start.isoformat(), "end_utc": end.isoformat(),
                "market_alignment": market.get("alignment"), "market_next_gate": market.get("confirmation_eligible_next_gate"),
                **assessment,
                "permissions": {"confirm_driver": False, "entry": False, "direction_switch": False, "score": False},
            })
        reports.append({"phase_id": phase_id, "windows": windows})
    return {
        "schema_version": 1, "available": True, "meeting_id": plan.get("meeting_id"),
        "coverage": coverage, "phases": reports,
        "policy": "Отсутствие найденной новости не доказывает чистоту окна без полного исторического покрытия.",
        "permissions": {"confirm_driver": False, "entry": False, "direction_switch": False, "score": False},
    }


def main() -> int:
    plan, reaction = load_json(PLAN_FILE), load_json(REACTION_FILE)
    calendar, official = load_json(CALENDAR_FILE), load_json(OFFICIAL_RELEASES_FILE)
    historical = load_json(HISTORICAL_CALENDAR_FILE)
    selected_calendar = historical if historical.get("available") else calendar
    events = normalize_events(selected_calendar, official) + planned_fomc_events(plan)
    supplied_coverage = historical.get("coverage") if historical.get("available") else None
    payload = build_contamination_report(plan, reaction, events, coverage_contract(selected_calendar, supplied_coverage))
    atomic_json(OUTPUT_FILE, payload)
    statuses = [window.get("status") for phase in payload.get("phases") or [] for window in phase.get("windows") or []]
    print(f"FOMC_CONTAMINATION: available={payload.get('available')} | statuses={statuses}")
    print(f"FOMC_CONTAMINATION_SAVED: {OUTPUT_FILE}")
    print("FOMC_CONTAMINATION_ENGINE_C2_5C1: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
