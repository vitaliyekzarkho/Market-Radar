from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from causal_replay_runner import synthetic_usd_breadth
from driver_contract import stable_driver_id
from macro_release_cluster import cluster_release
from prepare_causal_replay_pack import select_market_symbols
from reaction_window_engine import build_reaction_report, parse_time
from usd_breadth_engine import PAIR_SPECS


DATA_DIR = Path("data")
RAW_EVENTS_FILE = DATA_DIR / "news" / "raw_events.json"
VERIFIED_EVENTS_FILE = DATA_DIR / "news" / "verified_events.json"
OUTPUT_FILE = DATA_DIR / "news" / "GOLD_pro_reaction_windows.json"
RECENT_RELEASE = timedelta(hours=6)
BAR_DURATION = timedelta(minutes=15)


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
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


def is_released_usd_event(event: dict[str, Any], now: datetime) -> bool:
    currencies = event.get("currencies")
    if isinstance(currencies, str):
        currencies = [currencies]
    event_time = parse_time(event.get("event_time_utc") or event.get("date"))
    released = event.get("actual") not in (None, "") or str(event.get("status") or "").lower() in {"released", "actual"}
    return bool(
        event_time and released and isinstance(currencies, list)
        and "USD" in {str(item).upper() for item in currencies}
        and timedelta(0) <= now - event_time <= RECENT_RELEASE
    )


def select_recent_event(events: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    candidates = [event for event in events if isinstance(event, dict) and is_released_usd_event(event, now)]
    candidates.sort(key=lambda event: parse_time(event.get("event_time_utc") or event.get("date")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return candidates[0] if candidates else None


def rows_from_rates(rates: Any, now: datetime) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if rates is None:
        return result
    for row in list(rates):
        opened = datetime.fromtimestamp(int(row["time"]), timezone.utc)
        if opened + BAR_DURATION > now:
            continue
        result.append({
            "utc_time": opened.isoformat(), "open": float(row["open"]),
            "high": float(row["high"]), "low": float(row["low"]), "close": float(row["close"]),
            "tick_volume": float(row["tick_volume"]), "is_closed": True,
        })
    return result


def collect_assets(event_time: datetime, now: datetime) -> dict[str, dict[str, Any]]:
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        catalog = list(mt5.symbols_get() or [])
        selection = select_market_symbols(catalog)
        if not selection.get("gold") or not selection.get("oil"):
            raise RuntimeError("LIVE_REACTION_SYMBOLS_UNAVAILABLE")
        start = event_time - timedelta(hours=25)
        requested = {"gold": selection["gold"], "oil": selection["oil"]}
        pair_rows: dict[str, list[dict[str, Any]]] = {}
        assets: dict[str, dict[str, Any]] = {}
        for role, symbol in requested.items():
            mt5.symbol_select(symbol, True)
            rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, start, now)
            rows = rows_from_rates(rates, now)
            if not rows:
                raise RuntimeError(f"NO_LIVE_REACTION_DATA:{role}")
            assets[role] = {"symbol": symbol, "source": "MT5 live reaction", "timeframe": "M15", "candles": rows}
        for pair, symbol in selection.get("usd", {}).get("breadth", {}).items():
            if pair not in PAIR_SPECS:
                continue
            mt5.symbol_select(symbol, True)
            rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, start, now)
            rows = rows_from_rates(rates, now)
            if rows:
                pair_rows[pair] = rows
        assets["usd"] = {
            "symbol": "USD_BREADTH", "source": f"{len(pair_rows)}-pair live synthetic breadth",
            "timeframe": "M15", "candles": synthetic_usd_breadth(pair_rows),
        }
        return assets
    finally:
        mt5.shutdown()


def build_payload(raw: dict[str, Any], now: datetime, assets: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    events = raw.get("events") if isinstance(raw.get("events"), list) else []
    event = select_recent_event(events, now)
    if not event:
        return {"schema_version": 1, "available": False, "reason": "NO_RECENT_RELEASED_USD_EVENT", "generated_at_utc": now.isoformat()}
    event = cluster_release(dict(event), events)
    event.setdefault("event_id", stable_driver_id(event))
    event_time = parse_time(event.get("event_time_utc") or event.get("date"))
    if not event_time:
        return {"schema_version": 1, "available": False, "reason": "INVALID_EVENT_TIME", "generated_at_utc": now.isoformat()}
    measured_assets = assets if assets is not None else collect_assets(event_time, now)
    report = build_reaction_report(event, measured_assets, now)
    return {"schema_version": 1, "available": True, "event": event, "reaction_report": report, "generated_at_utc": now.isoformat()}


def main() -> int:
    now = datetime.now(timezone.utc)
    try:
        source = VERIFIED_EVENTS_FILE if VERIFIED_EVENTS_FILE.is_file() else RAW_EVENTS_FILE
        payload = build_payload(load_json(source), now)
    except Exception as exc:
        payload = {"schema_version": 1, "available": False, "reason": type(exc).__name__, "detail": str(exc), "generated_at_utc": now.isoformat()}
    atomic_json(OUTPUT_FILE, payload)
    if payload.get("available"):
        gold = payload["reaction_report"]["assets"]["gold"]["post_event"]
        print(f"LIVE_REACTION: event={payload['event'].get('title')} | post15={gold['post_15m']['status']} | post60={gold['post_60m']['status']}")
    else:
        print(f"LIVE_REACTION: NO_DATA | reason={payload.get('reason')}")
    print(f"LIVE_REACTION_SAVED: {OUTPUT_FILE}")
    print("LIVE_REACTION_ENGINE: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
