from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DATA_DIR = Path("data")
RAW_EVENTS_FILE = DATA_DIR / "news" / "raw_events.json"
CACHE_FILE = DATA_DIR / "news" / "trading_economics_cache.json"
API_KEY_ENV = "MARKETRADAR_TRADING_ECONOMICS_API_KEY"
API_ROOT = "https://api.tradingeconomics.com"
CALENDAR_PAGE = "https://tradingeconomics.com/united-states/calendar"
HTTP_TIMEOUT_SECONDS = 15
MAX_CACHE_AGE = timedelta(hours=6)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def api_url(api_key: str, now: datetime, dated: bool = True) -> str:
    start = (now - timedelta(days=1)).date().isoformat()
    end = (now + timedelta(days=1)).date().isoformat()
    country = urllib.parse.quote("united states")
    query = urllib.parse.urlencode({"c": api_key, "f": "json"})
    path = f"calendar/country/{country}"
    if dated:
        path += f"/{start}/{end}"
    return f"{API_ROOT}/{path}?{query}"


def request_json(url: str) -> list[dict[str, Any]]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "MarketRadar/1.0"},
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, list):
        raise ValueError("Trading Economics response is not a list")
    return [item for item in value if isinstance(item, dict)]


def fetch_calendar(api_key: str, now: datetime) -> list[dict[str, Any]]:
    try:
        return request_json(api_url(api_key, now, dated=True))
    except urllib.error.HTTPError as exc:
        # Some entry-level roles allow the current country snapshot but not a
        # custom date range. Fall back without weakening source verification.
        if exc.code not in {400, 403, 404}:
            raise
        return request_json(api_url(api_key, now, dated=False))


def text(item: dict[str, Any], key: str) -> str:
    return str(item.get(key) or "").strip()


def normalize_event(item: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    try:
        importance = int(item.get("Importance") or 0)
    except (TypeError, ValueError):
        return None
    if importance not in {2, 3}:
        return None
    date_text = text(item, "Date")
    if not date_text:
        return None
    actual = text(item, "Actual")
    source_url = text(item, "SourceURL") or CALENDAR_PAGE
    if not source_url.startswith(("https://", "http://")):
        source_url = CALENDAR_PAGE
    return {
        "event_id": text(item, "CalendarId"),
        "title": text(item, "Event") or text(item, "Category") or "US economic event",
        "category": text(item, "Category"),
        "event_time_utc": date_text,
        "importance": importance,
        "currencies": ["USD"],
        "assets": ["XAUUSD"],
        "status": "released" if actual else "scheduled",
        "source": text(item, "Source") or "Trading Economics",
        "source_url": source_url,
        "actual": actual or None,
        "forecast": text(item, "Forecast") or text(item, "TEForecast") or None,
        "previous": text(item, "Previous") or None,
        "reference": text(item, "Reference") or None,
        "last_update": text(item, "LastUpdate") or None,
        "provenance": "licensed_calendar",
        "verified_primary_source": False,
        "source_verified": False,
    }


def normalized_payload(items: list[dict[str, Any]], now: datetime, mode: str = "live") -> dict[str, Any]:
    events = [event for item in items if (event := normalize_event(item, now)) is not None]
    return {
        "schema_version": 1,
        "provider": "Trading Economics",
        "mode": mode,
        "fetched_at_utc": now.astimezone(timezone.utc).isoformat(),
        "calendar_url": CALENDAR_PAGE,
        "events": events,
    }


def usable_cache(payload: dict[str, Any], now: datetime) -> bool:
    fetched = str(payload.get("fetched_at_utc") or "")
    if not isinstance(payload.get("events"), list) or not fetched:
        return False
    try:
        timestamp = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
    except ValueError:
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc) - timestamp.astimezone(timezone.utc) <= MAX_CACHE_AGE


def publish_cache(cache: dict[str, Any]) -> None:
    payload = dict(cache)
    payload["mode"] = "cache"
    atomic_write_json(RAW_EVENTS_FILE, payload)


def main() -> int:
    now = datetime.now(timezone.utc)
    api_key = str(os.environ.get(API_KEY_ENV) or "").strip()
    cache = load_json(CACHE_FILE)

    if not api_key:
        if usable_cache(cache, now):
            publish_cache(cache)
            print("TRADING_ECONOMICS: CACHE | reason=API_KEY_MISSING")
        else:
            print("TRADING_ECONOMICS: NOT_CONFIGURED | API key missing")
        print("TRADING_ECONOMICS_ADAPTER: PASSED")
        return 0

    try:
        items = fetch_calendar(api_key, now)
        payload = normalized_payload(items, now)
        atomic_write_json(CACHE_FILE, payload)
        atomic_write_json(RAW_EVENTS_FILE, payload)
        print(f"TRADING_ECONOMICS: LIVE | relevant_events={len(payload['events'])}")
    except (OSError, TimeoutError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
        detail = f"HTTP_{exc.code}" if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
        if usable_cache(cache, now):
            publish_cache(cache)
            print(f"TRADING_ECONOMICS: CACHE | live request unavailable ({detail})")
        else:
            print(f"TRADING_ECONOMICS: UNAVAILABLE | {detail}")
    print("TRADING_ECONOMICS_ADAPTER: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
