from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd


SYMBOL = "GOLD.pro"
SYMBOL_FILE = SYMBOL.replace(".", "_")
TIMEFRAMES = ("W1", "D1", "H4", "H1", "M15")
DURATIONS = {
    "W1": pd.Timedelta(days=7),
    "D1": pd.Timedelta(days=1),
    "H4": pd.Timedelta(hours=4),
    "H1": pd.Timedelta(hours=1),
    "M15": pd.Timedelta(minutes=15),
}
PIPELINE = (
    "market_features.py",
    "key_levels.py",
    "dealing_range_engine.py",
    "level_state.py",
    "order_flow_structure.py",
    "volume_context_engine.py",
    "scenario_state.py",
    "trade_idea_engine.py",
    "event_monitor.py",
    "alert_policy.py",
    "notification_builder.py",
)


def load_json(path: Path, required: bool = True) -> dict[str, Any]:
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"Missing required file: {path}")
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_frames(raw_dir: Path) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for timeframe in TIMEFRAMES:
        path = raw_dir / f"{timeframe}.csv"
        if not path.is_file():
            raise FileNotFoundError(f"Missing replay source: {path}")
        data = pd.read_csv(path)
        time_column = "utc_time" if "utc_time" in data.columns else "time"
        if time_column == "time" and pd.api.types.is_numeric_dtype(data[time_column]):
            data["_time"] = pd.to_datetime(data[time_column], unit="s", utc=True, errors="coerce")
        else:
            data["_time"] = pd.to_datetime(data[time_column], utc=True, errors="coerce")
        data = data.dropna(subset=["_time"]).sort_values("_time").reset_index(drop=True)
        frames[timeframe] = data
    return frames


def replay_checkpoints(
    driver: pd.DataFrame,
    step: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> list[pd.Timestamp]:
    duration = DURATIONS[step]
    opens = driver["_time"]
    selected = opens[(opens >= start) & (opens < end + pd.Timedelta(days=1))]
    return [value + duration for value in selected]


def closed_slice(data: pd.DataFrame, timeframe: str, checkpoint: pd.Timestamp) -> pd.DataFrame:
    closed = data[data["_time"] + DURATIONS[timeframe] <= checkpoint].copy()
    return closed.drop(columns=["_time"], errors="ignore")


def write_snapshot(
    frames: dict[str, pd.DataFrame],
    destination: Path,
    checkpoint: pd.Timestamp,
    broker_offset_hours: int,
    bars_per_timeframe: int,
) -> dict[str, int]:
    destination.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for timeframe in TIMEFRAMES:
        snapshot = closed_slice(frames[timeframe], timeframe, checkpoint)
        snapshot = snapshot.tail(bars_per_timeframe).reset_index(drop=True)
        if len(snapshot) < 7:
            raise RuntimeError(f"Not enough closed {timeframe} bars at {checkpoint.isoformat()}")
        if "broker_time" not in snapshot.columns:
            utc = pd.to_datetime(snapshot["utc_time"], utc=True, errors="coerce")
            snapshot["broker_time"] = (
                utc + pd.Timedelta(hours=broker_offset_hours)
            ).map(lambda value: value.isoformat())
        snapshot["snapshot_utc"] = checkpoint.isoformat()
        snapshot.to_csv(destination / f"{timeframe}.csv", index=False)
        counts[timeframe] = int(len(snapshot))
    return counts


def write_replay_session(runtime_dir: Path, checkpoint: pd.Timestamp) -> None:
    # Session scoring is intentionally disabled in the first deterministic
    # replay. The current-time session engine must never contaminate history.
    payload = {
        "schema_version": 1,
        "generated_at_utc": checkpoint.isoformat(),
        "symbol": SYMBOL,
        "active_sessions": [],
        "replay_mode": True,
        "note": "Session context disabled for deterministic D1 replay",
    }
    atomic_json(runtime_dir / "data" / "sessions" / f"{SYMBOL_FILE}.json", payload)


def run_pipeline(project_root: Path, runtime_dir: Path, timeout: int) -> tuple[bool, str]:
    for script in PIPELINE:
        path = project_root / script
        if not path.is_file():
            return False, f"missing script: {path}"
        try:
            result = subprocess.run(
                [sys.executable, str(path)],
                cwd=runtime_dir,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False, f"{script} timed out after {timeout}s"
        if result.returncode != 0:
            output = "\n".join(part for part in (result.stdout, result.stderr) if part)
            return False, f"{script} exit={result.returncode}\n{output}"
    return True, ""


def zone_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not value:
        return None
    return {
        "name": value.get("name"),
        "low": value.get("low"),
        "high": value.get("high"),
        "state": value.get("h1_state") or value.get("state"),
    }


def target_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not value:
        return None
    return {"name": value.get("name"), "price": value.get("price")}


def timeline_item(
    checkpoint: pd.Timestamp,
    scenario: dict[str, Any],
    event_payload: dict[str, Any],
    counts: dict[str, int],
    policy_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    primary = scenario.get("primary_scenario")
    primary = primary if isinstance(primary, dict) else {}
    structure = scenario.get("order_flow_context")
    structure = structure if isinstance(structure, dict) else {}
    phases = structure.get("phases") if isinstance(structure.get("phases"), dict) else {}
    market = scenario.get("market_state") if isinstance(scenario.get("market_state"), dict) else {}
    structural = market.get("primary_structural_leg") if isinstance(market.get("primary_structural_leg"), dict) else {}
    counter = market.get("active_counter_leg") if isinstance(market.get("active_counter_leg"), dict) else {}
    opportunity = market.get("opportunity") if isinstance(market.get("opportunity"), dict) else {}
    policy_payload = policy_payload or {}
    return {
        "checkpoint_utc": checkpoint.isoformat(),
        "price": scenario.get("current_price"),
        "global_bias": scenario.get("global_bias"),
        "primary": {
            "direction": primary.get("direction"),
            "status": primary.get("status", "silent"),
            "score": int(primary.get("score") or 0),
            "target": target_summary(primary.get("point_b")),
            "decision": zone_summary(primary.get("next_decision_zone")),
            "current_reaction": zone_summary(primary.get("current_reaction_zone")),
            "deeper_pullback": zone_summary(primary.get("pullback_zone")),
            "conditions": primary.get("conditions") or {},
        },
        "structure": {
            "weekly_narrative": structure.get("weekly_narrative"),
            "delivery_status": structure.get("delivery_status"),
            "structure_status": structure.get("structure_status"),
            "phases": phases,
        },
        "events": event_payload.get("events") or [],
        "should_notify": bool(policy_payload.get("should_notify")),
        "notification_type": policy_payload.get("notification_type"),
        "alert_lifecycle_key": ((policy_payload.get("current_snapshot") or {}).get("alert_lifecycle_key")) if isinstance(policy_payload.get("current_snapshot"), dict) else None,
        "opportunity": opportunity.get("state"),
        "structural_leg": {
            "lifecycle_id": structural.get("lifecycle_id"),
            "point_a": target_summary(structural.get("point_a")),
            "direction": structural.get("direction"),
        },
        "counter_leg": {
            "lifecycle_id": counter.get("lifecycle_id"),
            "direction": counter.get("direction"),
        } if counter else None,
        "bars": counts,
    }


def evaluate_timeline(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure whether the radar was early, stable and causally consistent."""
    technical = [
        item for item in items if item.get("should_notify")
        and item.get("notification_type") not in {"NEWS_RISK", "NEWS_DRIVER", None, "NONE"}
    ]
    actionable = [item for item in technical if item.get("opportunity") in {"early_attention", "setup_forming", "active"}]
    post_fact = [item for item in technical if item.get("opportunity") in {"late", "missed"}]
    seen_alerts: set[tuple[Any, Any]] = set()
    duplicate_alerts = 0
    for item in technical:
        identity = (item.get("alert_lifecycle_key"), item.get("notification_type"))
        if identity in seen_alerts:
            duplicate_alerts += 1
        seen_alerts.add(identity)
    point_a_by_lifecycle: dict[str, Any] = {}
    point_a_drift = 0
    for item in items:
        leg = item.get("structural_leg") if isinstance(item.get("structural_leg"), dict) else {}
        lifecycle = str(leg.get("lifecycle_id") or "")
        point_a = leg.get("point_a")
        if not lifecycle:
            continue
        if lifecycle in point_a_by_lifecycle and point_a_by_lifecycle[lifecycle] != point_a:
            point_a_drift += 1
        point_a_by_lifecycle[lifecycle] = point_a
    rapid_direction_flips = 0
    prior_direction = None
    prior_change_index = -99
    for index, item in enumerate(items):
        direction = ((item.get("primary") or {}).get("direction"))
        if prior_direction and direction and direction != prior_direction:
            if index - prior_change_index <= 2:
                rapid_direction_flips += 1
            prior_change_index = index
        if direction:
            prior_direction = direction
    timely_ratio = len(actionable) / len(technical) if technical else None
    acceptance = {
        "no_post_fact_alerts": len(post_fact) == 0,
        "no_duplicate_lifecycle_alerts": duplicate_alerts == 0,
        "structural_point_a_immutable": point_a_drift == 0,
        "no_rapid_direction_flips": rapid_direction_flips == 0,
    }
    return {
        "technical_alerts": len(technical),
        "actionable_early_alerts": len(actionable),
        "post_fact_alerts": len(post_fact),
        "duplicate_lifecycle_alerts": duplicate_alerts,
        "structural_point_a_drift": point_a_drift,
        "rapid_direction_flips": rapid_direction_flips,
        "timely_alert_ratio": round(timely_ratio, 3) if timely_ratio is not None else None,
        "acceptance": acceptance,
        "passed": all(acceptance.values()),
    }


def signature(item: dict[str, Any]) -> tuple[Any, ...]:
    primary = item.get("primary") or {}
    target = primary.get("target") or {}
    decision = primary.get("decision") or {}
    reaction = primary.get("current_reaction") or {}
    structure = item.get("structure") or {}
    return (
        primary.get("direction"), primary.get("status"), primary.get("score"),
        target.get("name"), target.get("price"),
        decision.get("name"), decision.get("low"), decision.get("high"),
        reaction.get("name"), reaction.get("low"), reaction.get("high"), reaction.get("state"),
        structure.get("weekly_narrative"), structure.get("delivery_status"),
        structure.get("structure_status"),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay MarketRadar over an isolated historical data pack.")
    parser.add_argument("--start", default="2026-01-29")
    parser.add_argument("--end", default="2026-03-23")
    parser.add_argument("--step", choices=("D1", "H4", "H1"), default="D1")
    parser.add_argument("--progress-every", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=120, help="Maximum seconds per pipeline script.")
    parser.add_argument("--max-cycles", type=int, default=0, help="Optional smoke-test limit; 0 means all.")
    parser.add_argument(
        "--bars-per-timeframe",
        type=int,
        default=500,
        help="Rolling closed-bar window supplied to each engine; live radar uses 500.",
    )
    parser.add_argument(
        "--broker-offset-hours",
        type=int,
        default=None,
        help="Override the broker-time offset stored in the replay manifest.",
    )
    return parser.parse_args()


def resolve_pack_root(base: Path, start: str, end: str) -> Path:
    exact = base / f"{start}_{end}"
    if (exact / "manifest.json").is_file():
        return exact
    requested_start = pd.Timestamp(start, tz="UTC")
    requested_end = pd.Timestamp(end, tz="UTC")
    candidates: list[tuple[pd.Timedelta, Path]] = []
    for candidate in base.iterdir() if base.is_dir() else []:
        if not candidate.is_dir() or not (candidate / "manifest.json").is_file():
            continue
        try:
            pack_start_text, pack_end_text = candidate.name.split("_", 1)
            pack_start = pd.Timestamp(pack_start_text, tz="UTC")
            pack_end = pd.Timestamp(pack_end_text, tz="UTC")
        except (ValueError, TypeError):
            continue
        if pack_start <= requested_start and pack_end >= requested_end:
            candidates.append((pack_end - pack_start, candidate))
    if not candidates:
        raise FileNotFoundError(f"No replay pack contains {start} -> {end}")
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def main() -> int:
    args = parse_args()
    project_root = Path(__file__).resolve().parent
    pack_root = resolve_pack_root(project_root / "data" / "replay" / SYMBOL_FILE, args.start, args.end)
    manifest = load_json(pack_root / "manifest.json")
    broker_offset_hours = (
        int(args.broker_offset_hours)
        if args.broker_offset_hours is not None
        else int(manifest.get("broker_offset_hours", 2))
    )
    raw_dir = pack_root / "raw"
    frames = load_frames(raw_dir)
    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")
    checkpoints = replay_checkpoints(frames[args.step], args.step, start, end)
    if args.max_cycles > 0:
        checkpoints = checkpoints[: args.max_cycles]
    if not checkpoints:
        raise RuntimeError("No replay checkpoints found in the requested window")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = pack_root / "runs" / f"{args.step}_{run_id}"
    runtime_dir = run_dir / "runtime"
    snapshot_dir = runtime_dir / "data" / "raw" / SYMBOL_FILE
    timeline_path = run_dir / "timeline.jsonl"
    changes_path = run_dir / "changes.json"
    run_dir.mkdir(parents=True, exist_ok=False)

    print(f"REPLAY_RADAR: STARTED | step={args.step} | cycles={len(checkpoints)}")
    bars_per_timeframe = max(100, int(args.bars_per_timeframe))
    print(
        f"WINDOW: {args.start} -> {args.end} | warmup_days={manifest.get('warmup_days')} "
        f"| rolling_bars={bars_per_timeframe}"
    )
    started = time.monotonic()
    items: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    previous_signature: tuple[Any, ...] | None = None

    for index, checkpoint in enumerate(checkpoints, start=1):
        counts = write_snapshot(
            frames,
            snapshot_dir,
            checkpoint,
            broker_offset_hours=broker_offset_hours,
            bars_per_timeframe=bars_per_timeframe,
        )
        write_replay_session(runtime_dir, checkpoint)
        passed, error = run_pipeline(project_root, runtime_dir, max(10, args.timeout))
        if not passed:
            failure = {
                "checkpoint_utc": checkpoint.isoformat(),
                "cycle": index,
                "error": error,
            }
            atomic_json(run_dir / "failure.json", failure)
            print(f"REPLAY_FAILED: cycle={index} | checkpoint={checkpoint.isoformat()}")
            print(error)
            return 1

        scenario = load_json(runtime_dir / "data" / "scenario_states" / f"{SYMBOL_FILE}.json")
        event_payload = load_json(runtime_dir / "data" / "events" / f"{SYMBOL_FILE}.json", required=False)
        policy_payload = load_json(runtime_dir / "data" / "alert_policy" / f"{SYMBOL_FILE}.json", required=False)
        item = timeline_item(checkpoint, scenario, event_payload, counts, policy_payload)
        items.append(item)
        current_signature = signature(item)
        if previous_signature is None or current_signature != previous_signature:
            changes.append(item)
            primary = item["primary"]
            print(
                f"CHANGE {checkpoint.date()} | price={item.get('price')} | "
                f"{str(primary.get('direction') or 'none').upper()} "
                f"{str(primary.get('status') or 'silent').upper()} | score={primary.get('score')} | "
                f"delivery={item['structure'].get('delivery_status')}"
            )
        previous_signature = current_signature
        with timeline_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        if index % max(1, args.progress_every) == 0 or index == len(checkpoints):
            print(f"PROGRESS: {index}/{len(checkpoints)} | checkpoint={checkpoint.isoformat()}")

    elapsed = time.monotonic() - started
    metrics = evaluate_timeline(items)
    report = {
        "schema_version": 1,
        "symbol": SYMBOL,
        "replay_window": {"start": args.start, "end": args.end},
        "step": args.step,
        "cycles": len(items),
        "material_changes": len(changes),
        "elapsed_seconds": round(elapsed, 2),
        "session_scoring_enabled": False,
        "timeline_file": str(timeline_path),
        "changes": changes,
        "quality_metrics": metrics,
    }
    atomic_json(changes_path, report)
    print(
        f"REPLAY_RADAR: PASSED | cycles={len(items)} | changes={len(changes)} | "
        f"elapsed={elapsed:.1f}s"
    )
    print(
        f"REPLAY_QUALITY: {'PASSED' if metrics['passed'] else 'NEEDS_REVIEW'} | "
        f"early={metrics['actionable_early_alerts']} | post_fact={metrics['post_fact_alerts']} | "
        f"duplicates={metrics['duplicate_lifecycle_alerts']} | flips={metrics['rapid_direction_flips']}"
    )
    print(f"REPLAY_REPORT: {changes_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
