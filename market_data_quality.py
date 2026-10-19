from __future__ import annotations

from datetime import datetime, timedelta, timezone
from numbers import Real
from typing import Any


def parse_time(value: Any) -> datetime | None:
    if isinstance(value, Real) or (not isinstance(value, (str, bytes)) and hasattr(value, "__float__")):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, TypeError, ValueError):
            return None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def market_bar_freshness(last_bar: Any, now: datetime, timeframe_minutes: int = 60) -> dict[str, Any]:
    timestamp = parse_time(last_bar)
    if not timestamp:
        return {"status": "unknown", "age_seconds": None, "usable": False}
    raw_age = now.astimezone(timezone.utc) - timestamp
    if raw_age < -timedelta(minutes=5):
        return {"status": "future", "age_seconds": round(raw_age.total_seconds()), "usable": False}
    age = max(timedelta(0), raw_age)
    expected = timedelta(minutes=max(1, timeframe_minutes))
    if age <= expected * 2:
        status = "fresh"
    elif age <= expected * 4:
        status = "recent"
    else:
        status = "stale"
    return {"status": status, "age_seconds": round(age.total_seconds()), "usable": status in {"fresh", "recent"}}


def daily_series_freshness(last_observation: Any, now: datetime) -> dict[str, Any]:
    timestamp = parse_time(last_observation)
    if not timestamp:
        return {"status": "unknown", "age_seconds": None, "usable": False}
    age = max(timedelta(0), now.astimezone(timezone.utc) - timestamp)
    if age <= timedelta(days=2):
        status = "fresh"
    elif age <= timedelta(days=5):
        status = "recent"
    else:
        status = "stale"
    return {"status": status, "age_seconds": round(age.total_seconds()), "usable": status in {"fresh", "recent"}}


def broker_utc_offset_hours(tick_time: Any, now: datetime) -> int:
    timestamp = parse_time(tick_time)
    if not timestamp:
        return 0
    offset = round((timestamp - now.astimezone(timezone.utc)).total_seconds() / 3600)
    return offset if -12 <= offset <= 14 else 0


def utc_bar_time(value: Any, broker_offset_hours: int = 0) -> datetime | None:
    timestamp = parse_time(value)
    return timestamp - timedelta(hours=broker_offset_hours) if timestamp else None


def bar_diagnostics(rows: Any, now: datetime, timeframe_minutes: int = 60, broker_offset_hours: int = 0) -> dict[str, Any]:
    values = list(rows) if rows is not None else []
    timestamps = sorted(
        (parsed for row in values if (parsed := utc_bar_time(row["time"], broker_offset_hours)) is not None),
    )
    newest = timestamps[-1] if timestamps else None
    oldest = timestamps[0] if timestamps else None
    return {
        "bar_count": len(values),
        "oldest_bar_utc": oldest.isoformat() if oldest else None,
        "newest_bar_utc": newest.isoformat() if newest else None,
        "broker_utc_offset_hours": broker_offset_hours,
        "freshness": market_bar_freshness(newest, now, timeframe_minutes),
    }


def cache_freshness(generated_at: Any, now: datetime, max_age: timedelta = timedelta(hours=3)) -> dict[str, Any]:
    timestamp = parse_time(generated_at)
    if not timestamp:
        return {"status": "unknown", "age_seconds": None, "usable": False}
    age = max(timedelta(0), now.astimezone(timezone.utc) - timestamp)
    return {
        "status": "recent" if age <= max_age else "stale",
        "age_seconds": round(age.total_seconds()),
        "usable": age <= max_age,
    }
