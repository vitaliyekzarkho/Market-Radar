"""Read-only historical MT5 M15 export for C3.3.3 (no Radar/Telegram start)."""
from __future__ import annotations
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from reaction_window_engine import build_reaction_report, atomic_json
from live_reaction_engine import rows_from_rates
from prepare_causal_replay_pack import select_market_symbols
from causal_replay_runner import synthetic_usd_breadth
from usd_breadth_engine import PAIR_SPECS


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--story-id',default='',help='Exact story_id from fast_news_interpretations.json')
    p.add_argument('--interpretations',default='data/news/fast_news_interpretations.json')
    p.add_argument('--event-time',default='2026-10-09T18:46:00+00:00')
    p.add_argument('--output',default='data/news/diesel_historical_reaction.json')
    a=p.parse_args()
    if not a.story_id:
        src=Path(a.interpretations)
        if not src.exists(): raise SystemExit(f'Missing interpretations file: {src}')
        entries=json.loads(src.read_text(encoding='utf-8')).get('interpretations',[])
        candidates=[str(x.get('story_id')) for x in entries if 'diesel' in json.dumps(x,ensure_ascii=False).lower() and x.get('story_id')]
        if len(candidates)!=1: raise SystemExit(f'Expected one diesel story; found {len(candidates)}. Supply --story-id explicitly.')
        a.story_id=candidates[0]
    event=datetime.fromisoformat(a.event_time.replace('Z','+00:00')).astimezone(timezone.utc)
    as_of=event+timedelta(days=5)
    start=event-timedelta(hours=30)
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise SystemExit('Missing MetaTrader5 package in this Python environment') from exc
    if not mt5.initialize():
        raise SystemExit(f'MT5 initialize failed: {mt5.last_error()}')
    try:
        selected=select_market_symbols(list(mt5.symbols_get() or []))
        assets={}
        for role in ('gold','oil'):
            symbol=selected.get(role)
            if not symbol:
                print(f'{role}: UNAVAILABLE (not fabricated)')
                continue
            mt5.symbol_select(symbol,True)
            rates=mt5.copy_rates_range(symbol,mt5.TIMEFRAME_M15,start,as_of)
            rows=rows_from_rates(rates,as_of)
            if rows:
                assets[role]={'symbol':symbol,'source':'MT5 historical M15','timeframe':'M15','candles':rows}
                print(f'{role}: {symbol} / {len(rows)} bars')
            else:
                print(f'{role}: NO_DATA {symbol} / {mt5.last_error()}')
        pairs={}
        for pair,symbol in (selected.get('usd',{}).get('breadth',{}) or {}).items():
            if pair not in PAIR_SPECS: continue
            mt5.symbol_select(symbol,True)
            rows=rows_from_rates(mt5.copy_rates_range(symbol,mt5.TIMEFRAME_M15,start,as_of),as_of)
            if rows: pairs[pair]=rows
        if len(pairs)>=3:
            synthetic=synthetic_usd_breadth(pairs)
            if synthetic:
                assets['usd']={'symbol':'USD_BREADTH','source':f'MT5 synthetic breadth ({len(pairs)} FX pairs), not direct DXY','timeframe':'M15','candles':synthetic}
                print(f'USD breadth: {len(pairs)} pairs / {len(synthetic)} bars')
        else:
            print(f'USD breadth: INSUFFICIENT_DATA ({len(pairs)} FX pairs)')
        print('Yields: NOT COLLECTED (no verified yield series; do not substitute Treasury price)')
        if not assets: raise SystemExit('No historical bars collected. Nothing saved.')
        report=build_reaction_report({'event_id':a.story_id,'title':'Diesel supply story','event_time_utc':event.isoformat()},assets,as_of)
        atomic_json(Path(a.output),report)
        print(f'SAVED: {a.output} | event={event.isoformat()} | no trading actions')
    finally:
        mt5.shutdown()

if __name__=='__main__': main()
