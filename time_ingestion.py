"""Receipt persistence and time lineage shared by live acquisition and replay."""
import json,hashlib
from pathlib import Path
from mt5_clock_authority import ClockLedger,canonical,digest


def integrated_runtime():
    """Set by the existing runner for every child, never by input data."""
    import os
    return os.environ.get('MARKET_RADAR_ADMISSION') == '1'


def deferred_context(reason):
    return dict(available=False, status='DEFERRED', reason=reason,
                historical_availability='UNKNOWN', utc_time=None,
                causal_permission='none', intraday_reaction_capable=False,
                scoring_applied=False, telegram_eligible=False)


def capture_source_rows(mt5, symbol, timeframe, timeframe_name, count, root=None):
    """Read-only current price-order input. Never supplies historical UTC.

    Requires advancing source ticks, ordered bars, an unchanged content version
    and a successor in all three samples, plus current independent clock proof.
    Source scope is explicit per symbol. Callers keep their existing calculations.
    """
    import time, math
    from datetime import datetime, timezone
    from mt5_clock_authority import epoch, canonical, digest
    root=Path(root or Path.cwd())
    at=lambda:datetime.now(timezone.utc).isoformat()
    view=verified_utc_view(root,at())
    authority=view['current_clock_bounds']
    clock_reference=dict(kind='capture',capture_id=view['capture_id'],sha256=view['capture_sha256'])
    if epoch(at()) > epoch(authority['expires_at_utc']):
        authority=refresh_delivery_clock(root)
        clock_reference=dict(kind='system_utc_only',sha256=digest(authority),
                             path='data/time_authority/delivery/'+digest(authority)+'.json')
    samples=[]
    for _ in range(3):
        start=at();tick=mt5.symbol_info_tick(symbol)
        rates=mt5.copy_rates_from_pos(symbol,timeframe,0,count+1)
        rows=[] if rates is None else [{k:r[k].item() for k in rates.dtype.names} for r in rates]
        if tick is None or len(rows)<2:raise ValueError('SOURCE_DATA_MISSING')
        if tick.time != tick.time_msc//1000:raise ValueError('SOURCE_TICK_EPOCH_INCONSISTENT')
        epochs=[int(r['time']) for r in rows]
        if epochs!=sorted(set(epochs)) or epochs[-1]>tick.time:
            raise ValueError('SOURCE_ORDER_INVALID')
        # Source-clock age ceiling only, not an inferred UTC candle close.
        age_limit={'M5':300,'H1':3600,'H4':14400,'D1':90000,'W1':608400}[timeframe_name]
        if tick.time-epochs[-1]>age_limit:raise ValueError('SOURCE_CURRENT_BAR_STALE')
        for r in rows:
            if not all(math.isfinite(float(r[k])) for k in ('open','high','low','close','tick_volume')):
                raise ValueError('SOURCE_OHLCV_INVALID')
            if not r['low']<=min(r['open'],r['close'])<=max(r['open'],r['close'])<=r['high'] or r['tick_volume']<0:
                raise ValueError('SOURCE_OHLCV_INVALID')
        samples.append(dict(start_utc=start,end_utc=at(),tick_epoch=tick.time,tick_msc=tick.time_msc,rows=rows))
        time.sleep(.2)
    ticks=[s['tick_msc'] for s in samples]
    if ticks!=sorted(set(ticks)):raise ValueError('SOURCE_FRESH_TICKS_REQUIRED')
    end=at()
    if epoch(end)>epoch(authority['expires_at_utc']):raise ValueError('ACQUISITION_CLOCK_EXPIRED')
    versions=[{digest(r) for r in s['rows'][:-1]} for s in samples]
    admitted=[r for r in samples[-1]['rows'][:-1] if all(digest(r) in v for v in versions)]
    if len(admitted)<2 or len(admitted)!=len(samples[-1]['rows'])-1:
        raise ValueError('STABLE_CLOSED_CONTENT_REQUIRED')
    body=dict(source='OANDA_MT5_LOCAL',symbol=symbol,timeframe=timeframe_name,samples=samples,
              clock_reference=clock_reference,
              observed_at_utc=end,available_at_utc=__import__('mt5_clock_authority').utc(epoch(end)+max(0,authority['hi'])),
              historical_availability='UNKNOWN',time_basis='SOURCE_ORDER_ONLY',admitted_sha256=[digest(r) for r in admitted])
    key=digest(body);folder=root/'data/receipts/auxiliary';folder.mkdir(parents=True,exist_ok=True)
    path=folder/(key+'.json')
    if not path.exists():path.write_text(canonical(body),encoding='utf-8')
    # The bounded uncertainty upper edge must precede actual use.
    wait=epoch(body['available_at_utc'])-epoch(at())
    if wait>0:time.sleep(wait)
    return admitted,dict(receipt_sha256=key,receipt=str(path.relative_to(root)),
                        source=body['source'],symbol=symbol,timeframe=timeframe_name,
                        available_at_utc=body['available_at_utc'],historical_availability='UNKNOWN',time_basis='SOURCE_ORDER_ONLY')


def refresh_delivery_clock(root, probe=None):
    """Refresh system UTC authority only; never extend broker/bar certificates."""
    from datetime import datetime, timezone
    from mt5_clock_authority import bounds, utc, epoch
    if probe is None:
        from mt5_clock_probe import probes
        probe = probes
    samples = probe()
    lo, hi = bounds(samples)
    now = datetime.now(timezone.utc).isoformat()
    first = min(epoch(x['start_utc']) for x in samples)
    end = max(epoch(x['end_utc']) for x in samples)
    if not 0 <= epoch(now)-first <= 60 or end > epoch(now):
        raise ValueError('DELIVERY_CLOCK_PROBE_STALE')
    body = dict(samples=samples, observed_at_utc=now, available_at_utc=utc(max(epoch(now),end+hi)), expires_at_utc=utc(end+60),
                lo=lo, hi=hi, scope='SYSTEM_UTC_ONLY_NOT_BROKER_HISTORY')
    folder = Path(root)/'data/time_authority/delivery'
    folder.mkdir(parents=True, exist_ok=True)
    key = digest(body)
    (folder/(key+'.json')).write_text(canonical(body),encoding='utf-8')
    pending=folder/'latest.pending'
    pending.write_text(canonical({'sha256':key}),encoding='utf-8')
    pending.replace(folder/'latest.json')
    import time
    wait=epoch(body['available_at_utc'])-datetime.now(timezone.utc).timestamp()
    if wait>0:time.sleep(wait)
    return body


def refreshed_delivery_window(root, at_utc):
    from mt5_clock_authority import epoch, bounds
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    try:
        folder=Path(root)/'data/time_authority/delivery'
        key=json.loads((folder/'latest.json').read_text('utf-8'))['sha256']
        if len(key)!=64 or any(c not in '0123456789abcdef' for c in key):
            raise ValueError('CLOCK_HASH_INVALID')
        body=json.loads((folder/(key+'.json')).read_text('utf-8'))
        if digest(body)!=key:raise ValueError('CLOCK_HASH_MISMATCH')
        lo,hi=bounds(body['samples']);now=epoch(at_utc)
        end=max(epoch(x['end_utc']) for x in body['samples'])
        if not max(epoch(body['observed_at_utc']),end+hi)<=now<=end+60:
            return False,'CURRENT_CLOCK_PROOF_EXPIRED'
        allowed=all(8<=datetime.fromtimestamp(now+x,timezone.utc).astimezone(ZoneInfo('Europe/Dublin')).hour<20 for x in (lo,hi))
        return allowed, 'DUBLIN_WINDOW_VERIFIED' if allowed else 'OUTSIDE_0800_2000_DUBLIN'
    except (OSError,ValueError,KeyError,TypeError):
        return False,'CURRENT_CLOCK_PROOF_REQUIRED'


def verified_utc_view(root, cutoff):
    """Exact observed certificates only: never extrapolate an offset to history."""
    from mt5_clock_authority import epoch, candidate, utc
    verified = load_lineage(root)
    ledger = ClockLedger(Path(root)/'data/time_authority/observations.sqlite')
    try:
        records = ledger.records()
        key, capture, _ = records[-1]
        if key != verified['capture_id']:
            raise ValueError('LATEST_CAPTURE_POINTER_MISMATCH')
        authority = candidate(capture)
        proof_at = max(epoch(x['end_utc']) for x in capture['external_after']) + authority['hi']
        lineage = ledger.lineage(key, cutoff)
    finally:
        ledger.close()
    rows = []
    for row in lineage['rows']:
        item = dict(row)
        admitted = (item['verified_utc_timestamp'] is not None and
                    item['verified_close_utc'] is not None and
                    item['available_at_utc'] is not None and
                    max(proof_at, epoch(item['available_at_utc']), epoch(item['verified_close_utc'])) <= epoch(cutoff))
        item['utc_feature_admitted'] = admitted
        if admitted:
            item['available_at_utc'] = utc(max(proof_at, epoch(item['available_at_utc'])))
        else:
            item['verified_utc_timestamp'] = None
            item['utc_feature_reason'] = 'EXACT_OPEN_CLOSE_AND_RECEIPT_PROOF_REQUIRED'
        rows.append(item)
    return dict(capture_id=key, capture_sha256=lineage['capture_sha256'], cutoff=cutoff,
                rows=rows, current_clock_bounds=authority, proof_available_at_utc=utc(proof_at),
                historical_timezone_rule='UNKNOWN', mapping_scope='EXACT_OBSERVED_CONTENT_ONLY')


def verified_delivery_window(root, at_utc):
    """A current independent clock bound, not a broker-offset assumption."""
    from mt5_clock_authority import epoch, utc
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        view = verified_utc_view(root, at_utc)
        a = view['current_clock_bounds'];now = epoch(at_utc)
        if now < epoch(view['proof_available_at_utc']) or now > epoch(a['expires_at_utc']):
            return False, 'CURRENT_CLOCK_PROOF_UNAVAILABLE_OR_EXPIRED'
        for t in (now+a['lo'], now+a['hi']):
            local = datetime.fromisoformat(utc(t)).astimezone(ZoneInfo('Europe/Dublin'))
            if not 8 <= local.hour < 20:
                return False, 'OUTSIDE_0800_2000_DUBLIN'
        return True, 'INDEPENDENT_CLOCK_AND_DUBLIN_WINDOW_VERIFIED'
    except (OSError, ValueError, KeyError, TypeError):
        return False, 'CURRENT_CLOCK_PROOF_REQUIRED'

def persist_capture(root,capture_id,capture,decision_at_utc):
    root=Path(root);folder=root/'data/receipts'/capture_id
    if not capture_id or Path(capture_id).name!=capture_id or capture_id in {'.','..'}:raise ValueError('CAPTURE_ID_INVALID')
    raw=canonical(capture).encode();folder.mkdir(parents=True,exist_ok=True)
    p=folder/'clock_capture.json'
    if p.exists() and p.read_bytes()!=raw:raise ValueError('CAPTURE_FILE_CONFLICT')
    if not p.exists():
        with p.open('xb') as f:f.write(raw)
    ledger=ClockLedger(root/'data/time_authority/observations.sqlite')
    try:
        authority=ledger.ingest(capture_id,capture)
        lineage=ledger.lineage(capture_id,decision_at_utc)
    finally:ledger.close()
    receipt=dict(capture_id=capture_id,clock_capture_sha256=hashlib.sha256(raw).hexdigest(),raw_epochs_preserved=True,raw_ohlcv_preserved=True,authority_contract=authority['contract'],authority=authority,historical_availability='PARTIAL_OBSERVED_ONLY',production_promotion=False)
    # Immutable receipt; mutable latest view only points at immutable data.
    rp=folder/'clock_receipt.json';encoded=canonical(receipt).encode()
    if rp.exists() and rp.read_bytes()!=encoded:raise ValueError('RECEIPT_CONFLICT')
    if not rp.exists():rp.write_bytes(encoded)
    latest=root/'data/time_authority/latest.json';latest.parent.mkdir(parents=True,exist_ok=True)
    pending=latest.with_suffix('.pending');pending.write_text(canonical(dict(receipt=str(rp.relative_to(root)).replace('\\','/'),receipt_sha256=hashlib.sha256(encoded).hexdigest(),lineage=lineage)),encoding='utf-8');pending.replace(latest)
    return lineage

def load_lineage(root):
    root=Path(root);latest=root/'data/time_authority/latest.json'
    view=json.loads(latest.read_text('utf-8'));p=(root/view['receipt']).resolve()
    if not p.is_relative_to((root/'data/receipts').resolve()):raise ValueError('RECEIPT_PATH_INVALID')
    raw=p.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=view['receipt_sha256']:raise ValueError('RECEIPT_HASH_MISMATCH')
    receipt=json.loads(raw);capture=p.with_name('clock_capture.json').read_bytes()
    if hashlib.sha256(capture).hexdigest()!=receipt['clock_capture_sha256']:raise ValueError('CAPTURE_HASH_MISMATCH')
    ledger=ClockLedger(root/'data/time_authority/observations.sqlite')
    try:lineage=ledger.lineage(receipt['capture_id'],view['lineage']['decision_at_utc'])
    finally:ledger.close()
    if lineage!=view['lineage']:raise ValueError('TIME_LINEAGE_VIEW_MISMATCH')
    return lineage

def journal_deferred_cycle(root,cycle_id):
    """Existing C/D/R journal, explicit non-assessment; no legacy state overwrite."""
    from cdr_operational import publish_journal,now
    from recorded_ledger import RecordedLedger
    from r5_bridge import retrospective_context
    root=Path(root);lineage=load_lineage(root);out=root/'data/cdr';out.mkdir(parents=True,exist_ok=True)
    ledger=RecordedLedger(out/'recorded.db',context=retrospective_context(),dataset_id='captured:operational-audit-v1')
    try:
        for stage in ('capture','scenario','policy','notification'):
            key=cycle_id+':'+stage
            existing=[e for e in ledger.events() if e.get('audit_key')==key]
            if existing:
                if existing[0].get('time_lineage')!=lineage:raise ValueError('TIME_CYCLE_ID_CONFLICT')
                continue
            record=dict(kind='OPERATIONAL_STAGE',audit_key=key,cycle_id=cycle_id,stage=stage,at_utc=now(),at_utc_authority='LOCAL_UNVERIFIED',status='DEFERRED',time_lineage=lineage,technical=None,hypothesis_created=False,assessment=dict(status='DEFERRED',reasons=lineage['reasons']),eligibility=dict(status='DEFERRED',delivery_authorized=False),c_context=dict(status='DEFERRED',reason='No causal conclusions from unverified history'),existing_radar_state='PRESERVED_NOT_REEVALUATED',should_notify=False,outcome='NOT_GENERATED_TIME_UNVERIFIED',trade_authorized=False)
            with ledger.transaction():ledger.append(key+':stage',record)
        publish_journal(out,ledger)
        return {'status':'DEFERRED','cycle_id':cycle_id,'capture_id':lineage['capture_id'],'stages':4}
    finally:ledger.close()
