from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import uuid
from mt5_time_validation import inspect_times, require_admitted_capture

import MetaTrader5 as mt5

from mt5_reader import (
    SYMBOL,
    TIMEFRAMES,
    connect,
    detect_broker_utc_offset,
    get_rates,
)


OUTPUT_DIRECTORY = Path("data") / "raw" / "GOLD_pro"
BAR_COUNT = 500


def main(allow_deferred=False):
    connect()

    try:
        if allow_deferred:
            from clock_capture_adapter import collect
            from mt5_clock_probe import probes
            from time_ingestion import persist_capture
            capture=collect(mt5,TIMEFRAMES,probes,count=BAR_COUNT)
            capture_id='clock_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'_'+uuid.uuid4().hex[:12]
            persist_capture(Path.cwd(),capture_id,capture,datetime.now(timezone.utc).isoformat())
            print('TIME_AUTHORITY_DEFERRED: capture retained; historical analytics and transport skipped')
            return
        # Preserve the documented UTC interpretation as a candidate, not authority.
        # No offset is inferred; admission is gated on time validation below.
        offset_hours = 0
        snapshot_time = datetime.now(timezone.utc)

        OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)

        print(f"Snapshot UTC: {snapshot_time}")
        print("Source epoch interpretation: UTC candidate; broker offset UNKNOWN; time authority UNVERIFIED")

        captured = []
        for name, timeframe in TIMEFRAMES.items():
            capture_started = datetime.now(timezone.utc).isoformat()
            data = get_rates(
                SYMBOL,
                timeframe,
                offset_hours,
                BAR_COUNT,
                start_pos=1,
            )
            received = datetime.now(timezone.utc).isoformat()
            if len(data) < 7 or not data['time'].is_monotonic_increasing or data['time'].duplicated().any():
                raise RuntimeError(f"Insufficient or unordered closed history: {name}")
            data["is_closed"] = None
            data["time_status"] = "TIME_UNVERIFIED"
            data["source_position_closed_claim"] = True
            data["snapshot_utc"] = received
            captured.append((name, data, capture_started, received))

        capture_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:12]
        receipt_dir = Path('data') / 'receipts' / capture_id
        receipt_dir.mkdir(parents=True, exist_ok=False)
        receipts = []
        time_reports = {}
        for name, data, capture_started, received in captured:
            time_reports[name] = inspect_times(data.to_dict('records'), name, received)
            raw = data.to_csv(index=False).encode('utf-8')
            (receipt_dir / f'{name}.csv').write_bytes(raw)
            receipts.append(dict(timeframe=name, source='OANDA MT5', symbol=SYMBOL,
                capture_started_at_utc=capture_started, received_at_utc=received,
                last_market_event_at_utc=data.iloc[-1]['utc_time'].isoformat(),
                sha256=hashlib.sha256(raw).hexdigest(), rows=len(data), source_position=1,
                clock_authority='LOCAL_UNVERIFIED', clock_uncertainty_seconds=None,
                historical_availability='UNKNOWN', time_status='TIME_UNVERIFIED', closure_verified=False))

        (receipt_dir / 'receipt.json').write_text(json.dumps(dict(capture_id=capture_id, observations=receipts),indent=2),encoding='utf-8')
        (receipt_dir / 'time_validation.json').write_text(json.dumps(time_reports,indent=2),encoding='utf-8')
        require_admitted_capture(time_reports)
        for name, data, _, _ in captured:

            output_file = OUTPUT_DIRECTORY / f"{name}.csv"
            temporary = output_file.with_suffix('.tmp')
            temporary.write_bytes((receipt_dir / f'{name}.csv').read_bytes())
            temporary.replace(output_file)

            print(
                f"Saved {name}: {len(data)} bars -> "
                f"{output_file}"
            )

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main(allow_deferred=True)
