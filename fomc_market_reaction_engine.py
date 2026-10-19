from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from causal_replay_runner import synthetic_usd_breadth
from causal_validation_engine import MIN_MATERIAL_DISPLACEMENT, expected_relation, material_reaction, normalize_direction
from fomc_phase_engine import parse_time
from prepare_causal_replay_pack import select_market_symbols
from reaction_window_engine import average_true_range_percent, normalize_candles, window_metrics
from usd_breadth_engine import PAIR_SPECS


PLAN_FILE = Path("data/macro/fomc_phase_plan.json")
OUTPUT_FILE = Path("data/macro/fomc_market_reaction.json")
TIMEFRAME = "M15"
BAR_DURATION = timedelta(minutes=15)
SYNTHETIC_USD_MIN_ABSOLUTE_MOVE_PERCENT = 0.05


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


def mt5_rows(rates: Any, broker_offset_hours: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    # MT5 bar epochs are UTC. broker_offset_hours is retained in diagnostics
    # only; applying it here would shift the reaction away from the release.
    for row in list(rates) if rates is not None else []:
        opened = datetime.fromtimestamp(int(row["time"]), timezone.utc)
        result.append({
            "utc_time": opened.isoformat(), "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "tick_volume": float(row["tick_volume"]), "is_closed": True,
        })
    return result


def without_synthetic_volume(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove meaningless volume from a calculated USD breadth series."""
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["tick_volume"] = None
        item["volume"] = None
        cleaned.append(item)
    return cleaned


def plan_bounds(plan: dict[str, Any]) -> tuple[datetime, datetime]:
    windows = [window for phase in plan.get("phases") or [] for window in phase.get("windows") or []]
    starts = [value for item in windows if (value := parse_time(item.get("start_utc")))]
    ends = [value for item in windows if (value := parse_time(item.get("end_utc")))]
    if not starts or not ends:
        raise ValueError("FOMC_PHASE_WINDOWS_MISSING")
    return min(starts) - timedelta(hours=2), max(ends) + BAR_DURATION


def collect_mt5_assets(plan: dict[str, Any], broker_offset_hours: int = 2) -> tuple[dict[str, Any], dict[str, Any]]:
    import MetaTrader5 as mt5

    start, end = plan_bounds(plan)
    if not mt5.initialize():
        raise RuntimeError(f"MT5_INIT_FAILED:{mt5.last_error()}")
    try:
        selection = select_market_symbols(list(mt5.symbols_get() or []))
        if not selection.get("gold"):
            raise RuntimeError("GOLD_SYMBOL_NOT_FOUND")

        def download(symbol: str) -> list[dict[str, Any]]:
            if not mt5.symbol_select(symbol, True):
                return []
            return mt5_rows(mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, start, end), broker_offset_hours)

        assets: dict[str, Any] = {}
        gold_symbol = str(selection["gold"])
        gold_rows = download(gold_symbol)
        if not gold_rows:
            raise RuntimeError("GOLD_M15_HISTORY_UNAVAILABLE")
        assets["gold"] = {"symbol": gold_symbol, "source": "MT5 broker history", "timeframe": TIMEFRAME, "candles": gold_rows}

        usd_selection = selection.get("usd") or {}
        dxy_symbol = usd_selection.get("dxy")
        if dxy_symbol and (dxy_rows := download(str(dxy_symbol))):
            assets["usd"] = {"symbol": dxy_symbol, "source": "MT5 DXY history", "timeframe": TIMEFRAME, "candles": dxy_rows}
            usd_mode = "direct_dxy"
        else:
            pair_rows: dict[str, list[dict[str, Any]]] = {}
            for pair, symbol in (usd_selection.get("breadth") or {}).items():
                if pair in PAIR_SPECS and (rows := download(str(symbol))):
                    pair_rows[pair] = rows
            if len(pair_rows) >= 3:
                synthetic = without_synthetic_volume(synthetic_usd_breadth(pair_rows))
                assets["usd"] = {
                    "symbol": "USD_BREADTH", "source": f"{len(pair_rows)}-pair synthetic USD breadth",
                    "timeframe": TIMEFRAME, "candles": synthetic,
                }
                usd_mode = "synthetic_breadth"
            else:
                usd_mode = "unavailable"

        rate_symbols = selection.get("rates") or {}
        for label, symbol in rate_symbols.items():
            if rows := download(str(symbol)):
                assets[f"rates_{label.lower()}"] = {
                    "symbol": symbol, "label": label, "channel": "nominal_yields",
                    "source": "MT5 broker yield history", "timeframe": TIMEFRAME, "candles": rows,
                }
        diagnostics = {
            "selection": selection, "usd_mode": usd_mode,
            "yield_components": [key for key in assets if key.startswith("rates_")],
            "broker_offset_hours": broker_offset_hours,
            "download_window_utc": {"start": start.isoformat(), "end": end.isoformat()},
        }
        return assets, diagnostics
    finally:
        mt5.shutdown()


def metrics_for_window(candles: list[dict[str, Any]], start: datetime, end: datetime) -> dict[str, Any]:
    pre = [item for item in candles if item["close_time_utc"] <= start and item["close_time_utc"] > start - timedelta(minutes=60)]
    anchor = pre[-1] if pre else None
    selected = [item for item in candles if item["open_time_utc"] >= start and item["close_time_utc"] <= end]
    baseline_atr = average_true_range_percent(pre)
    volumes = [float(item["volume"]) for item in pre if item.get("volume") is not None]
    baseline_volume = sum(volumes) / len(volumes) if volumes else None
    result = window_metrics(selected, float(anchor["close"]) if anchor else None, baseline_atr, baseline_volume)
    expected_bars = round((end - start) / BAR_DURATION)
    complete = bool(anchor and len(selected) == expected_bars)
    result.update({
        "start_utc": start.isoformat(), "end_utc": end.isoformat(), "expected_bars": expected_bars,
        "complete": complete, "status": "complete" if complete else "incomplete_history",
    })
    return result


def channel_materiality(asset: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    """Apply an absolute noise floor to calculated USD breadth only."""
    atr_gate = material_reaction(metrics)
    is_synthetic_usd = str(asset.get("symbol") or "").upper() == "USD_BREADTH"
    try:
        absolute_move = abs(float(metrics.get("move_percent")))
    except (TypeError, ValueError):
        absolute_move = 0.0
    absolute_gate = absolute_move >= SYNTHETIC_USD_MIN_ABSOLUTE_MOVE_PERCENT if is_synthetic_usd else True
    material = bool(atr_gate and absolute_gate)
    if not atr_gate:
        reason = "below_relative_atr_threshold_or_incomplete"
    elif not absolute_gate:
        reason = "synthetic_usd_below_absolute_noise_floor"
    else:
        reason = "material_thresholds_passed"
    return {
        "material": material,
        "atr_gate_passed": atr_gate,
        "absolute_gate_passed": absolute_gate,
        "absolute_move_percent": round(absolute_move, 5),
        "absolute_floor_percent": SYNTHETIC_USD_MIN_ABSOLUTE_MOVE_PERCENT if is_synthetic_usd else None,
        "channel_quality": "synthetic_proxy" if is_synthetic_usd else "direct_market_series",
        "reason": reason,
    }


def channel_observation(asset: dict[str, Any], start: datetime, end: datetime, hypothesis: str) -> dict[str, Any]:
    raw = asset.get("candles") if isinstance(asset.get("candles"), list) else []
    candles = normalize_candles(raw, TIMEFRAME) if raw and "open_time_utc" not in raw[0] else raw
    metrics = metrics_for_window(candles, start, end)
    observed = normalize_direction(metrics.get("direction")) if metrics.get("available") else "unavailable"
    materiality = channel_materiality(asset, metrics)
    material = materiality["material"]
    return {
        "available": bool(metrics.get("available")), "symbol": asset.get("symbol"), "source": asset.get("source"),
        "hypothesized": hypothesis, "observed": observed,
        "relation": expected_relation(hypothesis, observed), "material": material,
        "materiality": materiality,
        "reaction_quality": "material" if material else "immaterial_or_incomplete",
        "metrics": metrics,
    }


def build_market_reaction(plan: dict[str, Any], assets: dict[str, Any], diagnostics: dict[str, Any] | None = None) -> dict[str, Any]:
    if not plan.get("available"):
        return {"schema_version": 1, "available": False, "reason": "FOMC_PHASE_PLAN_UNAVAILABLE"}
    reports: list[dict[str, Any]] = []
    rate_assets = {key: value for key, value in assets.items() if key.startswith("rates_")}
    for phase in plan.get("phases") or []:
        hypothesis = (phase.get("confirmation") or {}).get("hypothesized_transmission") or {}
        windows: list[dict[str, Any]] = []
        for spec in phase.get("windows") or []:
            if not spec.get("measurement_allowed"):
                windows.append({
                    "name": spec.get("name"), "status": "excluded_contaminated_window",
                    "phase_purity": spec.get("phase_purity"), "contaminated_by": spec.get("contaminated_by"),
                    "permissions": {"confirm_driver": False, "entry": False, "direction_switch": False, "score": False},
                })
                continue
            start, end = parse_time(spec.get("start_utc")), parse_time(spec.get("end_utc"))
            if not start or not end:
                continue
            observations: dict[str, Any] = {}
            if isinstance(assets.get("gold"), dict):
                observations["gold"] = channel_observation(assets["gold"], start, end, str(hypothesis.get("gold") or "unknown"))
            if isinstance(assets.get("usd"), dict):
                observations["usd"] = channel_observation(assets["usd"], start, end, str(hypothesis.get("usd") or "unknown"))
            rates = {
                key: channel_observation(asset, start, end, str(hypothesis.get("nominal_yields") or "unknown"))
                for key, asset in rate_assets.items()
            }
            aligned_rates = [value for value in rates.values() if value["relation"] == "aligned" and value["material"]]
            contradicted_rates = [value for value in rates.values() if value["relation"] == "contradicted" and value["material"]]
            observations["nominal_yields"] = {
                "available": bool(rates), "components": rates,
                "relation": "contradicted" if contradicted_rates else "aligned" if aligned_rates else "insufficient",
                "material": bool(aligned_rates or contradicted_rates),
                "reason": None if rates else "BROKER_INTRADAY_YIELDS_UNAVAILABLE",
            }
            gold = observations.get("gold") or {}
            transmission = [observations.get("usd") or {}, observations["nominal_yields"]]
            transmission_aligned = any(item.get("relation") == "aligned" and item.get("material") for item in transmission)
            hard_conflicts = [key for key, value in observations.items() if value.get("relation") == "contradicted" and value.get("material")]
            if hard_conflicts:
                alignment = "contradicted"
            elif gold.get("relation") == "aligned" and gold.get("material") and transmission_aligned:
                alignment = "market_aligned_candidate"
            elif (gold.get("relation") == "aligned" and gold.get("material")) or transmission_aligned:
                alignment = "materially_partially_aligned"
            elif gold.get("relation") == "aligned" or any(item.get("relation") == "aligned" for item in transmission):
                alignment = "directionally_aligned_but_immaterial"
            else:
                alignment = "insufficient_or_immaterial"
            windows.append({
                "name": spec.get("name"), "status": "measured", "phase_purity": spec.get("phase_purity"),
                "observations": observations, "alignment": alignment, "hard_conflicts": hard_conflicts,
                "confirmation_eligible_next_gate": alignment == "market_aligned_candidate",
                "permissions": {"confirm_driver": False, "entry": False, "direction_switch": False, "score": False},
            })
        reports.append({
            "phase_id": phase.get("phase_id"), "anchor_time_utc": phase.get("anchor_time_utc"),
            "policy_bias_candidate": phase.get("policy_bias_candidate"),
            "policy_bias_ru": phase.get("policy_bias_ru"),
            "gold_hypothesis_ru": phase.get("gold_hypothesis_ru"),
            "hypothesized_transmission": hypothesis,
            "windows": windows,
        })
    return {
        "schema_version": 1, "available": True, "meeting_id": plan.get("meeting_id"),
        "timeframe": TIMEFRAME, "materiality_threshold_atr": MIN_MATERIAL_DISPLACEMENT,
        "synthetic_usd_min_absolute_move_percent": SYNTHETIC_USD_MIN_ABSOLUTE_MOVE_PERCENT,
        "data_diagnostics": diagnostics or {}, "phases": reports,
        "policy": "Гипотеза и фактическая реакция разделены. Измерение рынка само по себе ещё не подтверждает причинность и не создаёт вход.",
        "permissions": {"confirm_driver": False, "entry": False, "direction_switch": False, "score": False},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure isolated FOMC market-reaction phases from MT5 M15 history.")
    parser.add_argument("--broker-offset-hours", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plan = load_json(PLAN_FILE)
    try:
        assets, diagnostics = collect_mt5_assets(plan, args.broker_offset_hours)
        payload = build_market_reaction(plan, assets, diagnostics)
    except Exception as exc:
        payload = {"schema_version": 1, "available": False, "reason": type(exc).__name__, "detail": str(exc)}
    atomic_json(OUTPUT_FILE, payload)
    print(f"FOMC_MARKET_REACTION: available={payload.get('available')} | phases={len(payload.get('phases') or [])}")
    print(f"FOMC_MARKET_REACTION_SAVED: {OUTPUT_FILE}")
    print("FOMC_MARKET_REACTION_ENGINE_C2_5B: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
