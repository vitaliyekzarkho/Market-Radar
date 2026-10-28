"""Audit-only observer of the existing pipeline. No acquisition or transport.

Recorded observations remain UNKNOWN without admitted historical authority.
The observer never grants permissions, recalculates scores or changes alert policy.
"""
import json,hashlib,uuid,shutil
from pathlib import Path
from datetime import datetime,timezone
from copy import deepcopy
from recorded_context import canonical,ROLES
from recorded_ledger import RecordedLedger
from recorded_assessment import RecordedNotificationAudit
from recorded_scenario import RecordedScenarios
from r5_bridge import retrospective_context,envelope,observation
from evidence_recorder import EvidenceRecorder
from cdr_news_bridge import connect_news
from decision_journal import DecisionJournal
from news_lifecycle_store import Store
from news_lifecycle_engine import Lifecycle
from news_notification_policy import Policy
from historical_evidence_assessment import assess_source

VERSION='0.5.2-hardening-review'
def sha(b):return hashlib.sha256(b).hexdigest()
def now():return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def load(p):
    return json.loads(p.read_text('utf-8-sig')) if p.exists() else {}
def write(p,value):
    p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix(p.suffix+'.pending');tmp.write_text(canonical(value),encoding='utf-8');tmp.replace(p)

def snapshot(root,out):
    """Content-address candidate inputs, never claim all files were consumed."""
    items={}
    for p in sorted((root/'data').rglob('*')):
        rel=p.relative_to(root/'data')
        if not p.is_file() or p.suffix not in {'.json','.csv'}:continue
        if rel.parts[0] in {'cdr','runtime','telegram','notifications'}:continue
        if any(k in p.name.lower() for k in ('credential','token','secret','account')):continue
        raw=p.read_bytes()
        if p.read_bytes()!=raw:raise RuntimeError('INPUT_CHANGED_DURING_CAPTURE')
        key=sha(raw);dst=out/'blobs'/key
        dst.parent.mkdir(parents=True,exist_ok=True)
        if dst.exists() and dst.read_bytes()!=raw:raise RuntimeError('BLOB_CONFLICT')
        if not dst.exists():dst.write_bytes(raw)
        items[str(rel).replace('\\','/')]={'sha256':key,'bytes':len(raw)}
    return items

def mapped(payload):
    """Preserve all actual hypotheses, never invent historical formation time."""
    if payload.get('publication_state')=='silent' and payload.get('primary_scenario') is None:
        m=deepcopy(payload.get('market_state') or {})
        return dict(observation_type='NON_PUBLISHABLE',publication_state='silent',
            point_a=None,point_b=None,direction=None,score=None,invalidation=None,FTA=None,TA=None,
            primary=None,nested=[{'role':k,'original':m[k]} for k in
                ('active_counter_leg','counter_candidate','continuation_setup') if m.get(k)],
            alternatives=[],htf=m.get('timeframe_hierarchy'),entry_allowed=False,
            permissions={'entry':False,'trade':False},legacy_primary=None,legacy_market_state=m,
            identity_semantics='No trading hypothesis formed; context retained without synthetic direction')
    p=deepcopy(payload.get('primary_scenario') or {});m=payload.get('market_state') or {}
    main=m.get('primary') or {};struct=m.get('primary_structural_leg') or {}
    anchor=struct.get('point_a') or p.get('point_a')
    primary_id='observed_'+sha(canonical([p.get('direction'),anchor]).encode())[:24]
    primary={'id':primary_id,'direction':p.get('direction'),'parent_id':None,'state':'active' if p.get('active',True) else 'inactive','original':p}
    nested=[]
    for name in ('active_counter_leg','counter_candidate','continuation_setup'):
        value=m.get(name)
        if isinstance(value,dict) and value:
            ident=value.get('lifecycle_id') or value.get('id') or sha(canonical([primary_id,name,value.get('direction'),value.get('point_a')]).encode())[:24]
            nested.append({'id':str(ident),'direction':value.get('direction'),'parent_id':primary_id,'state':value.get('state','unknown'),'original':deepcopy(value)})
    alternatives=[]
    value=m.get('alternative')
    if isinstance(value,dict) and value:
        alternatives=[{'id':'alternative_'+sha(canonical([primary_id,value.get('direction')]).encode())[:24], 'direction':value.get('direction'),'parent_id':primary_id,'state':value.get('status','unknown'),'original':deepcopy(value)}]
    return dict(point_a=deepcopy(anchor),point_b=deepcopy(main.get('point_b') or p.get('point_b')),direction=p.get('direction'),score=p.get('score'),
        invalidation=deepcopy(p.get('conditions',{}).get('invalidation')),FTA=deepcopy(main.get('minimum_target')),TA=deepcopy(main.get('route')),
        permissions={'entry':False,'trade':False},production_permissions=deepcopy(p.get('permissions')),permission_origin='audit always denies; original preserved separately',
        primary=primary,nested=nested,alternatives=alternatives,htf=deepcopy(m.get('timeframe_hierarchy')),
        legacy_primary=p,legacy_market_state=deepcopy(m),identity_semantics='observation identity only; original establishment UNKNOWN')

def observe(root,stage,cycle_id):
    root=Path(root).resolve();out=root/'data/cdr';out.mkdir(parents=True,exist_ok=True)
    context=retrospective_context()
    ledger=RecordedLedger(out/'recorded.db',context=context,dataset_id='captured:operational-audit-v1')
    try:
        key=cycle_id+':'+stage
        previous=[e for e in ledger.events() if e.get('audit_key')==key]
        if previous:
            publish_journal(out,ledger)
            return previous[-1]
        timestamp=now();packet=snapshot(root,out)
        packet_raw=canonical({'files':packet,'local_observed_at_utc':timestamp,'authority':'LOCAL_UNVERIFIED','stage':stage,'cycle':cycle_id}).encode()
        oid='captured:packet:'+sha(packet_raw)
        op=observation(oid,'captured:unverified-file-import',packet_raw)
        env=envelope(context,ledger.dataset_id,timestamp,[op]);inp=out/'intake'/sha(key.encode());inp.mkdir(parents=True,exist_ok=True)
        (inp/'packet.json').write_bytes(packet_raw);write(inp/'envelope.json',env)
        EvidenceRecorder(ledger,input_root=inp).receive(key,envelope_path=inp/'envelope.json',artifact_paths={oid:inp/'packet.json'},received_at_utc=timestamp,clock_source='LOCAL_UNVERIFIED')
        record={'kind':'OPERATIONAL_STAGE','audit_key':key,'stage':stage,'cycle_id':cycle_id,'at_utc':timestamp,'input_packet':oid,'input_scope':'candidate files captured at boundary, not verified read-set','status':'DEGRADED','trade_authorized':False}
        if (root/'data/time_authority/latest.json').exists():
            from time_ingestion import load_lineage
            record['time_lineage']=load_lineage(root)
            record['time_sensitive_assessment']='DEFERRED'
        if stage=='scenario':
            scenario=load(root/'data/scenario_states/GOLD_pro.json')
            news=load(root/'data/news/GOLD_pro.json');causal=load(root/'data/news/GOLD_pro_live_causal.json')
            bridge=connect_news(scenario.get('primary_scenario',{}),load(root/'data/news/GOLD_pro_reaction_windows.json'),news,cutoff=timestamp)
            explanation={'news_context':bridge['adapter']}
            technical=mapped(scenario);raw=canonical(technical).encode();sid='captured:scenario:'+sha(key.encode())
            refs={role:(oid if role=='technical' else None) for role in ROLES}
            role_files={'news':'news/GOLD_pro.json','usd':'market_context/GOLD_pro.json',
                        'yields':'macro/rates_context.json','volume':'market_context/GOLD_pro_volume.json'}
            role_obs=[];role_bytes={}
            for role,relative in role_files.items():
                captured=packet.get(relative)
                if captured is None:continue
                body=(out/'blobs'/captured['sha256']).read_bytes()
                rid='captured:role:'+role+':'+captured['sha256']
                ro=observation(rid,'captured:unverified-file-import',body);ro['role']=role
                role_obs.append(ro);role_bytes[rid]=body;refs[role]=rid
            if role_obs:ledger.ingest(key+':roles',canonical(envelope(context,ledger.dataset_id,timestamp,role_obs)).encode(),role_bytes)
            sob=observation(sid,'captured:r5-derived',raw,kind='scenario',deps=[x for x in refs.values() if x])
            # Map exact code/config bytes; no synthetic establishment or availability.
            sob['derivation']['engine_sha256']=sha(Path(__file__).read_bytes())
            ledger.ingest(key+':scenario',canonical(envelope(context,ledger.dataset_id,timestamp,[sob])).encode(),{sid:raw})
            if technical.get('observation_type')=='NON_PUBLISHABLE':
                from nonpublishable_observation import record_nonpublishable
                observation_result,eligibility=record_nonpublishable(ledger,key,observation_id=sid,cutoff=timestamp,refs=refs)
            else:
                observation_result=RecordedScenarios(ledger).observe_snapshot(key,scenario_id='GOLD.pro',observation_id=sid,cutoff=timestamp,refs=refs)
                eligibility=RecordedNotificationAudit(ledger).assess(key+':eligibility',scenario_key=key)
            store=None
            try:
                store=Store(out/'news.db',namespace='operational_audit',mode='operational_audit')
                life=Lifecycle(store,Policy('audit_v1',86400,3600,900,300,3600,1,60,30))
                life.expire(now=timestamp)
                lifecycle=life.observe_local(bridge,now=timestamp)
                lifecycle['transport_enabled']=False
            except ValueError as exc:
                lifecycle={'status':'DEFERRED','reason':'LIFECYCLE_STORE_CONTRACT: '+str(exc),'transport_enabled':False}
            finally:
                if store:store.close()
            historical=assess_source((root/'data').resolve(),'news/GOLD_pro.json')
            record.update(scenario_id=sid,scenario_version_sha256=sha(raw),technical=technical,assessment=observation_result,eligibility=eligibility,
                news_context=explanation['news_context'],news_evidence=bridge,news_lifecycle=lifecycle,historical_recheck=historical,
                causal_result=causal,causal_scope='unmodified actual producer result; not promoted to independent news/PA proof',
                reasons=['HISTORICAL_AVAILABILITY_UNKNOWN','NEWS_LIFECYCLE_DEFERRED','AUDIT_ONLY_NON_GATING'])
            scenario['cdr_audit']={'cycle_id':cycle_id,'scenario_id':sid,'assessment_key':key,'status':'DEGRADED','ledger':'data/cdr/recorded.db','news_context':explanation['news_context']}
            write(root/'data/scenario_states/GOLD_pro.json',scenario)
        elif stage in {'policy','notification','delivery'}:
            stages=[e for e in ledger.events() if e.get('cycle_id')==cycle_id and e.get('stage')=='scenario']
            if not stages:raise RuntimeError('SCENARIO_AUDIT_MISSING')
            src=stages[-1];record.update(scenario_id=src['scenario_id'],assessment_key=src['audit_key'],eligibility=src['eligibility'])
            policy=load(root/'data/alert_policy/GOLD_pro.json')
            if src['technical'].get('observation_type')=='NON_PUBLISHABLE':
                if policy.get('should_notify') or policy.get('notification_type') not in {None,'NONE'}:
                    raise RuntimeError('NO_PRIMARY_POLICY_CONFLICT')
                record.update(outcome='NO_NOTIFICATION',non_assessment_reasons=src['eligibility']['reasons'])
            record.update(existing_radar_decision=policy.get('notification_type'),should_notify=policy.get('should_notify'),reason_codes=policy.get('reason_codes'))
            if stage=='notification':
                p=root/'data/notifications/GOLD_pro.json';msg=load(p)
                if src['technical'].get('observation_type')=='NON_PUBLISHABLE' and (msg.get('message') or msg.get('should_send')):
                    raise RuntimeError('NO_PRIMARY_NOTIFICATION_CONFLICT')
                record['message_sha256']=sha(str(msg.get('message','')).encode())
                msg['cdr_audit']={'decision_key':cycle_id+':policy','assessment_key':src['audit_key'],'scenario_id':src['scenario_id'],'status':'DEGRADED'};write(p,msg)
            if stage=='delivery':
                t=load(root/'data/telegram/GOLD_pro.json')
                record['transport_observation']={k:t.get(k) for k in ('status','reason','message_digest')}
                record['confirmed_human_delivery']=False
        with ledger.transaction():record=ledger.append(key+':stage',record)
        write(out/'latest.json',record)
        publish_journal(out,ledger)
        return record
    finally:ledger.close()

def publish_journal(out,ledger):
    journal=[e for e in ledger.events() if e.get('kind')=='OPERATIONAL_STAGE']
    status=DecisionJournal(out/'journal').sync(journal)
    # Bounded, disposable reading view; full history is the linked JSONL chain.
    write(out/'DECISION_JOURNAL.json',journal[-4:])
    lines=['# GOLD.pro - latest decision stages','Full append-only history: journal/segment-*.jsonl. Historical availability UNKNOWN.']
    for e in journal[-4:]:
        lines+=['', '## '+e['at_utc']+' '+e['stage'],'```json',json.dumps(e,ensure_ascii=False,indent=2),'```']
    (out/'DECISION_JOURNAL.md').write_text('\n'.join(lines),encoding='utf-8')
    status['retention']='No receipts, blobs, SQLite events or segments automatically deleted'
    status['sqlite_bytes']=ledger.path.stat().st_size
    status['retained_bytes']=sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    status['free_disk_bytes']=shutil.disk_usage(out).free
    status['storage_warning']=status['retained_bytes']>1024**3 or status['free_disk_bytes']<2*1024**3
    write(out/'journal_status.json',status)

def safe_observe(root,stage,cycle_id):
    try:return observe(root,stage,cycle_id)
    except Exception as exc:
        # Never let an audit fault change the existing signal; make fault visible.
        error={'status':'DEGRADED','stage':stage,'cycle_id':cycle_id,'error_type':type(exc).__name__,'reason':str(exc),'trade_authorized':False}
        try:
            write(Path(root)/'data/cdr/degraded.json',error)
            write(Path(root)/('data/cdr/error_'+cycle_id+'_'+stage+'.json'),error)
        except OSError:
            print('CDR_AUDIT_PERSIST_FAILED')
        print('CDR_AUDIT_DEGRADED: '+stage+' '+type(exc).__name__+' '+str(exc))
        return error
