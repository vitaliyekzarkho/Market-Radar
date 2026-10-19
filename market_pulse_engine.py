from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SYMBOL = "GOLD.pro"
SYMBOL_FILE = SYMBOL.replace(".", "_")
DATA_DIR = Path("data")
M5_FILE = DATA_DIR / "raw" / SYMBOL_FILE / "M5.csv"
OUTPUT_FILE = DATA_DIR / "market_context" / f"{SYMBOL_FILE}_pulse.json"
WINDOWS = (5, 15, 30)
MIN_BARS = 12


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def choose_symbol(available: Iterable[str]) -> str | None:
    names = set(available)
    for candidate in (SYMBOL, "GOLD", "XAUUSD.pro", "XAUUSD"):
        if candidate in names:
            return candidate
    for name in sorted(names):
        upper = name.upper()
        if upper.startswith("GOLD") or upper.startswith("XAUUSD"):
            return name
    return None


def normalize_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        close = finite(row.get("close"))
        high = finite(row.get("high"))
        low = finite(row.get("low"))
        volume = finite(row.get("tick_volume"))
        if None in {close, high, low}:
            continue
        normalized.append({
            "time": str(row.get("utc_time") or row.get("time") or ""),
            "open": finite(row.get("open")) or close,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": volume or 0.0,
        })
    return normalized


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return normalize_rows(csv.DictReader(handle))
    except OSError:
        return []


def fetch_m5_from_mt5(count: int = 240) -> tuple[list[dict[str, Any]], str | None]:
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        symbols = mt5.symbols_get() or []
        symbol = choose_symbol(item.name for item in symbols)
        if not symbol:
            raise RuntimeError("Gold symbol not found")
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, count)
        if rates is None or len(rates) < MIN_BARS:
            raise RuntimeError(f"M5 data unavailable: {mt5.last_error()}")
        rows = []
        for item in rates:
            rows.append({
                "utc_time": datetime.fromtimestamp(int(item["time"]), timezone.utc).isoformat(),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "tick_volume": float(item["tick_volume"]),
            })
        return normalize_rows(rows), symbol
    finally:
        mt5.shutdown()


def median(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def true_ranges(rows: list[dict[str, Any]]) -> list[float]:
    output: list[float] = []
    for previous, current in zip(rows, rows[1:]):
        output.append(max(
            current["high"] - current["low"],
            abs(current["high"] - previous["close"]),
            abs(current["low"] - previous["close"]),
        ))
    return output


def window_snapshot(rows: list[dict[str, Any]], minutes: int) -> dict[str, Any]:
    bars = max(1, minutes // 5)
    if len(rows) < max(MIN_BARS, bars + 6):
        return {"available": False, "reason": "INSUFFICIENT_M5_BARS", "minutes": minutes}
    recent = rows[-bars:]
    baseline = rows[-max(36, bars + 12):-bars] or rows[:-bars]
    start = recent[0]["open"]
    end = recent[-1]["close"]
    move = end - start
    baseline_tr = true_ranges(baseline)
    atr = median(baseline_tr[-24:])
    normalized_move = move / (atr * math.sqrt(bars)) if atr > 0 else 0.0
    direction = "bullish" if normalized_move >= 0.35 else "bearish" if normalized_move <= -0.35 else "mixed"
    volume_base = median([item["tick_volume"] for item in baseline[-24:]])
    volume_recent = sum(item["tick_volume"] for item in recent) / len(recent)
    volume_ratio = volume_recent / volume_base if volume_base > 0 else 0.0
    return {
        "available": True,
        "minutes": minutes,
        "direction": direction,
        "move": round(move, 5),
        "normalized_move": round(normalized_move, 3),
        "range": round(max(item["high"] for item in recent) - min(item["low"] for item in recent), 5),
        "volume_ratio": round(volume_ratio, 2),
        "start": round(start, 5),
        "end": round(end, 5),
    }


def classify_state(horizons: dict[str, dict[str, Any]], previous: dict[str, Any] | None = None) -> tuple[str, str]:
    valid = [item for item in horizons.values() if item.get("available")]
    directional = [item for item in valid if item.get("direction") in {"bullish", "bearish"}]
    if not directional:
        return "transient", "mixed"
    bullish = sum(1 for item in directional if item["direction"] == "bullish")
    bearish = sum(1 for item in directional if item["direction"] == "bearish")
    direction = "bullish" if bullish > bearish else "bearish" if bearish > bullish else "mixed"
    agreement = max(bullish, bearish)
    strong = [item for item in directional if abs(float(item["normalized_move"])) >= 0.8]
    if direction == "mixed":
        state = "failed" if previous and previous.get("direction") in {"bullish", "bearish"} else "transient"
    elif agreement >= 3 and len(strong) >= 2:
        state = "persistent"
    elif agreement >= 2:
        state = "building"
    else:
        state = "transient"
    if previous and previous.get("direction") not in {None, "mixed", direction}:
        state = "failed"
    return state, direction


def build_payload(rows: list[dict[str, Any]], previous: dict[str, Any] | None = None, source: str = "M5 cache") -> dict[str, Any]:
    horizons = {f"{minutes}m": window_snapshot(rows, minutes) for minutes in WINDOWS}
    previous_pulse = (previous or {}).get("pulse") if isinstance((previous or {}).get("pulse"), dict) else {}
    state, direction = classify_state(horizons, previous_pulse)
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "source": source,
        "purpose": "Hidden lower-timeframe observation only; never an entry signal.",
        "pulse": {
            "available": any(item.get("available") for item in horizons.values()),
            "state": state,
            "direction": direction,
            "horizons": horizons,
            "scoring_applied": False,
            "telegram_eligible": False,
        },
    }


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def main() -> int:
    previous = load_json(OUTPUT_FILE)
    rows = read_csv_rows(M5_FILE)
    source = "M5 cache"
    try:
        fresh, symbol = fetch_m5_from_mt5()
        if fresh:
            rows = fresh
            source = f"MT5 {symbol}"
    except Exception as error:
        if not rows:
            payload = {
                "schema_version": 1,
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "symbol": SYMBOL,
                "pulse": {"available": False, "reason": type(error).__name__, "scoring_applied": False, "telegram_eligible": False},
            }
            atomic_write_json(OUTPUT_FILE, payload)
            print(f"MARKET_PULSE: NO_DATA | reason={type(error).__name__}")
            return 0
    payload = build_payload(rows, previous, source)
    atomic_write_json(OUTPUT_FILE, payload)
    pulse = payload["pulse"]
    print(f"MARKET_PULSE: {str(pulse['state']).upper()} | direction={pulse['direction']} | source={source} | alert=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
