"""Receipt persistence and time lineage shared by live acquisition and replay."""
import json,hashlib
from pathlib import Path
from mt5_clock_authority import ClockLedger,canonical,digest

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
