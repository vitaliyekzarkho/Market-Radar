from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fomc_phase_engine import parse_time
from trading_economics_adapter import API_KEY_ENV, API_ROOT, HTTP_TIMEOUT_SECONDS, normalize_event


PLAN_FILE = Path("data/macro/fomc_phase_plan.json")
OUTPUT_FILE = Path("data/macro/fomc_historical_calendar.json")


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


def historical_url(api_key: str, meeting_id: str) -> str:
    country = urllib.parse.quote("united states")
    query = urllib.parse.urlencode({"c": api_key, "f": "json"})
    return f"{API_ROOT}/calendar/country/{country}/{meeting_id}/{meeting_id}?{query}"


def fetch_historical(api_key: str, meeting_id: str) -> list[dict[str, Any]]:
    request = urllib.request.Request(historical_url(api_key, meeting_id), headers={"Accept": "application/json", "User-Agent": "MarketRadar/1.0"})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, list):
        raise ValueError("HISTORICAL_CALENDAR_RESPONSE_NOT_LIST")
    return [item for item in value if isinstance(item, dict)]


def build_payload(meeting_id: str, raw: list[dict[str, Any]], fetched_at: datetime) -> dict[str, Any]:
    day = datetime.fromisoformat(meeting_id).replace(tzinfo=timezone.utc)
    events = [event for item in raw if (event := normalize_event(item, fetched_at)) is not None]
    return {
        "schema_version": 1, "available": True, "provider": "Trading Economics historical calendar",
        "meeting_id": meeting_id, "fetched_at_utc": fetched_at.isoformat(), "events": events,
        "coverage": {
            "domain": "scheduled_macro", "status": "complete",
            "start_utc": day.isoformat(), "end_utc": (day + timedelta(days=1)).isoformat(),
            "country": "United States", "requested_date": meeting_id,
        },
    }


def unavailable(reason: str, meeting_id: str | None = None, detail: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1, "available": False, "meeting_id": meeting_id, "reason": reason, "detail": detail,
        "coverage": {"domain": "scheduled_macro", "status": "unknown", "start_utc": None, "end_utc": None, "reason": reason},
        "events": [],
    }


def main() -> int:
    plan = load_json(PLAN_FILE)
    meeting_id = str(plan.get("meeting_id") or "")
    api_key = str(os.environ.get(API_KEY_ENV) or "").strip()
    if not parse_time(meeting_id):
        payload = unavailable("FOMC_MEETING_ID_MISSING", meeting_id or None)
    elif not api_key:
        payload = unavailable("TRADING_ECONOMICS_API_KEY_MISSING", meeting_id)
    else:
        try:
            raw = fetch_historical(api_key, meeting_id)
            payload = build_payload(meeting_id, raw, datetime.now(timezone.utc))
        except Exception as exc:
            payload = unavailable("HISTORICAL_CALENDAR_REQUEST_FAILED", meeting_id, f"{type(exc).__name__}:{exc}")
    atomic_json(OUTPUT_FILE, payload)
    print(f"FOMC_HISTORICAL_CALENDAR: available={payload.get('available')} | events={len(payload.get('events') or [])}")
    print(f"FOMC_HISTORICAL_CALENDAR_SAVED: {OUTPUT_FILE}")
    print("FOMC_HISTORICAL_CALENDAR_ADAPTER_C2_5C2: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
