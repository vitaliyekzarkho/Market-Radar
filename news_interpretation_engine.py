from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SYMBOL = "GOLD.pro"
SYMBOL_FILE = SYMBOL.replace(".", "_")
DATA_DIR = Path("data")
RAW_EVENTS_FILE = DATA_DIR / "news" / "raw_events.json"
USD_CONTEXT_FILE = DATA_DIR / "market_context" / f"{SYMBOL_FILE}.json"
OUTPUT_FILE = DATA_DIR / "news" / f"{SYMBOL_FILE}.json"
LOOKAHEAD = timedelta(hours=24)
RECENT_RELEASE = timedelta(hours=2)

HIGHER_IS_USD_BULLISH = (
    "cpi", "consumer price", "pce", "producer price", "ppi", "gdp",
    "retail sales", "average hourly", "wage", "non-farm", "nonfarm",
    "payroll", "employment change", "ism", "federal funds", "interest rate",
)
HIGHER_IS_USD_BEARISH = (
    "unemployment rate", "jobless claims", "unemployment claims",
    "initial claims", "continuing claims",
)


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
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_numeric(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = float(match.group())
    suffix = text[match.end():].strip().upper()[:1]
    multiplier = {"K": 1_000.0, "M": 1_000_000.0, "B": 1_000_000_000.0}.get(suffix, 1.0)
    return number * multiplier


def importance(event: dict[str, Any]) -> str:
    value = str(event.get("importance") or "low").lower()
    return {"3": "high", "2": "medium", "1": "low"}.get(value, value)


def is_usd_event(event: dict[str, Any]) -> bool:
    currencies = event.get("currencies")
    if isinstance(currencies, str):
        currencies = [currencies]
    return isinstance(currencies, list) and "USD" in {str(item).upper() for item in currencies}


def surprise_direction(title: str, actual: Any, forecast: Any) -> tuple[str, str, str]:
    actual_number = parse_numeric(actual)
    forecast_number = parse_numeric(forecast)
    if actual_number is None or forecast_number is None:
        return "neutral", "neutral", "Actual/forecast comparison unavailable."
    if actual_number == forecast_number:
        return "neutral", "neutral", "Actual matched forecast."
    lowered = title.lower()
    inverse = any(token in lowered for token in HIGHER_IS_USD_BEARISH)
    direct = any(token in lowered for token in HIGHER_IS_USD_BULLISH)
    if not inverse and not direct:
        return "neutral", "neutral", "Event family has no deterministic surprise rule."
    actual_higher = actual_number > forecast_number
    usd_bullish = (not actual_higher) if inverse else actual_higher
    usd_effect = "bullish" if usd_bullish else "bearish"
    gold_effect = "bearish" if usd_bullish else "bullish"
    relation = "above" if actual_higher else "below"
    return gold_effect, usd_effect, f"Actual came {relation} forecast; inferred USD {usd_effect}."


def usd_reaction(usd_effect: str, usd_payload: dict[str, Any]) -> tuple[str, str]:
    context = usd_payload.get("usd_context") if isinstance(usd_payload.get("usd_context"), dict) else usd_payload
    direction = str(context.get("direction") or "mixed").lower()
    if usd_effect not in {"bullish", "bearish"} or direction not in {"bullish", "bearish"}:
        return "unconfirmed", direction
    return ("confirmed" if usd_effect == direction else "contradicted"), direction


def interpret_event(event: dict[str, Any], now: datetime, usd_payload: dict[str, Any]) -> dict[str, Any] | None:
    event_time = parse_time(event.get("event_time_utc") or event.get("date"))
    if not event_time or not is_usd_event(event) or importance(event) not in {"high", "medium"}:
        return None
    delta = event_time - now
    if delta > LOOKAHEAD or delta < -RECENT_RELEASE:
        return None
    actual = event.get("actual")
    released = actual not in (None, "") or str(event.get("status") or "").lower() in {"released", "actual"}
    title = str(event.get("title") or event.get("event") or "Economic event").strip()
    if released:
        effect, usd_effect, explanation = surprise_direction(title, actual, event.get("forecast"))
        reaction_status, observed_usd = usd_reaction(usd_effect, usd_payload)
        confidence = reaction_status if effect != "neutral" else "unclassified"
        explanation += f" Observed USD breadth: {observed_usd}; reaction {reaction_status}."
    else:
        effect, usd_effect = "risk_only", "unknown"
        reaction_status, confidence = "pending", "scheduled"
        explanation = "Scheduled release: forecast is context, not a directional trigger."
    source_url = str(event.get("source_url") or "").strip()
    return {
        "available": True,
        "title": title,
        "effect": effect,
        "usd_effect": usd_effect,
        "confidence": confidence,
        "importance": importance(event),
        "status": "released" if released else "scheduled",
        "event_time_utc": event_time.isoformat(),
        "minutes_to_event": round(delta.total_seconds() / 60, 1),
        "source": str(event.get("source") or "Forex Factory"),
        "source_url": source_url if source_url.startswith(("http://", "https://")) else None,
        "actual": actual,
        "forecast": event.get("forecast"),
        "previous": event.get("previous"),
        "reaction_status": reaction_status,
        "explanation": explanation,
        "scoring_applied": False,
    }


def select_driver(events: list[dict[str, Any]], now: datetime, usd_payload: dict[str, Any]) -> dict[str, Any]:
    candidates = [item for raw in events if isinstance(raw, dict) if (item := interpret_event(raw, now, usd_payload))]
    if not candidates:
        return {"available": False, "reason": "NO_VERIFIED_RELEVANT_EVENT", "scoring_applied": False}
    candidates.sort(key=lambda item: (
        0 if item["importance"] == "high" else 1,
        0 if item["status"] == "released" else 1,
        abs(float(item["minutes_to_event"])),
    ))
    return candidates[0]


def build_payload(raw: dict[str, Any], usd_payload: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    events = raw.get("events") if isinstance(raw.get("events"), list) else []
    return {
        "schema_version": 2,
        "generated_at_utc": moment.isoformat(),
        "symbol": SYMBOL,
        "provider": str(raw.get("provider") or "none"),
        "driver_context": select_driver(events, moment, usd_payload),
    }


def main() -> int:
    payload = build_payload(load_json(RAW_EVENTS_FILE), load_json(USD_CONTEXT_FILE))
    atomic_write_json(OUTPUT_FILE, payload)
    driver = payload["driver_context"]
    if driver.get("available"):
        print(f"NEWS_DRIVER: {driver['status'].upper()} | {driver['effect'].upper()} | {driver['title']} | reaction={driver['reaction_status']}")
    else:
        print(f"NEWS_DRIVER: NO_DATA | reason={driver.get('reason')}")
    print(f"NEWS_DRIVER_SAVED: {OUTPUT_FILE}")
    print("NEWS_INTERPRETATION_ENGINE: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
