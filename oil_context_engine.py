from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from market_data_quality import bar_diagnostics, broker_utc_offset_hours, cache_freshness, market_bar_freshness, utc_bar_time
from rates_context_engine import normalized_move


OUTPUT_FILE = Path("data") / "macro" / "oil_context.json"
CACHE_FILE = Path("data") / "macro" / "oil_context_cache.json"
ALIASES = ("BRENT", "UKOIL", "XBRUSD", "WTI", "USOIL", "XTIUSD")


def atomic_write(payload: dict[str, Any], path: Path = OUTPUT_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def choose_symbol(symbols: list[Any]) -> str | None:
    for alias in ALIASES:
        for symbol in symbols:
            name = str(getattr(symbol, "name", ""))
            description = str(getattr(symbol, "description", ""))
            if alias in f"{name} {description}".upper().replace(" ", ""):
                return name
    return None


def observation_from_rows(symbol: str, rows: Any, now: datetime, broker_offset: int = 0) -> dict[str, Any] | None:
    values = sorted(list(rows), key=lambda row: float(row["time"])) if rows is not None else []
    if not values:
        return None
    closes = [float(row["close"]) for row in values]
    move = normalized_move(closes)
    if not move:
        return None
    last_bar_time = utc_bar_time(values[-1]["time"], broker_offset)
    freshness = market_bar_freshness(last_bar_time, now, 60)
    if not freshness["usable"]:
        return None
    normalized = float(move["normalized_4h"])
    last_bar = last_bar_time.isoformat() if last_bar_time else None
    return {
        "available": True, "symbol": symbol,
        "direction": "rising" if normalized >= 0.35 else "falling" if normalized <= -0.35 else "mixed",
        "value": round(closes[-1], 5), **move, "timeframe": "H1",
        "last_closed_bar_utc": last_bar, "freshness": freshness,
        "broker_utc_offset_hours": broker_offset,
        "source": "MT5 broker market data", "source_kind": "intraday_market_data",
        "data_mode": "intraday_live", "intraday_reaction_capable": True,
        "causal_permission": "reaction_evidence", "scoring_applied": False,
    }


def read_live(now: datetime) -> dict[str, Any]:
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise RuntimeError("MT5_INIT_FAILED")
    try:
        symbol = choose_symbol(list(mt5.symbols_get() or []))
        if not symbol:
            return {"available": False, "reason": "BROKER_OIL_SYMBOL_NOT_FOUND", "scoring_applied": False}
        mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        broker_offset = broker_utc_offset_hours(getattr(tick, "time", None), now)
        rows = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 1, 25)
        observation = observation_from_rows(symbol, rows, now, broker_offset) if rows is not None else None
        return observation or {
            "available": False,
            "reason": "INSUFFICIENT_OR_STALE_OIL_DATA",
            "selected_symbol": symbol,
            "diagnostics": bar_diagnostics(rows, now, 60, broker_offset),
            "scoring_applied": False,
        }
    finally:
        mt5.shutdown()


def cached_context(cache: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    freshness = cache_freshness(cache.get("generated_at_utc"), now, timedelta(hours=3))
    context = cache.get("context") if isinstance(cache.get("context"), dict) else {}
    if not freshness["usable"] or not context.get("available"):
        return None
    result = dict(context)
    result.update({
        "data_mode": "cache", "cache_freshness": freshness,
        "intraday_reaction_capable": False, "causal_permission": "context_only",
        "limitation": "Cached oil data cannot verify a new headline reaction.",
    })
    return result


def collect_context(now: datetime) -> dict[str, Any]:
    try:
        live = read_live(now)
    except Exception as exc:
        live = {"available": False, "reason": type(exc).__name__, "scoring_applied": False}
    if live.get("available"):
        return live
    return cached_context(load_json(CACHE_FILE), now) or live


def main() -> int:
    now = datetime.now(timezone.utc)
    context = collect_context(now)
    payload = {"schema_version": 2, "generated_at_utc": now.isoformat(), "context": context}
    if context.get("available") and context.get("data_mode") == "intraday_live":
        atomic_write(payload, CACHE_FILE)
    atomic_write(payload)
    print(f"OIL_CONTEXT: {context.get('direction', 'NO_DATA')} | mode={context.get('data_mode', 'unavailable')} | reason={context.get('reason', 'ok')}")
    print("OIL_CONTEXT_ENGINE: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
