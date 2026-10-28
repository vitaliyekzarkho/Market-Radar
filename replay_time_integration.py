"""Recorded-only replay. Network, native import and production access denied."""
import sys,json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parent
blocked=[]
def guard(event,args):
    if event in {'socket.connect','socket.getaddrinfo','subprocess.Popen'} or (event=='import' and args[0].split('.')[0]=='MetaTrader5'):
        blocked.append(event);raise PermissionError('OFFLINE_REPLAY_ONLY')
    if event in {'open','sqlite3.connect'} and args and isinstance(args[0],(str,bytes)):
        p=str(args[0]).replace('/','\\').lower()
        if p.startswith('c:\\users\\user\\marketradar\\') or Path(p).name.startswith('.env'):raise PermissionError('PRODUCTION_ACCESS_FORBIDDEN')
sys.addaudithook(guard)
from time_ingestion import persist_capture,journal_deferred_cycle
from decision_journal import DecisionJournal
from mt5_clock_authority import ClockLedger,epoch

def main():
    out=ROOT/'time_verification/replay'
    out.mkdir(parents=True,exist_ok=False)
    p=ROOT/'verification_inputs/time_authority/native_clock_capture.json';raw=p.read_bytes();c=json.loads(raw)
    c.update(symbol='GOLD.pro',source='OANDA_MT5_LOCAL')
    lineage=persist_capture(out,'recorded_20261027',c,c['shutdown_end_utc'])
    journal_deferred_cycle(out,'recorded_cycle')
    before=DecisionJournal(out/'data/cdr/journal').scan()[0]
    persist_capture(out,'recorded_20261027',c,c['shutdown_end_utc']);journal_deferred_cycle(out,'recorded_cycle')
    after=DecisionJournal(out/'data/cdr/journal').scan()[0]
    l=ClockLedger(out/'data/time_authority/observations.sqlite')
    try:r=l.records();early=l.lineage('recorded_20261027','2026-10-27T09:35:47Z')
    finally:l.close()
    assert len(before)==4 and before==after and len(r)==1
    verified=[x for x in lineage['rows'] if x['time_status']=='TIME_VERIFIED']
    assert {x['timeframe'] for x in verified}=={'M15','H1'}
    assert all(x['available_at_utc'] is None for x in early['rows'])
    assert not blocked
    result=dict(status='PASS',source_file_sha256=hashlib.sha256(raw).hexdigest(),source_note='Existing actual read-only capture; symbol/source labels attached by local trusted adapter, raw market bytes unchanged',lineage=lineage,counts=dict(bars=len(lineage['rows']),verified_closures=len(verified),unverified=len(lineage['rows'])-len(verified),journal_stages=len(after),capture_records=len(r)),restart_idempotency=True,lookahead_cutoff_rejected=True,decision_journal=list(after.values()),blocked_external_attempts=blocked,native_run=False,telegram=False,trading=False)
    (ROOT/'time_verification/recorded_replay.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in {'lineage','decision_journal'}},indent=2))
if __name__=='__main__':main()
