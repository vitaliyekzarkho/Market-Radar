"""File-only evidence recorder. No network, market acquisition or delivery APIs.

Existing-file ingestion proves a local write now, not original historical
availability. Only externally audited receipt pins can verify old timestamps.
"""
from datetime import datetime,timezone
from pathlib import Path
from recorded_authority import Rejected, decode, sha, strict_utc
from recorded_ledger import safe_path
from recorded_context import canonical

def read_file(path,root,limit=8_000_000):
    root=safe_path(root);p=safe_path(path)
    if p==root or not p.is_relative_to(root):raise Rejected('INPUT_OUTSIDE_APPROVED_ROOT')
    if p.name.startswith('.') or p.suffix.lower() not in {'.json','.csv','.bin','.txt'}:
        raise Rejected('INPUT_TYPE_FORBIDDEN')
    before=p.stat()
    if not p.is_file() or before.st_size>limit:raise Rejected('INPUT_SIZE_INVALID')
    with p.open('rb') as f:data=f.read(limit+1)
    after=p.stat()
    if (len(data)>limit or (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino)):
        raise Rejected('INPUT_CHANGED_DURING_READ')
    return data

class EvidenceRecorder:
    def __init__(self,ledger,*,input_root):
        self.ledger=ledger;self.input_root=safe_path(input_root)
    def receive(self,key,*,envelope_path,artifact_paths,received_at_utc,clock_source,
                fail_before_commit=False,fail_after_evidence=False):
        if not strict_utc(received_at_utc) or not isinstance(clock_source,str) or not clock_source.strip():
            raise Rejected('EXPLICIT_RECEIPT_TIME_AND_CLOCK_REQUIRED')
        raw=read_file(envelope_path,self.input_root);env=decode(raw)
        if not isinstance(artifact_paths,dict):raise Rejected('ARTIFACT_MAP_REQUIRED')
        artifacts={ref:read_file(path,self.input_root) for ref,path in artifact_paths.items()}
        request={'envelope_sha256':sha(raw),'artifacts':{ref:sha(b) for ref,b in sorted(artifacts.items())},
                 'received_at_claim_utc':received_at_utc,'clock_source_claim':clock_source,
                 'authority_class':self.ledger.context.mode}
        self.ledger.integrity()
        for e in self.ledger.events():
            if e['kind']=='CAPTURE_RECORDED' and e['key']==key:
                if e['request']!=request:raise Rejected('RECORDER_IDEMPOTENCY_CONFLICT')
                return e
        assessment=self.ledger.context.assess(raw,artifacts)
        for o in env['observations']:
            # A verified claimed receipt is only attributable to this call's
            # explicit receipt time when exact; conflicting assertions reject.
            found=next(r for r in assessment['observations'] if r['observation_id']==o['observation_id'])
            if found['authority']['captured_at_utc']=='VERIFIED' and o['captured_at_utc']['at_utc']!=received_at_utc:
                raise Rejected('EXPLICIT_RECEIVE_TIME_CONFLICT')
        # Crash after evidence commit is recoverable by the same request key;
        # incomplete import is not reported as a complete capture record.
        self.ledger.ingest('recorder:'+key,raw,artifacts,fail_before_commit=fail_before_commit)
        if fail_after_evidence:raise RuntimeError('SIMULATED_CRASH_AFTER_EVIDENCE')
        now=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
        event={'kind':'CAPTURE_RECORDED','key':key,'request':request,'assessment':assessment,
               'observations':[o['observation_id'] for o in env['observations']],
               'local_persisted_at_utc':now,'local_clock_source':'system_utc',
               'local_clock_authority':'UNVERIFIED','local_clock_uncertainty_seconds':None,
               'availability_inferred_from_local_write':False}
        with self.ledger.transaction():
            # Another process may have completed the same evidence/capture
            # pair while this process was acquiring bytes. Preserve its event.
            for old in self.ledger.events():
                if old['kind']=='CAPTURE_RECORDED' and old['key']==key:
                    if old['request']!=request:raise Rejected('RECORDER_IDEMPOTENCY_CONFLICT')
                    return old
            return self.ledger.append('capture:'+key,event)
