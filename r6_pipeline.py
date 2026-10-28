"""Bounded acquisition into unverified local receipts, recorder and unchanged R5.

No native MT5 import or live entrypoint. Native API is explicitly injected.
All local timestamps are claims, never externally admitted receipt authorities.
"""
from dataclasses import asdict
from datetime import datetime,timezone
from pathlib import Path
import csv,io,time,json,re
from r6_adapter import ReadOnlyAdapter,RATE_FIELDS
from r5_bridge import run_replay,retrospective_context,envelope,observation
from r5_input import normalize_csv
from recorded_authority import sha,Rejected,strict_utc
from recorded_context import canonical
from recorded_ledger import safe_path,RecordedLedger
from evidence_recorder import EvidenceRecorder
from offline_integration import single_instance

VERSION='0.4.0-r6-readonly-review'

def system_clock():
    return dict(at_utc=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
                monotonic_ns=time.monotonic_ns(),source='local_system_utc',uncertainty_seconds=None)

def validate_clock(claim,previous=None):
    if set(claim)!={'at_utc','monotonic_ns','source','uncertainty_seconds'} or not strict_utc(claim['at_utc']):raise Rejected('CLOCK_INVALID')
    if type(claim['monotonic_ns'])!=int or claim['monotonic_ns']<0 or not isinstance(claim['source'],str) or not claim['source']:raise Rejected('CLOCK_INVALID')
    u=claim['uncertainty_seconds']
    if u is not None and (type(u) not in (float,int) or not __import__('math').isfinite(u) or u<0):raise Rejected('CLOCK_UNCERTAINTY_INVALID')
    if previous and (claim['monotonic_ns']<previous['monotonic_ns'] or datetime.fromisoformat(claim['at_utc'])<datetime.fromisoformat(previous['at_utc'])):raise Rejected('CLOCK_REGRESSED')

def csv_bytes(rows):
    stream=io.StringIO(newline='')
    w=csv.DictWriter(stream,fieldnames=[*RATE_FIELDS,'broker_time','utc_time','is_closed']);w.writeheader()
    for r in rows:
        # Preserve raw epoch. No current-tick/broker offset estimation.
        stamp=datetime.fromtimestamp(r['time'],timezone.utc).isoformat()
        w.writerow(dict(r,broker_time=stamp,utc_time=stamp,is_closed='true'))
    return stream.getvalue().encode()

def put(path,data):
    path=safe_path(path)
    if path.exists():
        if path.read_bytes()!=data:raise Rejected('IMMUTABLE_FILE_CONFLICT')
    else:
        tmp=path.with_suffix(path.suffix+'.pending');tmp.write_bytes(data);tmp.replace(path)

def acquire_and_replay(api,profile,output,*,session_id,count=500,snapshots=1,clock=system_clock,fail_at=None):
    """Maximum three immediate snapshots; no background loop or retry on errors.

    Resume after durable packet uses saved bytes and never reconnects. Partial
    acquisition before packet commit has no accepted evidence; retry is new capture.
    """
    profile.validate()
    if type(snapshots)!=int or not 1<=snapshots<=3 or type(count)!=int or not 7<=count<=2000:raise Rejected('BOUNDS_INVALID')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',session_id):raise Rejected('SESSION_ID_INVALID')
    out=safe_path(output);build=Path(__file__).resolve().parent
    if out==build or out.is_relative_to(build):raise Rejected('OUTPUT_MUST_BE_SEPARATE')
    out.mkdir(parents=True,exist_ok=True)
    with single_instance(out/'r6.lock'):
        owner=canonical(dict(version=VERSION,profile=asdict(profile),session_id=session_id,count=count,snapshots=snapshots)).encode()
        marker=out/'R6_OWNER.json'
        if not marker.exists() and any(p.name!='r6.lock' for p in out.iterdir()):raise Rejected('OUTPUT_NOT_EMPTY')
        put(marker,owner)
        capture=out/'capture.json'
        if capture.exists():
            packet=json.loads(capture.read_bytes())
            if packet['owner_sha256']!=sha(owner) or packet['payload_sha256']!=sha(canonical(packet['captures']).encode()):raise Rejected('CAPTURE_INTEGRITY_FAILED')
        else:
            captured=[];previous=None
            with ReadOnlyAdapter(api) as reader:
                for index in range(snapshots):
                    series={}
                    for tf in profile.timeframes:
                        before=clock();validate_clock(before,previous)
                        rows=reader.read(profile,tf,count)
                        after=clock();validate_clock(after,before);previous=after
                        series[tf]=dict(raw=rows,started=before,received=after,source_id='mt5:'+profile.broker_symbol,
                            receipt_id=f'local:{profile.symbol_id}:{session_id}:{index}:{tf}',
                            raw_sha256=sha(canonical(rows).encode()),market_event_epoch=rows[-1]['time'],
                            availability='UNKNOWN',clock_authority='LOCAL_UNVERIFIED')
                    captured.append(dict(id=f'{profile.symbol_id}:{session_id}:{index}',series=series,cutoff=previous['at_utc']))
            packet=dict(owner_sha256=sha(owner),captures=captured,payload_sha256=sha(canonical(captured).encode()))
            put(capture,canonical(packet).encode())
        if fail_at=='capture':raise RuntimeError('SIMULATED_AFTER_CAPTURE')
        inp=out/'input';inp.mkdir(exist_ok=True);ctx=retrospective_context();events=[]
        dataset='captured:r6:'+profile.symbol_id+':'+session_id
        ledger=RecordedLedger(out/'capture.db',context=ctx,dataset_id=dataset)
        try:
            recorder=EvidenceRecorder(ledger,input_root=inp)
            for index,item in enumerate(packet['captures']):
                obs=[];artifacts={};files={};mapping={}
                for tf in profile.timeframes:
                    s=item['series'][tf]
                    raw=canonical(s['raw']).encode()
                    if sha(raw)!=s['raw_sha256']:raise Rejected('RAW_HASH_CONFLICT')
                    base=f'captured:r6:{profile.symbol_id}:{session_id}:{index}:{tf}'
                    raw_id=base+':raw';csv_id=base+':csv';b=csv_bytes(s['raw'])
                    normalize_csv(b,symbol=profile.symbol_id,timeframe=tf,cutoff=item['cutoff'])
                    raw_name=f'{index}_{tf}.json';csv_name=f'{index}_{tf}.csv'
                    put(inp/raw_name,raw);put(inp/csv_name,b)
                    o=observation(raw_id,'captured:unverified-file-import',raw)
                    # Local receipt claims live alongside raw hashes; contract times
                    # remain UNKNOWN. No pins/verified evidence manufactured here.
                    o['capture_id']=s['receipt_id'];obs.append(o)
                    derived=observation(csv_id,'captured:r5-derived',b,kind='feature',deps=[raw_id])
                    derived['derivation']['engine_sha256']=sha(Path(__file__).read_bytes())
                    obs.append(derived);artifacts.update({raw_id:raw_name,csv_id:csv_name});files[tf]=csv_name;mapping[tf]=csv_id
                env=envelope(ctx,dataset,item['cutoff'],obs);env_name=f'{index}_envelope.json'
                put(inp/env_name,canonical(env).encode())
                recorder.receive('capture:'+item['id'],envelope_path=inp/env_name,
                    artifact_paths={k:inp/v for k,v in artifacts.items()},received_at_utc=item['cutoff'],clock_source='local_system_utc_UNVERIFIED')
                events.append(dict(id=item['id'],symbol=profile.symbol_id,cutoff=item['cutoff'],files=files,
                    envelope=env_name,artifacts=artifacts,series_observations=mapping))
        finally:ledger.close()
        put(inp/'manifest.json',canonical(dict(mode='recorded',dataset_id=dataset,events=events)).encode())
        if fail_at=='recorder':raise RuntimeError('SIMULATED_AFTER_RECORDER')
        replay=run_replay(inp/'manifest.json',out/'replay',context=ctx)
        report=dict(version=VERSION,profile=asdict(profile),capture_sha256=sha(capture.read_bytes()),
            delivery_permission=False,trade_permission=False,availability='UNKNOWN_LOCAL_CLOCK_ONLY',replay=replay,
            limitations=['UTC epoch semantics require broker validation before live approval',
                'Session calendar unconfirmed; closed bars conservatively filtered by R5 nominal duration',
                'Derived completion and scenario establishment remain UNKNOWN',
                'No independent timestamp authority, credential use or notification transport'])
        put(out/'R6_REPORT.json',canonical(report).encode());return report
