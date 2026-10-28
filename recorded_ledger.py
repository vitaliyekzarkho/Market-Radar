"""Separate recorded SQLite journal. No accepted synthetic code modifications."""
from pathlib import Path
from contextlib import contextmanager
import json
import sqlite3
from recorded_authority import Rejected, decode, sha, instant, interval
from recorded_context import canonical

def safe_path(path):
    path=Path(path).absolute()
    for p in (path,*path.parents):
        if p.is_symlink() or (p.exists() and getattr(p.stat(),'st_file_attributes',0)&0x400):
            raise Rejected('REPARSE_PATH_FORBIDDEN')
    path=path.resolve()
    runtime=Path('C:/Users/User/MarketRadar').resolve()
    if path==runtime or path.is_relative_to(runtime): raise Rejected('PRODUCTION_PATH_FORBIDDEN')
    return path

class RecordedLedger:
    def __init__(self,path,*,context,dataset_id):
        self.path=safe_path(path);self.context=context;self.dataset_id=dataset_id
        context.policy()
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path,timeout=0,isolation_level=None)
        try:
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS blobs (hash TEXT PRIMARY KEY,data BLOB NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, body TEXT NOT NULL, assessment TEXT NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, key TEXT UNIQUE, body TEXT NOT NULL, previous TEXT NOT NULL, hash TEXT NOT NULL)')
            owner=canonical({'domain':'recorded_offline','dataset':dataset_id,'context':context.fingerprint()})
            with self.transaction():
                old=self.db.execute("SELECT value FROM meta WHERE key='owner'").fetchone()
                if old and old[0]!=owner: raise Rejected('LEDGER_OWNER_MISMATCH')
                self.db.execute("INSERT OR IGNORE INTO meta VALUES ('owner',?)",(owner,))
            self.integrity()
        except BaseException:
            self.db.close();raise
    def close(self): self.db.close()
    @contextmanager
    def transaction(self):
        if self.db.in_transaction:
            yield
            return
        self.db.execute('BEGIN IMMEDIATE')
        try: yield;self.db.execute('COMMIT')
        except BaseException: self.db.execute('ROLLBACK');raise
    def events(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM events ORDER BY seq')]
    def append(self,key,body):
        text=canonical(body)
        old=self.db.execute('SELECT body FROM events WHERE key=?',(key,)).fetchone()
        if old:
            if old[0]!=text: raise Rejected('IDEMPOTENCY_CONFLICT')
            return json.loads(old[0])
        tail=self.db.execute('SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
        seq=tail[0]+1 if tail else 1; prev=tail[1] if tail else '0'*64
        h=sha(canonical([seq,key,text,prev]).encode())
        self.db.execute('INSERT INTO events VALUES (?,?,?,?,?)',(seq,key,text,prev,h))
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('head',?)",(canonical([seq,h]),))
        return json.loads(text)
    def integrity(self):
        prev='0'*64
        expected=0
        for expected,(seq,key,body,p,h) in enumerate(self.db.execute('SELECT * FROM events ORDER BY seq'),1):
            if seq!=expected or p!=prev or h!=sha(canonical([seq,key,body,p]).encode()): raise Rejected('JOURNAL_CORRUPT')
            prev=h
        head=self.db.execute("SELECT value FROM meta WHERE key='head'").fetchone()
        if (expected and (not head or head[0]!=canonical([expected,prev]))) or (not expected and head):
            raise Rejected('JOURNAL_HEAD_CORRUPT')
        for h,b in self.db.execute('SELECT * FROM blobs'):
            if sha(b)!=h: raise Rejected('BLOB_CORRUPT')
        ingested={}
        assessments={}
        for e in self.events():
            if e['kind']=='INGEST':
                ingested.update({o['observation_id']:o for o in e['observations']})
                for oid,a in e['assessments'].items(): assessments.setdefault(oid,a)
        stored=self.observations()
        if {k:v['body'] for k,v in stored.items()}!=ingested: raise Rejected('PROJECTION_CORRUPT')
        if {k:v['assessment'] for k,v in stored.items()}!=assessments: raise Rejected('ASSESSMENT_CORRUPT')
        for k,v in stored.items():
            if v['body']['file_sha256'] not in {r[0] for r in self.db.execute('SELECT hash FROM blobs')}:
                raise Rejected('BLOB_MISSING')
    def observations(self):
        return {k:{'body':json.loads(b),'assessment':json.loads(a)} for k,b,a in self.db.execute('SELECT * FROM observations')}
    def payload(self,oid):
        self.integrity()
        o=self.observations()[oid]['body']; b=self.db.execute('SELECT data FROM blobs WHERE hash=?',(o['file_sha256'],)).fetchone()[0]
        _,start,length=o['payload_locator'].split(':'); part=b[int(start):int(start)+int(length)]
        if sha(part)!=o['payload_sha256']: raise Rejected('PAYLOAD_CORRUPT')
        return part
    def ingest(self,key,raw,artifacts,*,fail_before_commit=False):
        with self.transaction():
            return self._ingest(key,raw,artifacts,fail_before_commit=fail_before_commit)
    def _ingest(self,key,raw,artifacts,*,fail_before_commit=False):
        self.integrity()
        env=decode(raw)
        if env['dataset_id']!=self.dataset_id: raise Rejected('DATASET_MISMATCH')
        verification=self.context.assess(raw,artifacts)
        assessment={r['observation_id']:r for r in verification['observations']}
        incoming={o['observation_id']:o for o in env['observations']}
        if any(o['file_ref'] not in artifacts for o in incoming.values()):raise Rejected('PAYLOAD_REQUIRED_FOR_STORAGE')
        allobs={k:v['body'] for k,v in self.observations().items()}
        for oid,o in incoming.items():
            if oid in allobs and allobs[oid]!=o: raise Rejected('OBSERVATION_ID_CONFLICT')
            allobs[oid]=o
        all_assessments={k:v['assessment'] for k,v in self.observations().items()}
        all_assessments.update(assessment)
        # Validate graph independently of input presentation order.
        children={}; roots={}; seqs={}; visiting=set(); done=set(); ordered=[]
        for oid,o in allobs.items():
            group=(o['source_id'],o['logical_item_id'])
            if o['supersedes']:
                pred=allobs.get(o['supersedes'])
                if pred is None or (pred['source_id'],pred['logical_item_id'])!=group: raise Rejected('CORRECTION_LINEAGE_INVALID')
                if (all_assessments[oid]['authority']['available_at_utc']=='VERIFIED'
                    and all_assessments[o['supersedes']]['authority']['available_at_utc']=='VERIFIED'
                    and interval(o['available_at_utc'])[1]<interval(pred['available_at_utc'])[0]):
                    raise Rejected('CORRECTION_CHRONOLOGY_CONFLICT')
                if o['supersedes'] in children: raise Rejected('CORRECTION_FORK')
                children[o['supersedes']]=oid
            else:
                if group in roots: raise Rejected('MULTIPLE_CHAIN_ROOTS')
                roots[group]=oid
            if o['source_sequence'] is not None:
                seqkey=(o['capture_stream_id'],o['source_sequence'])
                if seqkey in seqs and seqs[seqkey]!=oid: raise Rejected('SEQUENCE_CONFLICT')
                seqs[seqkey]=oid
        def visit(oid):
            if oid in done:return
            if oid in visiting: raise Rejected('DEPENDENCY_CYCLE')
            visiting.add(oid);o=allobs[oid]
            deps=([o['supersedes']] if o['supersedes'] else [])
            if o['derivation']: deps+=o['derivation']['required_input_ids']+o['derivation']['optional_input_ids']
            for dep in sorted(set(deps)):
                if dep not in allobs: raise Rejected('DEPENDENCY_MISSING')
                visit(dep)
            visiting.remove(oid);done.add(oid);ordered.append(oid)
        for oid in sorted(allobs): visit(oid)
        event={'kind':'INGEST','observations':[incoming[x] for x in ordered if x in incoming],
               'assessments':assessment,'receipt_hashes':sorted({r['artifact_sha256'] for r in env['receipts']}),
               'envelope_sha256':sha(canonical(dict(env,observations=sorted(env['observations'],key=lambda o:o['observation_id']),
                                    receipts=sorted(env['receipts'],key=lambda r:r['receipt_id']))).encode())}
        with self.transaction():
            for b in artifacts.values():
                if not isinstance(b,bytes): raise Rejected('ARTIFACT_BYTES_REQUIRED')
                self.db.execute('INSERT OR IGNORE INTO blobs VALUES (?,?)',(sha(b),b))
            for oid in ordered:
                if oid in incoming:
                    self.db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?)',(oid,canonical(incoming[oid]),canonical(assessment[oid])))
            result=self.append('ingest:'+key,event)
            if fail_before_commit: raise RuntimeError('SIMULATED_CRASH_BEFORE_COMMIT')
        return result
    def select(self,source_id,item_id,cutoff):
        self.integrity(); at=instant(cutoff)
        rows=self.observations(); chain=[v for v in rows.values() if v['body']['source_id']==source_id and v['body']['logical_item_id']==item_id]
        if not chain:return {'status':'UNKNOWN','reason':'MISSING','selected':None}
        children={v['body']['supersedes']:v for v in chain if v['body']['supersedes']}
        node=next(v for v in chain if v['body']['supersedes'] is None);selected=None
        while node:
            o=node['body'];a=node['assessment']
            if (a['authority']['available_at_utc']!='VERIFIED' or any(x in a['reasons'] for x in ['SOURCE_UNVERIFIED','PAYLOAD_UNAVAILABLE','CLOCK_ORDER_UNPROVEN'])
                or a['authority']['captured_at_utc']!='VERIFIED' or a['authority']['market_event_at_utc']!='VERIFIED'):
                return {'status':'UNKNOWN','reason':'HEAD_UNKNOWN','selected':None}
            lower,upper=interval(o['available_at_utc'])
            if o['supersedes']:
                predecessor=rows[o['supersedes']]['body']
                if interval(predecessor['available_at_utc'])[1]>lower:
                    return {'status':'UNKNOWN','reason':'CORRECTION_ORDER_UNPROVEN','selected':None}
            if upper>at:
                if lower<=at:return {'status':'UNKNOWN','reason':'CUTOFF_UNCERTAIN','selected':None}
                break
            selected=o;node=children.get(o['observation_id'])
        if selected is None:return {'status':'UNKNOWN','reason':'FUTURE','selected':None}
        return {'status':'VERIFIED','reason':None,'selected':selected}
    def dependency_status(self,oid,cutoff,seen=None):
        seen=set() if seen is None else set(seen)
        if oid in seen: raise Rejected('DEPENDENCY_CYCLE')
        seen.add(oid);rows=self.observations()
        if oid not in rows:return 'UNKNOWN'
        o=rows[oid]['body']; sel=self.select(o['source_id'],o['logical_item_id'],cutoff)
        if sel['status']!='VERIFIED' or sel['selected']['observation_id']!=oid:return 'UNKNOWN'
        if not o['derivation']:return 'VERIFIED'
        for dep in o['derivation']['required_input_ids']+o['derivation']['optional_input_ids']:
            if self.dependency_status(dep,cutoff,seen)!='VERIFIED':return 'UNKNOWN'
            d=rows[dep]['body']
            if interval(d['available_at_utc'])[1]>interval(o['available_at_utc'])[0]:return 'UNKNOWN'
        # Completion proof binds output as well as full derivation metadata.
        rid=o['derivation']['completion_receipt_ref']
        if rid is None:return 'UNKNOWN'
        for h,b in self.db.execute('SELECT * FROM blobs'):
            if h not in self.context.trust.audited_receipt_hashes:continue
            try:r=decode(b)
            except Rejected:continue
            if not isinstance(r,dict) or r.get('receipt_id')!=rid:continue
            if (r.get('kind')=='derivation_completion' and r.get('subject_id')==oid
                and r.get('subject_payload_sha256')==o['payload_sha256']
                and r.get('authority_id') in self.context.trust.authority_ids
                and r.get('source_id')==o['source_id']
                and r.get('decision_system_id')==self.context.policy()['decision_system_id']
                and r.get('derivation_sha256')==sha(canonical(o['derivation']).encode())
                and interval(r)[1]<=interval(o['available_at_utc'])[0]):return 'VERIFIED'
        return 'UNKNOWN'
