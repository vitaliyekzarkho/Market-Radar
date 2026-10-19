from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dxy_context_engine import SYMBOL_CANDIDATES as DXY_CANDIDATES
from oil_context_engine import choose_symbol as choose_oil_symbol
from prepare_replay_data import atomic_csv, atomic_json, normalize_rates
from rates_context_engine import choose_rate_symbols
from usd_breadth_engine import PAIR_SPECS, choose_symbol as choose_fx_symbol


DEFAULT_EVENT_ID = "iran-trump-20260309-very-soon"
DEFAULT_EVENT_TIME = "2026-03-09T22:07:00+00:00"
DEFAULT_TITLE = "Trump says Iran war will end very soon"


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "_" for character in value)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def select_verified_event(payload: dict[str, Any], title: str) -> dict[str, Any]:
    expected = title.strip().casefold()
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    candidates = [
        event for event in events
        if isinstance(event, dict)
        and str(event.get("title") or "").strip().casefold() == expected
        and event.get("actual") not in (None, "")
        and str(event.get("status") or "").lower() in {"released", "actual"}
        and event.get("source_verified") is True
        and event.get("authoritative_fact") is True
    ]
    if len(candidates) != 1:
        raise ValueError(f"VERIFIED_EVENT_MATCH_COUNT:{len(candidates)}")
    return dict(candidates[0])


def historical_event(args: argparse.Namespace, verified: dict[str, Any] | None = None) -> dict[str, Any]:
    if verified is not None:
        event = dict(verified)
        event["event_id"] = args.event_id
        event.setdefault("event_family", args.event_family)
        event.setdefault("lifecycle_state", "reported_fact")
        event.setdefault("fact_status", "verified")
        event.setdefault("novelty_evidence", {"status": "unknown"})
        return event
    return {
        "schema_version": 1, "event_id": args.event_id, "title": args.title,
        "event_family": args.event_family, "event_time_utc": parse_time(args.event_time).isoformat(),
        "source": args.source, "source_url": args.source_url,
        "lifecycle_state": "reported_fact", "status": "released",
        "fact_status": "verified", "source_verified": True,
        "authoritative_fact": True, "primary_source": True,
        "source_transport": "official_primary",
        "novelty_evidence": {"status": "unknown"},
    }


def symbol_name(item: Any) -> str:
    return str(getattr(item, "name", None) or (item.get("name") if isinstance(item, dict) else ""))


def symbol_description(item: Any) -> str:
    return str(getattr(item, "description", None) or (item.get("description") if isinstance(item, dict) else ""))


def choose_gold_symbol(symbols: list[Any], preferred: str = "GOLD.pro") -> str | None:
    names = [symbol_name(item) for item in symbols]
    for candidate in (preferred, "GOLD.pro", "XAUUSD.pro", "GOLD", "XAUUSD"):
        if candidate in names:
            return candidate
    for item in symbols:
        combined = f"{symbol_name(item)} {symbol_description(item)}".upper().replace(" ", "")
        if "XAUUSD" in combined or "GOLD" in combined:
            return symbol_name(item)
    return None


def select_market_symbols(symbols: list[Any], preferred_gold: str = "GOLD.pro") -> dict[str, Any]:
    names = [symbol_name(item) for item in symbols if symbol_name(item)]
    gold = choose_gold_symbol(symbols, preferred_gold)
    oil = choose_oil_symbol([
        SimpleNamespace(name=symbol_name(item), description=symbol_description(item)) for item in symbols
    ])
    dxy = next((candidate for candidate in DXY_CANDIDATES if candidate in names), None)
    breadth = {
        pair: chosen for pair in PAIR_SPECS
        if (chosen := choose_fx_symbol(pair, names)) is not None
    }
    rates = choose_rate_symbols([
        SimpleNamespace(name=symbol_name(item), description=symbol_description(item)) for item in symbols
    ])
    usd_mode = "direct_dxy" if dxy else "breadth" if len(breadth) >= 3 else "unavailable"
    return {
        "gold": gold,
        "oil": oil,
        "usd": {"mode": usd_mode, "dxy": dxy, "breadth": breadth},
        "rates": rates,
    }


def channel_status(selection: dict[str, Any], downloads: dict[str, dict[str, Any]]) -> dict[str, Any]:
    usd = selection["usd"]
    breadth_downloaded = [pair for pair, symbol in usd["breadth"].items() if symbol in downloads]
    direct_ready = bool(usd.get("dxy") and usd["dxy"] in downloads)
    breadth_ready = len(breadth_downloaded) >= 3
    return {
        "gold": {"required": True, "available": bool(selection.get("gold") in downloads)},
        "oil": {"required": True, "available": bool(selection.get("oil") in downloads)},
        "usd": {
            "required": True,
            "available": direct_ready or breadth_ready,
            "mode": "direct_dxy" if direct_ready else "breadth" if breadth_ready else "unavailable",
            "breadth_components": breadth_downloaded,
        },
        "rates": {
            "required": False,
            "available": any(symbol in downloads for symbol in selection.get("rates", {}).values()),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a synchronized MT5 causal replay pack around one event.")
    parser.add_argument("--event-id", default=DEFAULT_EVENT_ID)
    parser.add_argument("--event-time", default=DEFAULT_EVENT_TIME)
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--event-family", default="geopolitical_deescalation")
    parser.add_argument("--source", default="GovInfo / Trump press conference")
    parser.add_argument("--source-url", default="https://www.govinfo.gov/app/details/DCPD-202600161")
    parser.add_argument("--verified-events", default="")
    parser.add_argument("--verified-event-title", default="")
    parser.add_argument("--gold-symbol", default="GOLD.pro")
    parser.add_argument("--before-hours", type=int, default=48)
    parser.add_argument("--after-hours", type=int, default=48)
    parser.add_argument("--broker-offset-hours", type=int, default=2)
    parser.add_argument("--output-root", default="data/causal_replay")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    verified_event = None
    if args.verified_events or args.verified_event_title:
        if not args.verified_events or not args.verified_event_title:
            raise ValueError("VERIFIED_EVENT_REQUIRES_FILE_AND_TITLE")
        verified_event = select_verified_event(load_json(Path(args.verified_events)), args.verified_event_title)
    event = historical_event(args, verified_event)
    event_time = parse_time(str(event.get("event_time_utc") or event.get("date") or args.event_time))
    start = event_time - timedelta(hours=max(24, args.before_hours))
    end = event_time + timedelta(hours=max(24, args.after_hours))
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 package is not installed") from exc
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        catalog = list(mt5.symbols_get() or [])
        selection = select_market_symbols(catalog, args.gold_symbol)
        if not selection.get("gold"):
            raise RuntimeError("GOLD_SYMBOL_NOT_FOUND")
        requested: list[tuple[str, str]] = [("gold", selection["gold"])]
        if selection.get("oil"):
            requested.append(("oil", selection["oil"]))
        usd = selection["usd"]
        if usd.get("dxy"):
            requested.append(("dxy", usd["dxy"]))
        else:
            requested.extend((f"usd_{pair.lower()}", symbol) for pair, symbol in usd["breadth"].items())
        requested.extend((f"rates_{tenor}", symbol) for tenor, symbol in selection.get("rates", {}).items())

        output = Path(args.output_root) / safe_name(args.event_id)
        raw_dir = output / "raw"
        downloads: dict[str, dict[str, Any]] = {}
        failures: list[dict[str, str]] = []
        for role, symbol in requested:
            if symbol in downloads:
                continue
            if not mt5.symbol_select(symbol, True):
                failures.append({"role": role, "symbol": symbol, "reason": "SYMBOL_SELECT_FAILED"})
                continue
            rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, start, end)
            data = normalize_rates(rates, broker_offset_hours=args.broker_offset_hours)
            if data.empty:
                failures.append({"role": role, "symbol": symbol, "reason": "NO_M15_HISTORY"})
                continue
            path = raw_dir / f"{safe_name(role)}__{safe_name(symbol)}__M15.csv"
            atomic_csv(path, data)
            downloads[symbol] = {
                "role": role, "symbol": symbol, "timeframe": "M15", "path": str(path),
                "rows": int(len(data)), "first_utc": str(data.iloc[0]["utc_time"]),
                "last_utc": str(data.iloc[-1]["utc_time"]),
            }
            print(f"CAUSAL_REPLAY_DATA: {role:<14} {symbol:<18} rows={len(data)}")
    finally:
        mt5.shutdown()

    channels = channel_status(selection, downloads)
    event["event_time_utc"] = event_time.isoformat()
    atomic_json(output / "historical_event.json", event)
    manifest = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "event_id": args.event_id, "event_time_utc": event_time.isoformat(),
        "download_window_utc": {"start": start.isoformat(), "end": end.isoformat()},
        "source": "MetaTrader5.copy_rates_range", "timeframe": "M15",
        "broker_offset_hours": args.broker_offset_hours, "selection": selection,
        "channels": channels, "downloads": list(downloads.values()), "failures": failures,
        "causal_replay_ready": all(value["available"] for value in channels.values() if value["required"]),
        "permissions": {"causal_conclusion": False, "entry": False, "direction_switch": False},
    }
    atomic_json(output / "manifest.json", manifest)
    print(f"CAUSAL_REPLAY_MANIFEST: {output / 'manifest.json'}")
    print(f"CAUSAL_REPLAY_READY: {manifest['causal_replay_ready']}")
    print("PREPARE_CAUSAL_REPLAY_PACK: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
