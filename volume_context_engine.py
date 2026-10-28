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
RAW_DIR = DATA_DIR / "raw" / SYMBOL_FILE
OUTPUT_FILE = DATA_DIR / "market_context" / f"{SYMBOL_FILE}_volume.json"
TIMEFRAMES = ("M5", "M15", "H1", "H4", "D1", "W1")
MAX_CLOSED_BAR_AGE_HOURS = {
    "M5": 0.5,
    "M15": 1.0,
    "H1": 3.0,
    "H4": 10.0,
    # Daily/weekly tolerances intentionally include market weekends.
    "D1": 96.0,
    "W1": 240.0,
}


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


def normalize_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        values = {key: finite(row.get(key)) for key in ("open", "high", "low", "close", "tick_volume")}
        if any(values[key] is None for key in ("open", "high", "low", "close")):
            continue
        normalized: dict[str, Any] = {key: float(value or 0.0) for key, value in values.items()}
        normalized["time"] = row.get("utc_time") or row.get("broker_time") or row.get("time")
        # Closure is an explicit acquisition fact, not proof of availability.
        normalized["is_closed"] = row.get("is_closed") is True or str(row.get("is_closed")).lower() == "true"
        output.append(normalized)
    return output


def read_rows(path: Path) -> list[dict[str, float]]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return normalize_rows(csv.DictReader(handle))
    except OSError:
        return []


def median(values: list[float]) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2.0


def bar_metrics(row: dict[str, float]) -> tuple[float, float, float]:
    spread = max(0.0, row["high"] - row["low"])
    body = abs(row["close"] - row["open"])
    efficiency = body / spread if spread > 0 else 0.0
    direction = 1.0 if row["close"] > row["open"] else -1.0 if row["close"] < row["open"] else 0.0
    return spread, efficiency, direction


def classify_timeframe(rows: list[dict[str, float]], timeframe: str) -> dict[str, Any]:
    # Raw snapshots include the candle that is still forming. Comparing that
    # candle with completed history produces false "low volume" readings,
    # especially early in H1/H4. Structural volume therefore uses the last
    # completed candle; live M5/M15 activity is handled by market_pulse_engine.
    if len(rows) < 25:
        return {"available": False, "reason": "INSUFFICIENT_BARS", "timeframe": timeframe}
    closed_only = all(row.get("is_closed") is True for row in rows)
    baseline = rows[-21:-1] if closed_only else rows[-22:-2]
    current = rows[-1] if closed_only else rows[-2]
    spread, efficiency, direction_sign = bar_metrics(current)
    median_volume = median([item["tick_volume"] for item in baseline])
    median_spread = median([bar_metrics(item)[0] for item in baseline])
    volume_ratio = current["tick_volume"] / median_volume if median_volume > 0 else 0.0
    spread_ratio = spread / median_spread if median_spread > 0 else 0.0
    direction = "bullish" if direction_sign > 0 else "bearish" if direction_sign < 0 else "neutral"

    # Read effort/result as a sequence, not as one isolated candle.  The most
    # recent three closed bars are compared with the preceding five-bar push.
    drive = rows[-9:-4] if closed_only else rows[-10:-5]
    recent = rows[-4:-1] if closed_only else rows[-5:-2]
    drive_volume = sum(item["tick_volume"] for item in drive) / len(drive)
    recent_volume = sum(item["tick_volume"] for item in recent) / len(recent)
    drive_result = sum(abs(item["close"] - item["open"]) for item in drive) / len(drive)
    recent_result = sum(abs(item["close"] - item["open"]) for item in recent) / len(recent)
    participation_change = recent_volume / drive_volume if drive_volume > 0 else 0.0
    result_change = recent_result / drive_result if drive_result > 0 else 0.0
    if participation_change <= 0.75 and result_change <= 0.80:
        sequence = "fading_effort"
    elif participation_change >= 1.20 and result_change <= 0.75:
        sequence = "absorption_building"
    elif participation_change >= 1.20 and result_change >= 1.15:
        sequence = "expanding_participation"
    elif participation_change <= 0.85 and result_change >= 1.10:
        sequence = "thin_liquidity_move"
    else:
        sequence = "stable"

    if volume_ratio >= 1.5 and spread_ratio >= 1.35 and efficiency >= 0.55:
        state = "expansion"
    elif volume_ratio >= 1.5 and spread_ratio <= 0.85:
        state = "possible_absorption"
    elif volume_ratio >= 1.35 and efficiency <= 0.3:
        state = "possible_exhaustion"
    elif volume_ratio <= 0.75 and spread_ratio <= 1.0:
        state = "low_volume_pullback"
    elif volume_ratio >= 1.2 and spread_ratio >= 1.0:
        state = "high_volume_acceptance_candidate"
    else:
        state = "balanced"

    return {
        "available": True,
        "timeframe": timeframe,
        "state": state,
        "direction": direction,
        "tick_volume_ratio": round(volume_ratio, 2),
        "spread_ratio": round(spread_ratio, 2),
        "body_efficiency": round(efficiency, 2),
        "effort_result": "aligned" if (volume_ratio >= 1.2 and spread_ratio >= 1.0) else "effort_without_result" if (volume_ratio >= 1.5 and spread_ratio < 1.0) else "neutral",
        "sequence": sequence,
        "participation_change": round(participation_change, 2),
        "result_change": round(result_change, 2),
        "sequence_window": "5 closed bars -> 3 closed bars",
        "proxy": "broker_tick_volume",
        "bar_status": "closed",
        "closed_bar_time": current.get("time"),
        "observation_id": f"{timeframe}:{current.get('time') or 'closed-2'}",
    }


def apply_freshness(observation: dict[str, Any], now: datetime) -> dict[str, Any]:
    result = dict(observation)
    if not result.get("available"):
        return result
    raw_time = result.get("closed_bar_time")
    if not raw_time:
        result["freshness_status"] = "unknown"
        return result
    try:
        closed = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
        if closed.tzinfo is None:
            closed = closed.replace(tzinfo=timezone.utc)
        age_hours = max(0.0, (now - closed.astimezone(timezone.utc)).total_seconds() / 3600.0)
    except (TypeError, ValueError):
        result["freshness_status"] = "unknown"
        return result
    maximum = MAX_CLOSED_BAR_AGE_HOURS.get(str(result.get("timeframe")), 24.0)
    result["age_hours"] = round(age_hours, 2)
    result["max_age_hours"] = maximum
    if age_hours > maximum:
        result["available"] = False
        result["reason"] = "STALE_CLOSED_BAR"
        result["freshness_status"] = "stale"
    else:
        result["freshness_status"] = "fresh"
    return result


def aggregate_context(timeframes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    valid = [item for item in timeframes.values() if item.get("available")]
    notable = [item for item in valid if item.get("state") != "balanced"]
    if not valid:
        return {"available": False, "reason": "NO_VOLUME_DATA", "scoring_applied": False}
    directions = [item["direction"] for item in notable if item.get("direction") in {"bullish", "bearish"}]
    bullish = directions.count("bullish")
    bearish = directions.count("bearish")
    direction = "bullish" if bullish > bearish else "bearish" if bearish > bullish else "mixed"
    higher = [timeframes.get(name, {}) for name in ("H1", "H4", "D1", "W1")]
    lower = [timeframes.get(name, {}) for name in ("M5", "M15")]
    higher_direction = next((item.get("direction") for item in reversed(higher) if item.get("available") and item.get("state") != "balanced"), "mixed")
    lower_direction = next((item.get("direction") for item in lower if item.get("available") and item.get("state") != "balanced"), "mixed")
    relation = "aligned" if higher_direction == lower_direction and higher_direction != "mixed" else "conflict" if {higher_direction, lower_direction} == {"bullish", "bearish"} else "unconfirmed"
    return {
        "available": True,
        "direction": direction,
        "lower_vs_higher": relation,
        "notable_states": [f"{item['timeframe']}:{item['state']}" for item in notable],
        "interpretation": "Context only. Volume does not confirm an entry by itself.",
        "scoring_applied": False,
        "telegram_eligible": False,
    }


def build_payload(
    frames: dict[str, list[dict[str, float]]],
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = now or datetime.now(timezone.utc)
    observations = {
        timeframe: apply_freshness(
            classify_timeframe(frames.get(timeframe, []), timeframe), generated_at,
        )
        for timeframe in TIMEFRAMES
    }
    return {
        "schema_version": 1,
        "generated_at_utc": generated_at.isoformat(),
        "symbol": SYMBOL,
        "source": "broker tick volume",
        "warning": "Tick volume is a broker activity proxy, not centralized traded volume.",
        "timeframes": observations,
        "volume_context": aggregate_context(observations),
    }


def main() -> int:
    frames = {timeframe: read_rows(RAW_DIR / f"{timeframe}.csv") for timeframe in TIMEFRAMES}
    payload = build_payload(frames)
    atomic_write_json(OUTPUT_FILE, payload)
    context = payload["volume_context"]
    if not context.get("available"):
        print(f"VOLUME_CONTEXT: NO_DATA | reason={context.get('reason')}")
    else:
        notable = ",".join(context.get("notable_states") or []) or "balanced"
        print(f"VOLUME_CONTEXT: {str(context.get('direction')).upper()} | states={notable} | score_impact=0 | alert=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
