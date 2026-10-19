from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SYMBOL = "GOLD.pro"
SYMBOL_FILE = SYMBOL.replace(".", "_")
DATA_DIR = Path("data")
RAW_EVENTS_FILE = DATA_DIR / "news" / "raw_events.json"
OUTPUT_FILE = DATA_DIR / "news" / f"{SYMBOL_FILE}.json"

ASSET_ALIASES = {"GOLD", "GOLD.PRO", "XAU", "XAUUSD"}
RELEVANT_CURRENCIES = {"USD"}
LOOKAHEAD = timedelta(hours=24)
RECENT_RELEASE = timedelta(hours=2)


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def parse_time(value: Any) -> datetime | None:
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


def string_set(value: Any) -> set[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return set()
    return {str(item).strip().upper() for item in value if str(item).strip()}


def relevant_to_gold(event: dict[str, Any]) -> bool:
    assets = string_set(event.get("assets"))
    currencies = string_set(event.get("currencies"))
    return bool(assets & ASSET_ALIASES or currencies & RELEVANT_CURRENCIES)


def safe_source_url(event: dict[str, Any]) -> str | None:
    value = str(event.get("source_url") or "").strip()
    return value if value.startswith(("https://", "http://")) else None


def normalized_importance(event: dict[str, Any]) -> str:
    value = str(event.get("importance") or "low").strip().lower()
    aliases = {"3": "high", "2": "medium", "1": "low"}
    return aliases.get(value, value) if value in {"high", "medium", "low", "3", "2", "1"} else "low"


def candidate(event: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    event_time = parse_time(event.get("event_time_utc") or event.get("date"))
    source_url = safe_source_url(event)
    if not event_time or not source_url or not relevant_to_gold(event):
        return None
    importance = normalized_importance(event)
    if importance not in {"high", "medium"}:
        return None
    delta = event_time - now
    if delta > LOOKAHEAD or delta < -RECENT_RELEASE:
        return None

    released = str(event.get("status") or "scheduled").lower() in {"released", "actual"}
    raw_effect = str(event.get("effect_xauusd") or event.get("effect") or "").lower()
    effect = raw_effect if released and raw_effect in {"bullish", "bearish", "neutral"} else "risk_only"
    confidence = str(event.get("confidence") or ("verified" if released else "scheduled")).lower()
    return {
        "available": True,
        "title": str(event.get("title") or event.get("event") or "Economic event").strip(),
        "effect": effect,
        "confidence": confidence,
        "importance": importance,
        "status": "released" if released else "scheduled",
        "event_time_utc": event_time.isoformat(),
        "minutes_to_event": round(delta.total_seconds() / 60, 1),
        "source": str(event.get("source") or "Trading Economics").strip(),
        "source_url": source_url,
        "actual": event.get("actual"),
        "forecast": event.get("forecast"),
        "previous": event.get("previous"),
        "scoring_applied": False,
    }


def select_driver(events: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    candidates = [item for raw in events if isinstance(raw, dict) if (item := candidate(raw, now))]
    if not candidates:
        return {
            "available": False,
            "reason": "NO_VERIFIED_RELEVANT_EVENT",
            "scoring_applied": False,
        }
    importance_rank = {"high": 0, "medium": 1}
    candidates.sort(key=lambda item: (
        importance_rank[item["importance"]],
        0 if item["status"] == "released" else 1,
        abs(float(item["minutes_to_event"])),
    ))
    return candidates[0]


def build_payload(raw: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    events = raw.get("events") if isinstance(raw.get("events"), list) else []
    driver = select_driver(events, moment)
    return {
        "schema_version": 1,
        "generated_at_utc": moment.isoformat(),
        "symbol": SYMBOL,
        "provider": str(raw.get("provider") or "none"),
        "driver_context": driver,
    }


def main() -> int:
    payload = build_payload(load_json(RAW_EVENTS_FILE))
    atomic_write_json(OUTPUT_FILE, payload)
    driver = payload["driver_context"]
    if driver.get("available"):
        print(
            f"NEWS_DRIVER: {driver['effect'].upper()} | {driver['title']} | "
            f"importance={driver['importance']} | score_impact=0"
        )
    else:
        print(f"NEWS_DRIVER: NO_DATA | reason={driver.get('reason')}")
    print(f"NEWS_DRIVER_SAVED: {OUTPUT_FILE}")
    print("NEWS_DRIVER_ENGINE: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
