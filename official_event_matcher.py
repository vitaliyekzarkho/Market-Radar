from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from reaction_window_engine import parse_time


CALENDAR_FILE = Path("data/news/raw_events.json")
ISM_FILE = Path("data/macro/official_ism_facts.json")
BLS_FILE = Path("data/macro/official_bls_facts.json")
BEA_FILE = Path("data/macro/official_bea_facts.json")
FOMC_FILE = Path("data/macro/official_fomc_facts.json")
OUTPUT_FILE = Path("data/news/verified_events.json")
MAX_TIME_DISTANCE = timedelta(hours=6)

INDICATORS = {
    "federal funds rate": "fomc_target_upper_pct",
    "core pce price index m/m": "core_pce_price_index_mom",
    "core pce price index y/y": "core_pce_price_index_yoy",
    "core pce m/m": "core_pce_price_index_mom",
    "core pce y/y": "core_pce_price_index_yoy",
    "pce price index m/m": "pce_price_index_mom",
    "pce price index y/y": "pce_price_index_yoy",
    "personal income m/m": "personal_income_mom",
    "personal spending m/m": "personal_spending_mom",
    "advance gdp q/q": "real_gdp_qoq_annualized",
    "prelim gdp q/q": "real_gdp_qoq_annualized",
    "second gdp q/q": "real_gdp_qoq_annualized",
    "final gdp q/q": "real_gdp_qoq_annualized",
    "third gdp q/q": "real_gdp_qoq_annualized",
    "average hourly earnings": "average_hourly_earnings_mom",
    "non-farm employment change": "nonfarm_payrolls",
    "nonfarm employment change": "nonfarm_payrolls",
    "non-farm payrolls": "nonfarm_payrolls",
    "nonfarm payrolls": "nonfarm_payrolls",
    "unemployment rate": "unemployment_rate",
    "core cpi m/m": "core_cpi_mom",
    "core cpi y/y": "core_cpi_yoy",
    "core ppi m/m": "core_ppi_mom",
    "core ppi y/y": "core_ppi_yoy",
    "cpi m/m": "cpi_mom",
    "cpi y/y": "cpi_yoy",
    "ppi m/m": "ppi_mom",
    "ppi y/y": "ppi_yoy",
    "ism services pmi": "ism_services_pmi",
    "ism manufacturing pmi": "ism_manufacturing_pmi",
}


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


def indicator_for_event(event: dict[str, Any]) -> str | None:
    title = str(event.get("title") or "").strip().lower()
    currencies = event.get("currencies")
    if isinstance(currencies, str):
        currencies = [currencies]
    if not isinstance(currencies, list) or "USD" not in {str(item).upper() for item in currencies}:
        return None
    return next((indicator for phrase, indicator in INDICATORS.items() if phrase in title), None)


def expected_report_period(event_time: datetime, indicator: str | None = None) -> str:
    if indicator == "fomc_target_upper_pct":
        return event_time.date().isoformat()
    if indicator == "real_gdp_qoq_annualized":
        quarter = (event_time.month - 1) // 3
        if quarter == 0:
            return f"{event_time.year - 1:04d}-Q4"
        return f"{event_time.year:04d}-Q{quarter}"
    year, month = event_time.year, event_time.month - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year:04d}-{month:02d}"


def estimate_stage_for_event(event: dict[str, Any]) -> str | None:
    title = str(event.get("title") or "").strip().lower()
    if "advance gdp" in title:
        return "advance"
    if "prelim gdp" in title or "second gdp" in title:
        return "second"
    if "final gdp" in title or "third gdp" in title:
        return "third"
    return None


def match_fact(event: dict[str, Any], facts: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    indicator = indicator_for_event(event)
    event_time = parse_time(event.get("event_time_utc") or event.get("date"))
    if not indicator or not event_time:
        return None, "UNSUPPORTED_OR_INVALID_EVENT"
    period = expected_report_period(event_time, indicator)
    required_stage = estimate_stage_for_event(event)
    candidates = []
    for fact in facts:
        published = parse_time(fact.get("published_at_utc")) if isinstance(fact, dict) else None
        provenance = fact.get("provenance") if isinstance(fact.get("provenance"), dict) else {}
        timing_matches = bool(not published or abs(published - event_time) <= MAX_TIME_DISTANCE)
        if (
            fact.get("indicator") == indicator and fact.get("report_period") == period
            and (required_stage is None or fact.get("estimate_stage") == required_stage)
            and timing_matches
            and provenance.get("fact_permission") is True
            and fact.get("actual") not in (None, "")
        ):
            candidates.append(fact)
    if len(candidates) == 1:
        timing = "OFFICIAL_TIMESTAMP" if candidates[0].get("published_at_utc") else "CALENDAR_SCHEDULE_BOUND"
        return candidates[0], f"MATCHED:{timing}"
    return (None, "AMBIGUOUS_MATCH" if candidates else "NO_MATCH")


def enrich_events(calendar: dict[str, Any], official: dict[str, Any] | list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    sources = official if isinstance(official, list) else [official]
    facts = [
        fact for source in sources if isinstance(source, dict)
        for fact in (source.get("facts") if isinstance(source.get("facts"), list) else [])
        if isinstance(fact, dict)
    ]
    events = calendar.get("events") if isinstance(calendar.get("events"), list) else []
    output, audit = [], []
    for source_event in events:
        if not isinstance(source_event, dict):
            continue
        event = dict(source_event)
        fact, status = match_fact(event, facts)
        if fact:
            timing_basis = "official_timestamp" if fact.get("published_at_utc") else "calendar_schedule_bound"
            provenance = fact.get("provenance") if isinstance(fact.get("provenance"), dict) else {}
            primary_source = bool(provenance.get("primary_verified"))
            authoritative_fact = bool(provenance.get("fact_permission"))
            event.update({
                "status": "released", "actual": fact["actual"], "fact_status": "verified",
                "source": fact.get("source"), "source_url": fact.get("source_url"),
                "source_verified": authoritative_fact,
                "authoritative_fact": authoritative_fact,
                "primary_source": primary_source,
                "verified_primary_source": primary_source,
                "source_transport": fact.get("source_transport") or provenance.get("kind"),
                "official_fact": fact,
                "calendar_source": source_event.get("source"),
                "calendar_source_url": source_event.get("source_url"),
                "fact_timing_basis": timing_basis,
            })
        output.append(event)
        if indicator_for_event(event):
            audit.append({"title": event.get("title"), "event_time_utc": event.get("event_time_utc"), "status": status, "fact_id": fact.get("fact_id") if fact else None})
    return {
        **calendar, "schema_version": 2, "provider": "Calendar + verified official facts",
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(), "events": output,
        "official_match_audit": audit,
    }


def main() -> int:
    now = datetime.now(timezone.utc)
    payload = enrich_events(
        load_json(CALENDAR_FILE),
        [load_json(ISM_FILE), load_json(BLS_FILE), load_json(BEA_FILE), load_json(FOMC_FILE)], now,
    )
    atomic_json(OUTPUT_FILE, payload)
    matched = sum(str(item.get("status") or "").startswith("MATCHED:") for item in payload.get("official_match_audit") or [])
    print(f"OFFICIAL_EVENT_MATCHER: matched={matched} | events={len(payload.get('events') or [])}")
    print(f"VERIFIED_EVENTS_SAVED: {OUTPUT_FILE}")
    print("OFFICIAL_EVENT_MATCHER_ENGINE: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
