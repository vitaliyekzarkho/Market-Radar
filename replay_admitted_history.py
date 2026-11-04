"""Explicit offline entrypoint; never imports native MT5 or transport."""
import argparse
import hashlib
import json
import sys
from pathlib import Path


def main():
    def guard(event, args):
        if event in {'socket.connect','socket.connect_ex','socket.getaddrinfo'} or (event == 'import' and args[0].split('.')[0] == 'MetaTrader5'):
            raise PermissionError('OFFLINE_ONLY')
        if event in {'open','sqlite3.connect'} and args and isinstance(args[0], (str, bytes)):
            p = str(args[0]).replace('/', '\\').casefold()
            if p.startswith('c:\\users\\user\\marketradar\\'):
                raise PermissionError('PRODUCTION_FORBIDDEN')
    sys.addaudithook(guard)
    p = argparse.ArgumentParser()
    p.add_argument('--capture', required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--capture-id', required=True)
    p.add_argument('--cutoff', required=True)
    p.add_argument('--root', required=True)
    p.add_argument('--mode', choices=['current_analysis','historical_replay','shadow'], default='current_analysis')
    a = p.parse_args()
    root = Path(a.root).resolve()
    if not (root/'OFFLINE_ADMISSION_SANDBOX').is_file() or str(root).casefold().startswith('c:\\users\\user\\marketradar'):
        raise ValueError('EXPLICIT_OFFLINE_SANDBOX_REQUIRED')
    raw = Path(a.capture).read_bytes()
    if hashlib.sha256(raw).hexdigest() != a.sha256:
        raise ValueError('CAPTURE_HASH_MISMATCH')
    from mt5_clock_authority import ClockLedger, canonical, digest
    from time_ingestion import persist_capture
    from historical_admission_pipeline import evaluate, journal_result, previous_scenario_at
    capture = json.loads(raw)
    persist_capture(root, a.capture_id, capture, a.cutoff)
    ledger = ClockLedger(root/'data/time_authority/observations.sqlite')
    try:
        result = evaluate(ledger.records(), a.cutoff, a.mode,
                          previous_scenario=previous_scenario_at(root, a.cutoff, a.mode))
    finally:
        ledger.close()
    cycle_id = 'admission:' + digest([a.capture_id, a.cutoff, a.mode])
    journal = journal_result(root, cycle_id, result)
    (root/'ANALYSIS.json').write_text(canonical(result), encoding='utf-8')
    (root/'JOURNAL_RESULT.json').write_text(canonical(journal), encoding='utf-8')
    print(json.dumps({'status':result['status'], 'scenario_status':result['scenario_status'], 'journal':journal}))


if __name__ == '__main__': main()
