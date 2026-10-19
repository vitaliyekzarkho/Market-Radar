from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, time as clock_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


try:
    IRELAND = ZoneInfo("Europe/Dublin")
except ZoneInfoNotFoundError:
    # Windows installations without the optional tzdata package still use the
    # machine's configured local timezone. MarketRadar is operated in Ireland.
    IRELAND = datetime.now().astimezone().tzinfo
ACTIVE_FROM = clock_time(8, 0)
ACTIVE_TO = clock_time(20, 0)
DEFAULT_INTERVAL_SECONDS = 60

SNAPSHOT_SCRIPT = "save_snapshot.py"
HEAVY_PIPELINE = [
    "market_features.py",
    "key_levels.py",
    "dealing_range_engine.py",
    "order_flow_structure.py",
]
LIGHT_PIPELINE = [
    "session_context.py",
    "level_state.py",
    # Fast observers run every cycle but remain context-only. They neither
    # alter the technical score nor emit Telegram events on their own.
    "market_pulse_engine.py",
    "volume_context_engine.py",
    "forex_factory_adapter.py",
    "official_ism_adapter.py",
    "official_bls_adapter.py",
    "official_bea_adapter.py",
    "official_fomc_adapter.py",
    "official_event_matcher.py",
    "fomc_interpretation_engine.py",
    "fomc_phase_engine.py",
    "fomc_market_reaction_engine.py",
    "usd_breadth_engine.py",
    "dxy_context_engine.py",
    "official_macro_adapter.py",
    "rates_context_engine.py",
    "oil_context_engine.py",
    "market_news_adapter.py",
    "intermarket_context_engine.py",
    "cot_context_adapter.py",
    "fed_expectations_adapter.py",
    "macro_context_engine.py",
    "news_interpretation_engine.py",
    "live_reaction_engine.py",
    "live_causal_engine.py",
    "scenario_state.py",
    "trade_idea_engine.py",
    "event_monitor.py",
    "alert_policy.py",
    "notification_builder.py",
    "telegram_notifier.py",
]
PIPELINE = [SNAPSHOT_SCRIPT, *HEAVY_PIPELINE, *LIGHT_PIPELINE]

SCENARIO_FILE = Path("data") / "scenario_states" / "GOLD_pro.json"
CACHED_TIMEFRAMES = ("W1", "D1", "H4", "H1", "M15")
CACHED_RAW_DIR = Path("data") / "raw" / "GOLD_pro"
SCHEDULE_STATE_FILE = Path("data") / "runtime" / "GOLD_pro_radar_schedule.json"
HEARTBEAT_FILE = Path("data") / "runtime" / "radar_heartbeat.json"
HEAVY_OUTPUTS = (
    Path("data") / "features" / "GOLD_pro.json",
    Path("data") / "levels" / "GOLD_pro.json",
    Path("data") / "ranges" / "GOLD_pro.json",
    Path("data") / "order_flow" / "GOLD_pro.json",
)


def configure_console_utf8() -> None:
    """Keep verbose pipeline output printable in Windows PowerShell."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def ireland_now() -> datetime:
    return datetime.now(IRELAND)


def inside_active_window(moment: datetime) -> bool:
    local_time = moment.time().replace(tzinfo=None)
    return ACTIVE_FROM <= local_time < ACTIVE_TO


def validate_project(root: Path) -> list[str]:
    return [name for name in PIPELINE if not (root / name).is_file()]


def weekend_cache_available(root: Path, moment: datetime) -> bool:
    """Allow read-only analysis of the last snapshot while FX/CFD markets are closed."""
    if moment.weekday() not in {5, 6}:
        return False
    return all((root / CACHED_RAW_DIR / f"{timeframe}.csv").is_file() for timeframe in CACHED_TIMEFRAMES)


def complete_cache_available(root: Path) -> bool:
    return all((root / CACHED_RAW_DIR / f"{timeframe}.csv").is_file() for timeframe in CACHED_TIMEFRAMES)


def recognized_market_closed(output: str, moment: datetime) -> bool:
    return moment.weekday() in {5, 6} or (
        "Trade allowed: False" in output and "Invalid broker UTC offset" in output
    )


def load_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def utf8_subprocess_environment() -> dict[str, str]:
    """Keep every Python pipeline step on one encoding on Windows."""
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def last_closed_bar_marker(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return None
    for row in reversed(rows):
        closed = str(row.get("is_closed", "true")).strip().lower()
        if closed not in {"true", "1", "yes"}:
            continue
        return str(row.get("utc_time") or row.get("broker_time") or row.get("time") or "").strip() or None
    return None


def heavy_recalculation_required(root: Path, marker: str | None, force: bool = False) -> tuple[bool, str]:
    if force:
        return True, "forced"
    if any(not (root / output).is_file() for output in HEAVY_OUTPUTS):
        return True, "missing derived output"
    state = load_json_file(root / SCHEDULE_STATE_FILE)
    previous_marker = state.get("last_heavy_h1_closed_bar")
    if not marker:
        return True, "H1 marker unavailable"
    if previous_marker != marker:
        return True, "new closed H1 candle"
    return False, "same closed H1 candle"


def run_step(root: Path, script_name: str, verbose: bool, report_failure: bool = True) -> tuple[bool, str]:
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(root / script_name)],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        env=utf8_subprocess_environment(),
    )
    elapsed = time.monotonic() - started
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if result.returncode != 0:
        if report_failure:
            print(f"FAILED: {script_name} | exit={result.returncode} | {elapsed:.1f}s")
            if output:
                print(output)
        return False, output
    print(f"PASSED: {script_name} | {elapsed:.1f}s")
    if verbose and output:
        print(output)
    return True, output


def run_pipeline(root: Path, verbose: bool, force_full: bool = False) -> bool:
    cycle_time = ireland_now()
    print(f"\nRADAR_CYCLE: {cycle_time.isoformat(timespec='seconds')}")
    snapshot_passed, snapshot_output = run_step(
        root, SNAPSHOT_SCRIPT, verbose, report_failure=False
    )
    if not snapshot_passed:
        if complete_cache_available(root) and recognized_market_closed(snapshot_output, cycle_time):
            print("MARKET_DATA: UNAVAILABLE | market closed")
            print("SNAPSHOT_FALLBACK: using last complete cached W1/D1/H4/H1/M15 snapshot")
        else:
            print(f"FAILED: {SNAPSHOT_SCRIPT} | fresh market data unavailable")
            if snapshot_output:
                print(snapshot_output)
            print("RADAR_CYCLE: FAILED — remaining steps skipped")
            atomic_write_json(root / HEARTBEAT_FILE, {"updated_at": cycle_time.isoformat(), "status": "failed", "step": SNAPSHOT_SCRIPT})
            return False

    h1_marker = last_closed_bar_marker(root / CACHED_RAW_DIR / "H1.csv")
    run_heavy, reason = heavy_recalculation_required(root, h1_marker, force=force_full)
    if run_heavy:
        print(f"HEAVY_LAYER: RUN | reason={reason}")
        for script_name in HEAVY_PIPELINE:
            passed, _ = run_step(root, script_name, verbose)
            if not passed:
                print("RADAR_CYCLE: FAILED — remaining steps skipped")
                atomic_write_json(root / HEARTBEAT_FILE, {"updated_at": cycle_time.isoformat(), "status": "failed", "step": script_name})
                return False
        atomic_write_json(root / SCHEDULE_STATE_FILE, {
            "updated_at": cycle_time.isoformat(),
            "last_heavy_h1_closed_bar": h1_marker,
        })
    else:
        print(f"HEAVY_LAYER: SKIPPED | reason={reason}")

    for script_name in LIGHT_PIPELINE:
        passed, _ = run_step(root, script_name, verbose)
        if not passed:
            print("RADAR_CYCLE: FAILED — remaining steps skipped")
            atomic_write_json(root / HEARTBEAT_FILE, {"updated_at": cycle_time.isoformat(), "status": "failed", "step": script_name})
            return False
    print("RADAR_CYCLE: PASSED")
    atomic_write_json(root / HEARTBEAT_FILE, {"updated_at": cycle_time.isoformat(), "status": "passed", "mode": "live"})
    return True


def load_scenario(root: Path) -> dict[str, Any]:
    path = root / SCENARIO_FILE
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def scenario_signature(payload: dict[str, Any]) -> tuple[Any, ...]:
    primary = payload.get("primary_scenario") or {}
    target = primary.get("point_b") or {}
    decision = primary.get("next_decision_zone") or {}
    current_reaction = primary.get("current_reaction_zone") or {}
    pullback = primary.get("pullback_zone") or {}
    order_flow = payload.get("order_flow_context") or {}
    return (
        primary.get("direction"),
        primary.get("status"),
        primary.get("score"),
        target.get("name"),
        target.get("price"),
        decision.get("name"),
        decision.get("low"),
        decision.get("high"),
        primary.get("next_decision_state"),
        current_reaction.get("name"),
        current_reaction.get("low"),
        current_reaction.get("high"),
        current_reaction.get("h1_state"),
        pullback.get("name"),
        pullback.get("low"),
        pullback.get("high"),
        order_flow.get("weekly_narrative"),
        order_flow.get("delivery_status"),
        order_flow.get("structure_status"),
    )


def print_compact_scenario(payload: dict[str, Any], changed: bool) -> None:
    primary = payload.get("primary_scenario") or {}
    if not primary:
        print("PUBLISHED_CONTEXT: SILENT")
        return
    target = primary.get("point_b") or {}
    decision = primary.get("next_decision_zone") or {}
    current_reaction = primary.get("current_reaction_zone") or {}
    pullback = primary.get("pullback_zone") or {}
    conditions = primary.get("conditions") or {}
    order_flow = payload.get("order_flow_context") or {}
    label = "SCENARIO_CHANGED" if changed else "SCENARIO_UNCHANGED"
    print(f"{label}: {str(primary.get('direction', '')).upper()} {str(primary.get('status', '')).upper()} | score={primary.get('score')}")
    print(f"POINT_B: {target.get('name', 'none')} {target.get('price', '')}")
    print(
        f"NEXT_DECISION_ZONE: {decision.get('name', 'none')} "
        f"{decision.get('low', '')}-{decision.get('high', '')} | "
        f"state={primary.get('next_decision_state', 'none')}"
    )
    print(
        f"CURRENT_REACTION_ZONE: {current_reaction.get('name', 'none')} "
        f"{current_reaction.get('low', '')}-{current_reaction.get('high', '')} | "
        f"state={current_reaction.get('h1_state', 'none')}"
    )
    print(
        f"PULLBACK_ZONE: {pullback.get('name', 'none')} "
        f"{pullback.get('low', '')}-{pullback.get('high', '')}"
    )
    if order_flow:
        print(
            f"MARKET_STRUCTURE: W1={order_flow.get('weekly_narrative', 'unknown')} | "
            f"delivery={order_flow.get('delivery_status', 'unknown')} | "
            f"structure={order_flow.get('structure_status', 'unknown')}"
        )
    if changed:
        print(f"CONFIRMATION: {conditions.get('confirmation', 'none')}")
        print(f"INVALIDATION: {conditions.get('invalidation', 'none')}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the MarketRadar pipeline continuously.")
    parser.add_argument("--once", action="store_true", help="Run one cycle immediately and exit.")
    parser.add_argument("--all-hours", action="store_true", help="Ignore the 08:00-20:00 Ireland window.")
    parser.add_argument("--verbose", action="store_true", help="Print full output from every pipeline script.")
    parser.add_argument("--full", action="store_true", help="Force heavy feature/range/structure recalculation.")
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help="Polling interval in seconds (minimum 15; default 60).",
    )
    return parser.parse_args()


def main() -> int:
    configure_console_utf8()
    args = parse_args()
    interval = max(15, args.interval)
    root = Path(__file__).resolve().parent
    missing = validate_project(root)
    if missing:
        print("RUN_RADAR: FAILED — missing scripts:")
        for name in missing:
            print(f"  - {name}")
        return 1

    print(f"RUN_RADAR: STARTED | python={sys.executable}")
    print(f"INTERVAL: {interval}s | WINDOW: 08:00-20:00 Ireland")
    previous_signature: tuple[Any, ...] | None = None
    waiting_reported = False

    try:
        while True:
            now = ireland_now()
            may_run = args.once or args.all_hours or inside_active_window(now)
            if may_run:
                waiting_reported = False
                cycle_started = time.monotonic()
                if run_pipeline(root, args.verbose, force_full=args.full):
                    payload = load_scenario(root)
                    signature = scenario_signature(payload)
                    changed = previous_signature is None or signature != previous_signature
                    print_compact_scenario(payload, changed)
                    previous_signature = signature
                if args.once:
                    return 0
                elapsed = time.monotonic() - cycle_started
                time.sleep(max(1.0, interval - elapsed))
            else:
                if not waiting_reported:
                    print(
                        f"RADAR_PAUSED: outside 08:00-20:00 Ireland | "
                        f"now={now.strftime('%Y-%m-%d %H:%M:%S %Z')}"
                    )
                    waiting_reported = True
                time.sleep(min(interval, 60))
    except KeyboardInterrupt:
        print("\nRUN_RADAR: STOPPED BY USER")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
