from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from causal_mechanism_engine import propose_mechanisms
from causal_validation_engine import candidate_contract, validate_candidates
from driver_contract import build_driver_contract, transition_driver
from priced_in_engine import apply_priced_in_gate, evaluate_priced_in, no_material_reaction_evaluation
from reaction_window_engine import build_reaction_report, normalize_candles
from usd_breadth_engine import PAIR_SPECS


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def resolve_download_file(pack: Path, item: dict[str, Any]) -> Path | None:
    role = str(item.get("role") or "")
    symbol = str(item.get("symbol") or "")
    candidates = list((pack / "raw").glob(f"{role}__*__M15.csv"))
    if not candidates and symbol:
        safe_symbol = symbol.replace(".", "_")
        candidates = list((pack / "raw").glob(f"*__{safe_symbol}__M15.csv"))
    return candidates[0] if candidates else None


def indexed_downloads(pack: Path, manifest: dict[str, Any]) -> dict[str, tuple[str, Path]]:
    result: dict[str, tuple[str, Path]] = {}
    for item in manifest.get("downloads") or []:
        if not isinstance(item, dict):
            continue
        path = resolve_download_file(pack, item)
        if path:
            result[str(item.get("role") or "unknown")] = (str(item.get("symbol") or ""), path)
    return result


def synthetic_usd_breadth(pair_rows: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    closes: dict[str, dict[str, float]] = {}
    for pair, rows in pair_rows.items():
        values: dict[str, float] = {}
        for row in rows:
            timestamp = str(row.get("utc_time") or row.get("time") or "")
            try:
                close = float(row.get("close"))
            except (TypeError, ValueError):
                continue
            if timestamp and close > 0:
                values[timestamp] = close
        if values:
            closes[pair] = values
    if len(closes) < 3:
        raise ValueError("USD_BREADTH_REQUIRES_THREE_COMPONENTS")
    common = sorted(set.intersection(*(set(values) for values in closes.values())))
    if len(common) < 2:
        raise ValueError("USD_BREADTH_HAS_NO_COMMON_TIMESTAMPS")
    bases = {pair: closes[pair][common[0]] for pair in closes}
    previous = 100.0
    synthetic: list[dict[str, Any]] = []
    for timestamp in common:
        log_level = sum(
            PAIR_SPECS[pair] * math.log(closes[pair][timestamp] / bases[pair])
            for pair in closes
        ) / len(closes)
        level = 100.0 * math.exp(log_level)
        synthetic.append({
            "utc_time": timestamp, "open": previous, "high": max(previous, level),
            "low": min(previous, level), "close": level, "tick_volume": 1,
            "is_closed": True,
        })
        previous = level
    return synthetic


def build_assets(pack: Path, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed = indexed_downloads(pack, manifest)
    assets: dict[str, dict[str, Any]] = {}
    for channel in ("gold", "oil", "dxy"):
        if channel in indexed:
            symbol, path = indexed[channel]
            assets["usd" if channel == "dxy" else channel] = {
                "symbol": symbol, "source": "MT5 causal replay", "timeframe": "M15",
                "candles": read_csv(path),
            }
    if "usd" not in assets:
        pair_rows: dict[str, list[dict[str, Any]]] = {}
        for role, (_, path) in indexed.items():
            if not role.startswith("usd_"):
                continue
            pair = role[4:].upper()
            if pair in PAIR_SPECS:
                pair_rows[pair] = read_csv(path)
        assets["usd"] = {
            "symbol": "USD_BREADTH", "source": f"{len(pair_rows)}-pair synthetic breadth",
            "timeframe": "M15", "candles": synthetic_usd_breadth(pair_rows),
        }
    missing = [channel for channel in ("gold", "oil", "usd") if channel not in assets]
    if missing:
        raise ValueError("MISSING_REQUIRED_CHANNELS:" + ",".join(missing))
    return assets


def last_closed_time(rows: list[dict[str, Any]]) -> datetime:
    candles = normalize_candles(rows, "M15")
    if not candles:
        raise ValueError("NO_CLOSED_CANDLES")
    return candles[-1]["close_time_utc"]


def replay_as_of(assets: dict[str, dict[str, Any]]) -> datetime:
    return min(last_closed_time(asset["candles"]) for asset in assets.values())


def select_primary_evaluation(validation: dict[str, Any], mechanisms: dict[str, Any]) -> dict[str, Any]:
    primary_ids = {
        item.get("candidate_id") for item in mechanisms.get("candidates") or []
        if isinstance(item, dict) and item.get("primary")
    }
    evaluations = [item for item in validation.get("evaluations") or [] if isinstance(item, dict)]
    return next((item for item in evaluations if item.get("candidate_id") in primary_ids), evaluations[0] if evaluations else {})


def novelty_override(event: dict[str, Any], status: str | None, prior_reports: int) -> dict[str, Any]:
    if status == "repeated":
        return {"status": "repeated", "repeated_statement": True, "prior_verified_reports": max(1, prior_reports)}
    if status == "new":
        return {"status": "new", "first_verified_publication": True, "prior_verified_reports": 0}
    original = event.get("novelty_evidence")
    return original if isinstance(original, dict) else {"status": "unknown"}


def run_replay_pack(
    pack: Path,
    novelty_status: str | None = None,
    prior_verified_reports: int = 0,
    external_evidence: dict[str, bool] | None = None,
) -> dict[str, Any]:
    event = load_json(pack / "historical_event.json")
    manifest = load_json(pack / "manifest.json")
    event.setdefault("lifecycle_state", "reported_fact")
    event.setdefault("status", "released")
    assets = build_assets(pack, manifest)
    as_of = replay_as_of(assets)
    reaction = build_reaction_report(event, assets, as_of)
    contract = build_driver_contract(event, now=as_of)
    mechanisms = propose_mechanisms(event)
    validation = validate_candidates(contract, mechanisms, reaction, external_evidence)
    primary = select_primary_evaluation(validation, mechanisms)
    novelty = novelty_override(event, novelty_status, prior_verified_reports)
    if validation.get("next_gate") == "reaction_window_expired":
        priced_in = no_material_reaction_evaluation(contract, primary)
    elif mechanisms.get("release_status") == "in_line":
        priced_in = {
            "schema_version": 1,
            "event_id": contract.get("driver_id"),
            "candidate_id": None,
            "classification": "no_directional_surprise",
            "causal_role": "context_only",
            "reason_ru": "Опубликованные компоненты совпали с прогнозом; направленного макросюрприза нет.",
            "novelty": novelty,
            "causal_validation_ready": False,
            "driver_confirmation_allowed": False,
            "origin_claim_allowed": False,
            "permissions": {
                "confirm_driver": False, "entry": False, "direction_switch": False,
                "score": False, "technical_invalidation_override": False,
            },
        }
    else:
        priced_in = evaluate_priced_in(contract, primary, reaction, novelty)
    candidate = candidate_contract(contract, primary)
    final_contract = apply_priced_in_gate(candidate, priced_in)
    if priced_in.get("classification") == "no_material_reaction":
        final_contract = transition_driver(final_contract, "expired", "reaction_window_expired", as_of)
    result = {
        "schema_version": 1, "event_id": event.get("event_id"), "as_of_utc": as_of.isoformat(),
        "classification": priced_in.get("classification"), "causal_role": priced_in.get("causal_role"),
        "primary_mechanism": primary.get("mechanism_id"), "primary_alignment": primary.get("alignment"),
        "hard_conflicts": primary.get("hard_conflicts") or [],
        "next_gate": validation.get("next_gate"),
        "final_lifecycle": final_contract.get("lifecycle_state"),
        "driver_confirmation_allowed": bool(priced_in.get("driver_confirmation_allowed")),
        "origin_claim_allowed": bool(priced_in.get("origin_claim_allowed")),
        "causal_replay_ready": bool(manifest.get("causal_replay_ready")),
        "permissions": priced_in.get("permissions"),
    }
    atomic_json(pack / "reaction_windows.json", reaction)
    atomic_json(pack / "mechanism_candidates.json", mechanisms)
    atomic_json(pack / "causal_validation.json", validation)
    atomic_json(pack / "priced_in_evaluation.json", priced_in)
    atomic_json(pack / "historical_replay_result.json", result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full causal replay pipeline for one event pack.")
    parser.add_argument("pack")
    parser.add_argument("--novelty", choices=("new", "repeated", "unknown"), default=None)
    parser.add_argument("--prior-verified-reports", type=int, default=0)
    parser.add_argument("--evidence-json", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    evidence = load_json(Path(args.evidence_json)) if args.evidence_json else {}
    result = run_replay_pack(
        Path(args.pack), args.novelty, args.prior_verified_reports,
        {str(key): bool(value) for key, value in evidence.items()},
    )
    print(f"CAUSAL_REPLAY: {result['classification']} | role={result['causal_role']}")
    print(f"PRIMARY_MECHANISM: {result['primary_mechanism']} | {result['primary_alignment']}")
    print(f"FINAL_LIFECYCLE: {result['final_lifecycle']}")
    print("CAUSAL_REPLAY_RUNNER: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
