from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


TIMEFRAME_DURATION = {
    "M5": timedelta(minutes=5),
    "M15": timedelta(minutes=15),
    "H1": timedelta(hours=1),
}
PRE_WINDOWS = {
    "pre_24h": timedelta(hours=24),
    "pre_4h": timedelta(hours=4),
    "pre_60m": timedelta(minutes=60),
}
POST_WINDOWS = {
    "post_15m": timedelta(minutes=15),
    "post_60m": timedelta(minutes=60),
    "post_4h": timedelta(hours=4),
    "post_24h": timedelta(hours=24),
}


def parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromtimestamp(float(text), tz=timezone.utc)
        except (OverflowError, OSError, TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def is_closed(value: Any) -> bool:
    return str(value if value is not None else "true").strip().lower() in {"true", "1", "yes"}


def normalize_candles(rows: list[dict[str, Any]], timeframe: str) -> list[dict[str, Any]]:
    duration = TIMEFRAME_DURATION[timeframe]
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not is_closed(row.get("is_closed")):
            continue
        opened = parse_time(row.get("utc_time") or row.get("time"))
        prices = {name: as_float(row.get(name)) for name in ("open", "high", "low", "close")}
        if not opened or any(value is None for value in prices.values()):
            continue
        volume = as_float(row.get("tick_volume") or row.get("volume") or row.get("real_volume"))
        normalized.append({
            "open_time_utc": opened,
            "close_time_utc": opened + duration,
            **prices,
            "volume": volume,
        })
    normalized.sort(key=lambda item: item["open_time_utc"])
    return normalized


def load_csv(path: Path, timeframe: str) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return normalize_candles(list(csv.DictReader(handle)), timeframe)


def stable_event_id(event: dict[str, Any]) -> str:
    supplied = str(event.get("event_id") or event.get("driver_id") or "").strip()
    if supplied:
        return supplied
    identity = "|".join((
        str(event.get("title") or "unknown").strip().lower(),
        str(event.get("event_time_utc") or ""),
        str(event.get("source") or "unknown").strip().lower(),
    ))
    return "evt_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def average_true_range_percent(candles: list[dict[str, Any]]) -> float | None:
    ranges = [
        (float(item["high"]) - float(item["low"])) / float(item["open"]) * 100.0
        for item in candles if float(item["open"]) > 0
    ]
    return sum(ranges) / len(ranges) if ranges else None


def window_metrics(
    candles: list[dict[str, Any]],
    anchor_price: float | None,
    baseline_atr_percent: float | None,
    baseline_volume: float | None,
) -> dict[str, Any]:
    if not candles:
        return {"available": False, "bar_count": 0}
    first, last = candles[0], candles[-1]
    start_price = anchor_price if anchor_price and anchor_price > 0 else float(first["open"])
    close = float(last["close"])
    highest = max(float(item["high"]) for item in candles)
    lowest = min(float(item["low"]) for item in candles)
    move_percent = (close / start_price - 1.0) * 100.0
    high_excursion = (highest / start_price - 1.0) * 100.0
    low_excursion = (lowest / start_price - 1.0) * 100.0
    volumes = [float(item["volume"]) for item in candles if item.get("volume") is not None]
    average_volume = sum(volumes) / len(volumes) if volumes else None
    volume_ratio = average_volume / baseline_volume if average_volume is not None and baseline_volume and baseline_volume > 0 else None
    displacement_ratio = abs(move_percent) / baseline_atr_percent if baseline_atr_percent and baseline_atr_percent > 0 else None
    return {
        "available": True,
        "bar_count": len(candles),
        "first_bar_open_utc": first["open_time_utc"].isoformat(),
        "last_bar_close_utc": last["close_time_utc"].isoformat(),
        "start_price": round(start_price, 6),
        "close_price": round(close, 6),
        "move_percent": round(move_percent, 5),
        "direction": "bullish" if move_percent > 0 else "bearish" if move_percent < 0 else "flat",
        "high_excursion_percent": round(high_excursion, 5),
        "low_excursion_percent": round(low_excursion, 5),
        "range_percent": round((highest - lowest) / start_price * 100.0, 5),
        "average_volume": round(average_volume, 3) if average_volume is not None else None,
        "volume_ratio_to_pre60": round(volume_ratio, 4) if volume_ratio is not None else None,
        "displacement_ratio_to_pre_atr": round(displacement_ratio, 4) if displacement_ratio is not None else None,
    }


def measure_asset_reaction(
    candles: list[dict[str, Any]],
    timeframe: str,
    event_time: datetime,
    as_of: datetime,
    symbol: str,
    source: str,
) -> dict[str, Any]:
    duration = TIMEFRAME_DURATION[timeframe]
    event_time = event_time.astimezone(timezone.utc)
    as_of = as_of.astimezone(timezone.utc)
    visible = [item for item in candles if item["close_time_utc"] <= as_of]
    pre_closed = [item for item in visible if item["close_time_utc"] <= event_time]
    anchor = pre_closed[-1] if pre_closed else None
    anchor_price = float(anchor["close"]) if anchor else None
    post_start_candidates = [item for item in visible if item["open_time_utc"] >= event_time]
    reaction_start = post_start_candidates[0]["open_time_utc"] if post_start_candidates else None
    precision_delay = (reaction_start - event_time).total_seconds() if reaction_start else None
    market_closed_at_event = bool(precision_delay is not None and precision_delay > duration.total_seconds())
    opening_gap_percent = None
    if reaction_start and anchor_price and anchor_price > 0:
        first_post = post_start_candidates[0]
        opening_gap_percent = (float(first_post["open"]) / anchor_price - 1.0) * 100.0

    pre60_candles = [
        item for item in pre_closed
        if item["close_time_utc"] > event_time - PRE_WINDOWS["pre_60m"]
    ]
    baseline_atr = average_true_range_percent(pre60_candles)
    baseline_volumes = [float(item["volume"]) for item in pre60_candles if item.get("volume") is not None]
    baseline_volume = sum(baseline_volumes) / len(baseline_volumes) if baseline_volumes else None

    pre: dict[str, Any] = {}
    for name, window in PRE_WINDOWS.items():
        selected = [item for item in pre_closed if item["close_time_utc"] > event_time - window]
        metrics = window_metrics(selected, None, None, None)
        metrics.update({
            "window_start_utc": (event_time - window).isoformat(),
            "window_end_utc": event_time.isoformat(),
            "complete": bool(selected and selected[0]["open_time_utc"] <= event_time - window + duration),
        })
        pre[name] = metrics

    post: dict[str, Any] = {}
    for name, window in POST_WINDOWS.items():
        # The publication clock and the tradable reaction clock are separate.
        # During rollover/weekends, measuring from wall-clock event time would
        # permanently mark short windows as empty even though the market could
        # not react. Start measurement at the first clean tradable bar instead.
        window_start = reaction_start
        window_end = reaction_start + window if reaction_start else event_time + window
        selected = [
            item for item in visible
            if window_start and item["open_time_utc"] >= window_start and item["close_time_utc"] <= window_end
        ]
        metrics = window_metrics(selected, anchor_price, baseline_atr, baseline_volume)
        completed_by_clock = bool(window_start and as_of >= window_end)
        coverage_gap = (
            max(0.0, (window_end - selected[-1]["close_time_utc"]).total_seconds())
            if selected else None
        )
        covered_by_data = bool(selected and coverage_gap is not None and coverage_gap < duration.total_seconds())
        metrics.update({
            "event_time_utc": event_time.isoformat(),
            "window_start_utc": window_start.isoformat() if window_start else None,
            "window_end_utc": window_end.isoformat(),
            "complete": completed_by_clock and covered_by_data,
            "coverage_gap_seconds": round(coverage_gap) if coverage_gap is not None else None,
            "status": "complete" if completed_by_clock and covered_by_data else "developing" if selected else "pending",
        })
        post[name] = metrics

    return {
        "symbol": symbol,
        "source": source,
        "timeframe": timeframe,
        "resolution_seconds": round(duration.total_seconds()),
        "as_of_utc": as_of.isoformat(),
        "anchor": {
            "available": anchor is not None,
            "price": round(anchor_price, 6) if anchor_price is not None else None,
            "last_pre_event_close_utc": anchor["close_time_utc"].isoformat() if anchor else None,
        },
        "reaction_start_utc": reaction_start.isoformat() if reaction_start else None,
        "measurement_delay_seconds": round(precision_delay) if precision_delay is not None else None,
        "market_closed_at_event": market_closed_at_event,
        "session_delay_class": (
            "weekend_or_extended_closure" if precision_delay is not None and precision_delay >= 6 * 3600
            else "rollover_or_session_break" if market_closed_at_event
            else "normal_bar_alignment" if reaction_start else "no_tradable_data_yet"
        ),
        "opening_gap_percent": round(opening_gap_percent, 5) if opening_gap_percent is not None else None,
        "precision": "high" if timeframe == "M5" else "medium" if timeframe == "M15" else "coarse",
        "pre_event": pre,
        "post_event": post,
        "permissions": {"causal_interpretation": False, "entry": False, "direction_switch": False, "score": False},
    }


def build_reaction_report(
    event: dict[str, Any],
    assets: dict[str, dict[str, Any]],
    as_of: datetime,
) -> dict[str, Any]:
    event_time = parse_time(event.get("event_time_utc") or event.get("published_at_utc"))
    if not event_time:
        raise ValueError("Event requires a valid event_time_utc or published_at_utc")
    reports: dict[str, Any] = {}
    for name, asset in assets.items():
        timeframe = str(asset.get("timeframe") or "M15").upper()
        if timeframe not in TIMEFRAME_DURATION:
            raise ValueError(f"Unsupported reaction timeframe: {timeframe}")
        raw_candles = asset.get("candles") if isinstance(asset.get("candles"), list) else []
        candles = normalize_candles(raw_candles, timeframe) if raw_candles and "open_time_utc" not in raw_candles[0] else raw_candles
        reports[name] = measure_asset_reaction(
            candles, timeframe, event_time, as_of,
            str(asset.get("symbol") or name), str(asset.get("source") or "unknown"),
        )
    return {
        "schema_version": 1,
        "event_id": stable_event_id(event),
        "title": event.get("title"),
        "event_time_utc": event_time.isoformat(),
        "as_of_utc": as_of.astimezone(timezone.utc).isoformat(),
        "assets": reports,
        "causal_interpretation_allowed": False,
        "future_data_policy": "Only bars closed at or before as_of_utc are visible.",
    }


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure pre/post event reaction without causal interpretation.")
    parser.add_argument("--event-time", required=True)
    parser.add_argument("--title", default="Manual event")
    parser.add_argument("--event-id", default="")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--symbol", default="GOLD.pro")
    parser.add_argument("--timeframe", choices=tuple(TIMEFRAME_DURATION), default="M15")
    parser.add_argument("--as-of", default="")
    parser.add_argument("--output", default="data/news/reaction_windows.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    as_of = parse_time(args.as_of) if args.as_of else datetime.now(timezone.utc)
    if not as_of:
        raise ValueError("Invalid --as-of")
    candles = load_csv(Path(args.csv), args.timeframe)
    report = build_reaction_report(
        {"event_id": args.event_id, "title": args.title, "event_time_utc": args.event_time},
        {args.symbol: {"symbol": args.symbol, "source": "CSV", "timeframe": args.timeframe, "candles": candles}},
        as_of,
    )
    atomic_json(Path(args.output), report)
    asset = report["assets"][args.symbol]
    print(f"REACTION_WINDOWS: {args.symbol} | tf={args.timeframe} | post60={asset['post_event']['post_60m']['status']}")
    print(f"REACTION_WINDOWS_SAVED: {args.output}")
    print("REACTION_WINDOW_ENGINE: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
