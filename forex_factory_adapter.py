from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DATA_DIR = Path("data")
RAW_EVENTS_FILE = DATA_DIR / "news" / "raw_events.json"
CACHE_FILE = DATA_DIR / "news" / "forex_factory_cache.json"
FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CALENDAR_URL = "https://www.forexfactory.com/calendar"
HTTP_TIMEOUT_SECONDS = 15
MAX_CACHE_AGE = timedelta(hours=6)
ALLOWED_IMPACTS = {"High", "Medium"}
RELEVANT_CURRENCIES = {"USD", "EUR", "GBP", "AUD", "CAD", "CHF"}


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


def fetch_feed() -> list[dict[str, Any]]:
    request = urllib.request.Request(
        FEED_URL,
        headers={"Accept": "application/json", "User-Agent": "MarketRadar/1.0"},
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, list):
        raise ValueError("Forex Factory weekly feed is not a list")
    return [item for item in value if isinstance(item, dict)]


def normalize_event(item: dict[str, Any]) -> dict[str, Any] | None:
    currency = str(item.get("country") or "").strip().upper()
    impact = str(item.get("impact") or "").strip().title()
    event_time = str(item.get("date") or "").strip()
    title = str(item.get("title") or "").strip()
    if currency not in RELEVANT_CURRENCIES or impact not in ALLOWED_IMPACTS:
        return None
    if not event_time or not title:
        return None
    actual = str(item.get("actual") or "").strip() or None
    return {
        "title": title,
        "event_time_utc": event_time,
        "importance": impact.lower(),
        "currencies": [currency],
        "assets": ["XAUUSD"] if currency == "USD" else [],
        "status": "released" if actual is not None else "scheduled",
        "source": "Forex Factory",
        "source_url": CALENDAR_URL,
        "actual": actual,
        "forecast": str(item.get("forecast") or "").strip() or None,
        "previous": str(item.get("previous") or "").strip() or None,
        "provenance": "licensed_calendar",
        "verified_primary_source": False,
        "source_verified": False,
    }


def normalized_payload(items: list[dict[str, Any]], now: datetime, mode: str = "live") -> dict[str, Any]:
    events = [event for item in items if (event := normalize_event(item)) is not None]
    return {
        "schema_version": 1,
        "provider": "Forex Factory",
        "mode": mode,
        "fetched_at_utc": now.astimezone(timezone.utc).isoformat(),
        "calendar_url": CALENDAR_URL,
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
    cache = load_json(CACHE_FILE)
    try:
        payload = normalized_payload(fetch_feed(), now)
        atomic_write_json(CACHE_FILE, payload)
        atomic_write_json(RAW_EVENTS_FILE, payload)
        print(f"FOREX_FACTORY: LIVE | relevant_events={len(payload['events'])}")
    except (OSError, TimeoutError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
        if usable_cache(cache, now):
            publish_cache(cache)
            print(f"FOREX_FACTORY: CACHE | live feed unavailable ({type(exc).__name__})")
        else:
            print(f"FOREX_FACTORY: UNAVAILABLE | {type(exc).__name__}")
    print("FOREX_FACTORY_ADAPTER: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
