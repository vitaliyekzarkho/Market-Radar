from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OUTPUT = Path("data/market_context/DXY.json")
SYMBOL_CANDIDATES = ("DXY.pro", "DXY", "USDX.pro", "USDX")


def atomic_write(payload: dict[str, Any]) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OUTPUT)


def detect_fvgs(rows: list[dict[str, float]], timeframe: str) -> list[dict[str, Any]]:
    if len(rows) < 4:
        return []
    price = float(rows[-1]["close"])
    zones: list[dict[str, Any]] = []
    for index in range(2, len(rows)):
        first, third = rows[index - 2], rows[index]
        if float(third["low"]) > float(first["high"]):
            low, high, direction = float(first["high"]), float(third["low"]), "bullish"
        elif float(third["high"]) < float(first["low"]):
            low, high, direction = float(third["high"]), float(first["low"]), "bearish"
        else:
            continue
        interaction = "inside" if low <= price <= high else "above" if price > high else "below"
        distance = 0.0 if interaction == "inside" else min(abs(price / low - 1), abs(price / high - 1)) * 100
        zones.append({
            "name": f"{timeframe} {direction} FVG", "timeframe": timeframe,
            "direction": direction, "low": low, "high": high,
            "interaction": interaction, "distance_percent": round(distance, 3),
            "formed_at": third.get("time"),
        })
    zones.sort(key=lambda item: (0 if item["interaction"] == "inside" else 1, item["distance_percent"], str(item["formed_at"])), reverse=False)
    return zones


def context_from_frames(frames: dict[str, list[dict[str, float]]], symbol: str) -> dict[str, Any]:
    zones: list[dict[str, Any]] = []
    directions: list[str] = []
    for timeframe in ("D1", "H4", "H1"):
        rows = frames.get(timeframe, [])
        zones.extend(detect_fvgs(rows, timeframe)[:2])
        if len(rows) >= 5:
            directions.append("bullish" if rows[-1]["close"] > rows[-5]["close"] else "bearish")
    current = next((item for item in zones if item["interaction"] == "inside"), None)
    direction = "bullish" if directions.count("bullish") > directions.count("bearish") else "bearish" if directions.count("bearish") > directions.count("bullish") else "mixed"
    return {
        "available": True, "symbol": symbol, "direction": direction,
        "current_zone": current, "nearby_zones": zones[:6],
        "source_type": "direct DXY/USDX broker chart",
        "warning": "Zone interaction is a hypothesis driver; it is not confirmation by itself.",
    }


def read_mt5() -> tuple[str, dict[str, list[dict[str, float]]]]:
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise RuntimeError("MT5_INIT_FAILED")
    try:
        names = {str(item.name) for item in (mt5.symbols_get() or [])}
        symbol = next((name for name in SYMBOL_CANDIDATES if name in names), None)
        if not symbol:
            raise RuntimeError("DXY_SYMBOL_NOT_FOUND")
        mt5.symbol_select(symbol, True)
        mapping = {"D1": mt5.TIMEFRAME_D1, "H4": mt5.TIMEFRAME_H4, "H1": mt5.TIMEFRAME_H1}
        frames: dict[str, list[dict[str, float]]] = {}
        for name, timeframe in mapping.items():
            rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, 220)
            if rates is None:
                frames[name] = []
                continue
            frames[name] = [{key: float(row[key]) for key in ("open", "high", "low", "close")} | {"time": int(row["time"])} for row in rates]
        return symbol, frames
    finally:
        mt5.shutdown()


def main() -> int:
    try:
        symbol, frames = read_mt5()
        context = context_from_frames(frames, symbol)
    except Exception as exc:
        context = {"available": False, "reason": str(exc), "source_type": "direct DXY/USDX broker chart"}
    payload = {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "context": context}
    atomic_write(payload)
    print(f"DXY_CONTEXT: {'READY' if context.get('available') else 'NO_DATA'} | {context.get('reason', context.get('direction'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
