"""One-shot recorded market bridge, with retrospective results explicitly separated."""
from pathlib import Path
from copy import deepcopy
from contextlib import ExitStack
import json,tempfile
from recorded_authority import TrustStore,Rejected,sha,decode,interval
from recorded_context import AuthorityContext,ROLES,MODES,canonical
from recorded_ledger import RecordedLedger,safe_path
from recorded_scenario import RecordedScenarios
from recorded_assessment import RecordedNotificationAudit
from evidence_recorder import read_file
from offline_integration import single_instance
from r5_input import normalize_csv,time_utc
from r5_legacy import analyze

ROOT=Path(__file__).resolve().parent
BASE='a3c4f27f5f2f9307e7e4c645b54e55ee18668caa'

def unknown():return dict(state='UNKNOWN',at_utc=None,uncertainty_seconds=None,authority_id=None,evidence_ref=None,reason='No independently verified historical computation/receipt')

def retrospective_context():
    p=dict(classification='recorded_offline',contract_version='recorded_offline/1.0.0-draft.1',policy_id='captured:r5-retrospective',
        decision_system_id='captured:unadmitted',authority_class='CAPTURED_AUTHORITY',transport_enabled=False,trading_enabled=False,
        max_clock_uncertainty_seconds=0,require_entry_permission=False,conflict_behavior='WAIT',missing_behavior='DEFERRED',
        inputs={r:dict(required=r!='news',max_age_seconds=3600,age_anchor='available_at_utc',
                       source_ids=['captured:unverified-file-import','captured:r5-derived']) for r in sorted(ROLES)})
    return AuthorityContext('CAPTURED_AUTHORITY',TrustStore(canonical(p).encode(),frozenset({'captured:unadmitted'}),
        frozenset({'captured:unverified-file-import','captured:r5-derived'}),frozenset(),
        'NO HISTORICAL AUTHORITY ADMITTED; retrospective storage only'))

def envelope(context,dataset,cutoff,observations):
    p=context.policy()
    return dict(contract_version=p['contract_version'],classification='recorded_offline',dataset_id=dataset,
                decision_system_id=p['decision_system_id'],policy_id=p['policy_id'],policy_sha256=sha(context.trust.policy_bytes),
                historical_cutoff_utc=cutoff,replay_executed_at_utc=None,observations=observations,receipts=[])

def observation(oid,source,payload,*,kind='raw',deps=None):
    return dict(observation_id=oid,logical_item_id=oid,source_id=source,capture_id=oid+':unverified-import',
        capture_stream_id=source,source_sequence=None,kind=kind,role='technical',file_ref=oid,file_sha256=sha(payload),
        payload_sha256=sha(payload),payload_locator=f'bytes:0:{len(payload)}',supersedes=None,
        derivation=None if kind=='raw' else dict(required_input_ids=deps or [],optional_input_ids=[],
            engine_sha256=sha((ROOT/'r5_legacy.py').read_bytes()),config_sha256=sha(b'R5 lossless mapping v1'),completion_receipt_ref=None),
        **{k:unknown() for k in ('market_event_at_utc','captured_at_utc','available_at_utc','scenario_established_at_utc')})

def mapped_scenario(analysis,symbol):
    payload=analysis['scenario'];p=payload.get('primary_scenario')
    if not isinstance(p,dict):return None
    reasoning=payload.get('market_state',{}).get('primary',{})
    # Local identity is an audit key, not a claim of original hypothesis formation.
    hid='replay:'+symbol+':'+str(p.get('direction'))
    a=deepcopy(reasoning.get('point_a'));b=deepcopy(reasoning.get('point_b'))
    if not a or not b:return None
    return dict(point_a=a,point_b=b,direction=p['direction'],score=p['score'],invalidation=deepcopy(p.get('conditions',{}).get('invalidation')),
        FTA=deepcopy(reasoning.get('minimum_target')),TA=deepcopy(reasoning.get('route')),permissions=deepcopy(p.get('permissions',{'entry':False})),
        primary=dict(id=hid,direction=p['direction'],parent_id=None,state='active'),nested=[],alternatives=[],
        htf=deepcopy(payload.get('market_state',{}).get('timeframe_hierarchy',{})),
        legacy_primary=deepcopy(p),legacy_market_state=deepcopy(payload.get('market_state')),
        identity_semantics='replay-local only; original historical identity/establishment unproven')

def run_replay(manifest_path,output,*,context=None,fail_after=None):
    manifest_path=safe_path(manifest_path);input_root=manifest_path.parent
    raw=read_file(manifest_path,input_root);manifest=decode(raw)
    if manifest.get('mode') not in {'retrospective','recorded'} or not isinstance(manifest.get('events'),list):raise Rejected('MANIFEST_INVALID')
    if manifest['mode']=='recorded' and context is None:raise Rejected('EXTERNAL_ADMISSION_CONTEXT_REQUIRED')
    context=retrospective_context() if manifest['mode']=='retrospective' else context
    policy=context.policy();prefix=MODES[context.mode];derivedsource=prefix+'r5-derived'
    if derivedsource not in context.trust.source_ids or derivedsource not in policy['inputs']['technical']['source_ids']:
        raise Rejected('EXPLICIT_DERIVED_SOURCE_POLICY_REQUIRED')
    dataset=manifest.get('dataset_id',prefix+'r5:'+sha(raw))
    out=safe_path(output)
    if out==ROOT or out.is_relative_to(ROOT) or out==input_root or out.is_relative_to(input_root):raise Rejected('OUTPUT_MUST_BE_SEPARATE')
    out.mkdir(parents=True,exist_ok=True)
    with single_instance(out/'r5.lock'):
        owner={'base':BASE,'manifest_sha256':sha(raw),'context':context.fingerprint()}
        marker=out/'R5_OWNER.json'
        if marker.exists():
            if decode(marker.read_bytes())!=owner:raise Rejected('OUTPUT_OWNER_MISMATCH')
        elif any(p.name!='r5.lock' for p in out.iterdir()):raise Rejected('OUTPUT_NOT_EMPTY')
        else:marker.write_text(canonical(owner),encoding='utf-8')
        ledger=RecordedLedger(out/'recorded.db',context=context,dataset_id=dataset)
        try:
            seen={};last={};previous={};results=[]
            for event in manifest['events']:
                eid=event['id'];symbol=event['symbol'];cutoff=event['cutoff']
                if not isinstance(eid,str) or not eid:raise Rejected('EVENT_ID_REQUIRED')
                if eid in seen:
                    if seen[eid]!=event:raise Rejected('EVENT_ID_CONFLICT')
                    continue
                seen[eid]=event
                moment=time_utc(cutoff)
                if symbol in last and moment<last[symbol]:raise Rejected('EVENT_ORDER_INVALID')
                last[symbol]=moment;views={};payloads={};oids=[];source_status={}
                if manifest['mode']=='recorded':
                    eraw=read_file(input_root/event['envelope'],input_root)
                    artifacts={ref:read_file(input_root/path,input_root) for ref,path in event['artifacts'].items()}
                    ledger.ingest('source:'+eid,eraw,artifacts)
                for tf,path in event['files'].items():
                    b=read_file(input_root/path,input_root);views[tf]=normalize_csv(b,symbol=symbol,timeframe=tf,cutoff=cutoff)
                    if manifest['mode']=='retrospective':
                        oid=prefix+'import:'+symbol+':'+tf+':'+sha(b)
                        o=observation(oid,'captured:unverified-file-import',b)
                        ledger.ingest('source:'+eid+':'+tf,canonical(envelope(context,dataset,cutoff,[o])).encode(),{oid:b})
                        source_status[tf]='UNKNOWN'
                    else:
                        oid=event['series_observations'][tf];rows=ledger.observations()
                        if oid not in rows or ledger.payload(oid)!=b:raise Rejected('MARKET_PAYLOAD_BINDING_CONFLICT')
                        o=rows[oid]['body'];selection=ledger.select(o['source_id'],o['logical_item_id'],cutoff)
                        source_status[tf]='VERIFIED' if selection['status']=='VERIFIED' and selection['selected']['observation_id']==oid else 'UNKNOWN_OR_SUPERSEDED'
                        if source_status[tf]=='VERIFIED':
                            rule=policy['inputs'][o['role']];anchor=rule['age_anchor']
                            if rows[oid]['assessment']['authority'][anchor]!='VERIFIED':source_status[tf]='AGE_UNKNOWN'
                            elif (moment-interval(o[anchor])[0]).total_seconds()>rule['max_age_seconds']:source_status[tf]='STALE'
                    oids.append(oid);payloads[tf]=sha(b)
                eventhash=sha(eid.encode())[:20]
                with tempfile.TemporaryDirectory(prefix='r5-',dir=out) as scratch:
                    analysis=analyze(views,root=scratch,symbol=symbol,cutoff=cutoff,previous=previous.get(symbol))
                    log=Path(scratch)/'legacy.log'
                    if log.exists():(out/(eventhash+'.log')).write_bytes(log.read_bytes())
                prior=previous.get(symbol)
                if prior and prior.get('recorded_scenario_id'):oids.append(prior['recorded_scenario_id'])
                aggregate=canonical({'symbol':symbol,'cutoff':cutoff,'raw_hashes':payloads,'analysis':analysis}).encode()
                fid=prefix+'r5-feature:'+eventhash
                f=observation(fid,derivedsource,aggregate,kind='feature',deps=oids)
                ledger.ingest('feature:'+eid,canonical(envelope(context,dataset,cutoff,[f])).encode(),{fid:aggregate})
                if fail_after==eid:raise RuntimeError('SIMULATED_R5_INTERRUPTION')
                mapped=mapped_scenario(analysis,symbol) if analysis['status']=='ANALYZED_RETROSPECTIVELY' else None
                if mapped:
                    sid=prefix+'r5-scenario:'+eventhash;body=canonical(mapped).encode()
                    s=observation(sid,derivedsource,body,kind='scenario',deps=[fid])
                    ledger.ingest('scenario-source:'+eid,canonical(envelope(context,dataset,cutoff,[s])).encode(),{sid:body})
                    refs={r:(fid if r=='technical' else None) for r in ROLES}
                    rec=RecordedScenarios(ledger).record('r5:'+eid,scenario_id=symbol,observation_id=sid,cutoff=cutoff,refs=refs,
                        event_type='HYPOTHESIS_FORMED',target_id=mapped['primary']['id'])
                    eligibility=RecordedNotificationAudit(ledger).assess('r5:'+eid,scenario_key='r5:'+eid)
                    analysis['recorded_scenario_id']=sid
                else:
                    rec=None;eligibility={'status':'DEFERRED','reasons':['NO_COMPLETE_LEGACY_SCENARIO'],'trade_authorized':False,'delivery_authorized':False}
                    with ledger.transaction():ledger.append('incomplete:'+eid,{'kind':'ANALYSIS_INCOMPLETE','id':eid,'feature_id':fid,'eligibility':eligibility})
                result=dict(event_id=eid,symbol=symbol,cutoff=cutoff,mode=manifest['mode'],
                    authority_class=context.mode if manifest['mode']=='recorded' else 'UNVERIFIED_HISTORICAL',
                    source_selection=source_status,input_views=views,analysis=analysis,recorded_scenario=rec,eligibility=eligibility,
                    historical_notification_generated=False,transport_actions=0,
                    reasoning_scope='RETROSPECTIVE_RECOMPUTATION; source availability is not derived availability')
                result['legacy_policy_scope']='RETROSPECTIVE_ONLY; not historical notification eligibility'
                results.append(result)
                if analysis['status']=='ANALYZED_RETROSPECTIVELY':previous[symbol]=analysis
            report={'version':'0.3.0-r5-bridge-review','base_sha':BASE,'mode':manifest['mode'],'events':results,
                    'historical_eligibility':'DEFERRED_FOR_RECOMPUTED_SCENARIOS','live_actions':0}
            tmp=out/'report.pending.json';tmp.write_text(canonical(report),encoding='utf-8');tmp.replace(out/'report.json')
            ledger.integrity();return report
        finally:ledger.close()
