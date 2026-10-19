from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


DATA_DIR = Path("data")
OUTPUT_FILE = DATA_DIR / "market_context" / "GOLD_pro.json"
EVENTS_FILE = DATA_DIR / "news" / "raw_events.json"
TIMEFRAME = "H1"
LOOKBACK_BARS = 24
MIN_COMPONENTS = 3

# Sign converts the pair return into USD strength: +1 when USD is the base,
# -1 when USD is the quote. The engine prefers the broker's .pro feed.
PAIR_SPECS = {
    "EURUSD": -1,
    "GBPUSD": -1,
    "AUDUSD": -1,
    "USDCAD": 1,
    "USDCHF": 1,
}
PAIR_LOCAL_CURRENCY = {
    "EURUSD": "EUR",
    "GBPUSD": "GBP",
    "AUDUSD": "AUD",
    "USDCAD": "CAD",
    "USDCHF": "CHF",
}
CONTAMINATION_BEFORE = timedelta(minutes=30)
CONTAMINATION_AFTER = timedelta(minutes=90)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def choose_symbol(base: str, available: Iterable[str]) -> str | None:
    names = set(available)
    for candidate in (f"{base}.pro", base):
        if candidate in names:
            return candidate
    matches = sorted(name for name in names if name.upper().startswith(base))
    return matches[0] if matches else None


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def parse_time(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def contaminated_pairs(events: list[dict[str, Any]], now: datetime) -> dict[str, dict[str, Any]]:
    blocked: dict[str, dict[str, Any]] = {}
    for event in events:
        importance = str(event.get("importance") or "").lower()
        currencies = event.get("currencies") if isinstance(event.get("currencies"), list) else []
        event_time = parse_time(event.get("event_time_utc") or event.get("date"))
        if importance not in {"high", "medium"} or not event_time:
            continue
        if not (event_time - CONTAMINATION_BEFORE <= now <= event_time + CONTAMINATION_AFTER):
            continue
        for pair, local_currency in PAIR_LOCAL_CURRENCY.items():
            if local_currency in {str(item).upper() for item in currencies}:
                blocked[pair] = {
                    "currency": local_currency,
                    "event": str(event.get("title") or "economic event"),
                    "event_time_utc": event_time.isoformat(),
                }
    return blocked


def component_from_closes(base: str, symbol: str, usd_sign: int, closes: list[float]) -> dict[str, Any] | None:
    values = [value for item in closes if (value := finite(item)) is not None and value > 0]
    if len(values) < 6:
        return None
    hourly_returns = [(new / old - 1.0) * 100.0 for old, new in zip(values, values[1:])]
    volatility = sum(abs(item) for item in hourly_returns[-20:]) / min(20, len(hourly_returns))
    if volatility <= 0:
        return None
    raw_1h = hourly_returns[-1]
    raw_4h = (values[-1] / values[-5] - 1.0) * 100.0
    usd_1h = usd_sign * raw_1h
    usd_4h = usd_sign * raw_4h
    normalized_1h = max(-3.0, min(3.0, usd_1h / volatility))
    normalized_4h = max(-3.0, min(3.0, usd_4h / (volatility * 2.0)))
    contribution = 0.4 * normalized_1h + 0.6 * normalized_4h
    return {
        "pair": base,
        "symbol": symbol,
        "usd_position": "base" if usd_sign > 0 else "quote",
        "return_1h_percent": round(raw_1h, 5),
        "return_4h_percent": round(raw_4h, 5),
        "usd_move_1h_percent": round(usd_1h, 5),
        "usd_move_4h_percent": round(usd_4h, 5),
        "normalized_1h": round(normalized_1h, 4),
        "normalized_4h": round(normalized_4h, 4),
        "contribution": round(contribution, 4),
    }


def aggregate_components(components: list[dict[str, Any]], contamination: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    contamination = contamination or {}
    excluded = [{**item, "excluded_reason": contamination[item["pair"]]} for item in components if item.get("pair") in contamination]
    components = [item for item in components if item.get("pair") not in contamination]
    valid = [item for item in components if finite(item.get("contribution")) is not None]
    if len(valid) < MIN_COMPONENTS:
        return {
            "available": False,
            "reason": "INSUFFICIENT_COMPONENTS",
            "component_count": len(valid),
            "required_components": MIN_COMPONENTS,
            "components": valid,
            "excluded_components": excluded,
            "scoring_applied": False,
        }
    average = sum(float(item["contribution"]) for item in valid) / len(valid)
    score = round(max(-100.0, min(100.0, average / 3.0 * 100.0)))
    if score >= 15:
        direction = "bullish"
    elif score <= -15:
        direction = "bearish"
    else:
        direction = "mixed"
    if direction == "mixed":
        agreeing = sum(1 for item in valid if abs(float(item["contribution"])) < 0.45)
    else:
        sign = 1 if direction == "bullish" else -1
        agreeing = sum(1 for item in valid if float(item["contribution"]) * sign > 0)
    return {
        "available": True,
        "name": "MarketRadar USD Breadth",
        "direction": direction,
        "strength": score,
        "agreement": {"agreeing": agreeing, "total": len(valid)},
        "timeframe": TIMEFRAME,
        "horizons": ["1H", "4H"],
        "components": valid,
        "excluded_components": excluded,
        "scoring_applied": False,
    }


def read_components_from_mt5() -> tuple[list[dict[str, Any]], list[str]]:
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    missing: list[str] = []
    components: list[dict[str, Any]] = []
    try:
        symbols = mt5.symbols_get() or []
        available = [str(item.name) for item in symbols]
        for base, usd_sign in PAIR_SPECS.items():
            symbol = choose_symbol(base, available)
            if not symbol:
                missing.append(base)
                continue
            mt5.symbol_select(symbol, True)
            rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 1, LOOKBACK_BARS + 1)
            if rates is None or len(rates) < 6:
                missing.append(base)
                continue
            component = component_from_closes(base, symbol, usd_sign, [float(row["close"]) for row in rates])
            if component:
                components.append(component)
            else:
                missing.append(base)
    finally:
        mt5.shutdown()
    return components, missing


def build_payload(now: datetime | None = None) -> dict[str, Any]:
    generated = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        components, missing = read_components_from_mt5()
        raw = load_json(EVENTS_FILE)
        events = [item for item in raw.get("events", []) if isinstance(item, dict)]
        contamination = contaminated_pairs(events, generated)
        context = aggregate_components(components, contamination)
        context["missing_pairs"] = missing
        context["contamination_filter"] = contamination
    except Exception as exc:
        context = {
            "available": False,
            "reason": type(exc).__name__,
            "components": [],
            "scoring_applied": False,
        }
    return {
        "schema_version": 1,
        "generated_at_utc": generated.astimezone(timezone.utc).isoformat(),
        "context": context,
    }


def main() -> int:
    payload = build_payload()
    atomic_write_json(OUTPUT_FILE, payload)
    context = payload["context"]
    if context.get("available"):
        agreement = context["agreement"]
        print(
            f"USD_BREADTH: {str(context['direction']).upper()} | strength={context['strength']:+d} "
            f"| agreement={agreement['agreeing']}/{agreement['total']}"
        )
    else:
        print(f"USD_BREADTH: NO_DATA | reason={context.get('reason', 'UNKNOWN')}")
    print(f"USD_BREADTH_SAVED: {OUTPUT_FILE}")
    print("USD_BREADTH_ENGINE: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
