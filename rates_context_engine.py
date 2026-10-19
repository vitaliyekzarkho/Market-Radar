from __future__ import annotations

import csv
import io
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from market_data_quality import bar_diagnostics, broker_utc_offset_hours, cache_freshness, daily_series_freshness, market_bar_freshness, utc_bar_time


OUTPUT_FILE = Path("data") / "macro" / "rates_context.json"
CACHE_FILE = Path("data") / "macro" / "rates_context_cache.json"
HTTP_TIMEOUT_SECONDS = 8
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_SERIES = {"US2Y": "DGS2", "US10Y": "DGS10", "US10Y_REAL": "DFII10"}
MATURITIES = {
    "US2Y": ("US2Y", "UST2Y", "US02Y", "2YYIELD", "US2YIELD"),
    "US10Y": ("US10Y", "UST10Y", "10YYIELD", "US10YIELD"),
}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
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


def choose_rate_symbols(symbols: list[Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for label, aliases in MATURITIES.items():
        for symbol in symbols:
            name = str(getattr(symbol, "name", ""))
            description = str(getattr(symbol, "description", ""))
            compact = f"{name} {description}".upper().replace(" ", "")
            strong_alias = any(alias in compact for alias in aliases)
            explicitly_yield = "YIELD" in compact or label in compact or (label == "US2Y" and "UST2Y" in compact) or (label == "US10Y" and "UST10Y" in compact)
            if strong_alias and explicitly_yield:
                result[label] = name
                break
    return result


def normalized_move(closes: list[float]) -> dict[str, Any] | None:
    values = [float(item) for item in closes if math.isfinite(float(item)) and float(item) > 0]
    if len(values) < 6:
        return None
    returns = [(new / old - 1) * 100 for old, new in zip(values, values[1:])]
    volatility = sum(abs(item) for item in returns[-20:]) / min(20, len(returns))
    if volatility <= 0:
        return None
    move = (values[-1] / values[-5] - 1) * 100
    normalized = max(-3.0, min(3.0, move / (volatility * 2)))
    return {"move_4h_percent": round(move, 5), "normalized_4h": round(normalized, 4)}


def observation_from_rows(label: str, symbol: str, rows: Any, now: datetime, broker_offset: int = 0) -> dict[str, Any] | None:
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
    move_bps = (closes[-1] - closes[-5]) * 100.0
    last_bar = last_bar_time.isoformat() if last_bar_time else None
    return {
        "label": label, "symbol": symbol, "value_percent": round(closes[-1], 5),
        "move_4h_bps": round(move_bps, 3), **move, "timeframe": "H1",
        "last_closed_bar_utc": last_bar, "freshness": freshness,
        "broker_utc_offset_hours": broker_offset,
        "source": "MT5 broker market data", "source_kind": "intraday_market_data",
        "causal_permission": "reaction_evidence",
    }


def aggregate_observations(observations: dict[str, dict[str, Any]], mode: str) -> dict[str, Any]:
    if not observations:
        return {"available": False, "reason": "NO_USABLE_RATES_DATA", "scoring_applied": False}
    average = sum(float(item["normalized_4h"]) for item in observations.values()) / len(observations)
    direction = "rising" if average >= 0.35 else "falling" if average <= -0.35 else "mixed"
    return {
        "available": True, "direction": direction, "observations": observations,
        "data_mode": mode, "intraday_reaction_capable": mode == "intraday_live",
        "scoring_applied": False,
    }


def daily_rate_structure(observations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    two_year = observations.get("US2Y")
    ten_year = observations.get("US10Y")
    real_ten_year = observations.get("US10Y_REAL")
    if two_year and ten_year:
        spread_bps = (float(ten_year["value_percent"]) - float(two_year["value_percent"])) * 100.0
        spread_change = float(ten_year["move_4d_bps"]) - float(two_year["move_4d_bps"])
        result["yield_curve"] = {
            "spread_2s10s_bps": round(spread_bps, 3),
            "change_4d_bps": round(spread_change, 3),
            "shape": "normal" if spread_bps > 0 else "inverted" if spread_bps < 0 else "flat",
            "dynamic": "steepening" if spread_change >= 3 else "flattening" if spread_change <= -3 else "stable",
            "causal_permission": "context_only",
        }
    if real_ten_year:
        real_move = float(real_ten_year["move_4d_bps"])
        result["gold_rate_channel"] = {
            "primary_series": "US10Y_REAL",
            "move_4d_bps": real_move,
            "direction": "rising" if real_move >= 5 else "falling" if real_move <= -5 else "mixed",
            "causal_permission": "context_only",
        }
    return result


def read_mt5(now: datetime) -> dict[str, Any]:
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise RuntimeError("MT5_INIT_FAILED")
    try:
        selected = choose_rate_symbols(list(mt5.symbols_get() or []))
        observations: dict[str, Any] = {}
        rejected: dict[str, Any] = {}
        for label, symbol in selected.items():
            mt5.symbol_select(symbol, True)
            tick = mt5.symbol_info_tick(symbol)
            broker_offset = broker_utc_offset_hours(getattr(tick, "time", None), now)
            rows = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 1, 25)
            observation = observation_from_rows(label, symbol, rows, now, broker_offset) if rows is not None else None
            if observation:
                observations[label] = observation
            else:
                rejected[label] = {"symbol": symbol, **bar_diagnostics(rows, now, 60, broker_offset)}
        if not observations:
            return {
                "available": False,
                "reason": "BROKER_YIELD_SYMBOLS_NOT_FOUND" if not selected else "BROKER_YIELD_DATA_REJECTED",
                "selected_symbols": selected,
                "rejected_observations": rejected,
                "scoring_applied": False,
            }
        return aggregate_observations(observations, "intraday_live")
    finally:
        mt5.shutdown()


def parse_fred_csv(data: bytes, series_id: str) -> list[tuple[datetime, float]]:
    output: list[tuple[datetime, float]] = []
    for row in csv.DictReader(io.StringIO(data.decode("utf-8-sig"))):
        date_text = str(row.get("DATE") or row.get("observation_date") or "").strip()
        value_text = str(row.get(series_id) or "").strip()
        if not date_text or value_text in {"", "."}:
            continue
        try:
            timestamp = datetime.fromisoformat(date_text).replace(tzinfo=timezone.utc)
            value = float(value_text)
        except ValueError:
            continue
        output.append((timestamp, value))
    return output


def fetch_fred_bundle() -> dict[str, list[tuple[datetime, float]]]:
    series_ids = list(FRED_SERIES.values())
    url = FRED_CSV_URL + "?" + urllib.parse.urlencode({"id": ",".join(series_ids)})
    request = urllib.request.Request(url, headers={"User-Agent": "MarketRadar/1.0", "Accept": "text/csv"})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        data = response.read()
    return {series_id: parse_fred_csv(data, series_id) for series_id in series_ids}


def read_fred(now: datetime) -> dict[str, Any]:
    observations: dict[str, Any] = {}
    errors: dict[str, str] = {}
    try:
        bundle = fetch_fred_bundle()
    except (OSError, TimeoutError, urllib.error.URLError, ValueError) as exc:
        return {
            "available": False,
            "reason": "FRED_UNAVAILABLE",
            "provider_errors": {"FRED": type(exc).__name__},
            "intraday_reaction_capable": False,
            "scoring_applied": False,
        }
    for label, series_id in FRED_SERIES.items():
        series = bundle.get(series_id) or []
        if len(series) < 5:
            errors[label] = "INSUFFICIENT_DAILY_DATA"
            continue
        timestamp, latest = series[-1]
        freshness = daily_series_freshness(timestamp, now)
        if not freshness["usable"]:
            errors[label] = "STALE_DAILY_DATA"
            continue
        change_bps = (latest - series[-5][1]) * 100.0
        observations[label] = {
            "label": label, "series_id": series_id, "value_percent": round(latest, 5),
            "move_4d_bps": round(change_bps, 3),
            "normalized_4h": 1.0 if change_bps >= 5 else -1.0 if change_bps <= -5 else 0.0,
            "timeframe": "D1", "last_observation_utc": timestamp.isoformat(), "freshness": freshness,
            "measurement_window": "4 trading days",
            "source": "Federal Reserve Economic Data (FRED)", "source_url": FRED_CSV_URL,
            "source_kind": "official_daily_series", "causal_permission": "context_only",
        }
    context = aggregate_observations(observations, "official_daily_fallback")
    context.update(daily_rate_structure(observations))
    context["provider_errors"] = errors
    context["intraday_reaction_capable"] = False
    context["limitation"] = "Daily yields provide macro context only and cannot verify an intraday news reaction."
    return context


def cached_context(cache: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    context = cache.get("context") if isinstance(cache.get("context"), dict) else {}
    original_mode = str(context.get("data_mode") or "")
    max_age = timedelta(hours=6) if original_mode == "official_daily_fallback" else timedelta(hours=3)
    freshness = cache_freshness(cache.get("generated_at_utc"), now, max_age)
    if not freshness["usable"] or not context.get("available"):
        return None
    result = dict(context)
    result.update({
        "data_mode": "official_daily_cache" if original_mode == "official_daily_fallback" else "intraday_cache",
        "cache_freshness": freshness, "intraday_reaction_capable": False,
        "limitation": "Cached rates cannot verify a new intraday reaction.",
    })
    return result


def collect_context(now: datetime) -> dict[str, Any]:
    mt5_error = None
    try:
        intraday = read_mt5(now)
    except Exception as exc:
        intraday, mt5_error = {"available": False}, type(exc).__name__
    if intraday.get("available"):
        return intraday
    cached = cached_context(load_json(CACHE_FILE), now)
    if cached:
        cached["intraday_failure"] = intraday.get("reason") or mt5_error
        return cached
    try:
        daily = read_fred(now)
    except Exception as exc:
        daily = {"available": False, "reason": type(exc).__name__}
    if daily.get("available"):
        daily["intraday_failure"] = intraday.get("reason") or mt5_error
        return daily
    return {
        "available": False, "reason": "RATES_UNAVAILABLE",
        "intraday_failure": intraday.get("reason") or mt5_error,
        "daily_failure": daily.get("reason") or "NO_USABLE_DAILY_SERIES",
        "daily_provider_errors": daily.get("provider_errors"),
        "scoring_applied": False,
    }


def main() -> int:
    now = datetime.now(timezone.utc)
    context = collect_context(now)
    payload = {"schema_version": 2, "generated_at_utc": now.isoformat(), "context": context}
    if context.get("available") and not str(context.get("data_mode") or "").endswith("cache"):
        atomic_write_json(CACHE_FILE, payload)
    atomic_write_json(OUTPUT_FILE, payload)
    print(f"RATES_CONTEXT: {str(context.get('direction') or 'NO_DATA').upper()} | mode={context.get('data_mode', 'unavailable')} | intraday={context.get('intraday_reaction_capable', False)}")
    print("RATES_CONTEXT_ENGINE: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
