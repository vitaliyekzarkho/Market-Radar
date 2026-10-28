"""Bounded synthetic integration only. Never imports production acquisition/transport."""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import os
import sys

BASE_SHA = 'a3c4f27f5f2f9307e7e4c645b54e55ee18668caa'
ROOT = Path(__file__).resolve().parent
CASES = ('normal', 'missing_yields', 'stale_yields', 'conflicting_news', 'unknown_news')
GUARDED = False


def install_guard():
    global GUARDED
    if GUARDED:
        return
    def audit(event, args):
        if event in {'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo', 'subprocess.Popen', 'os.system'}:
            raise PermissionError('OFFLINE_EXTERNAL_ACTION_FORBIDDEN')
        if event == 'import' and args[0].split('.')[0] in {'MetaTrader5', 'telegram_notifier', 'mt5_reader', 'run_radar', 'run_cycle'}:
            raise ImportError('OFFLINE_MODULE_FORBIDDEN')
        if event in {'open', 'sqlite3.connect'} and args and isinstance(args[0], (str, bytes, os.PathLike)):
            path = os.path.abspath(os.fsdecode(args[0])).replace('/', '\\').casefold()
            if path == r'c:\users\user\marketradar' or path.startswith('c:\\users\\user\\marketradar\\'):
                raise PermissionError('PRODUCTION_PATH_FORBIDDEN')
            if Path(path).name.startswith('.env'):
                raise PermissionError('CREDENTIAL_FILE_FORBIDDEN')
    sys.addaudithook(audit)
    # Do not propagate credentials to analysis code. No subprocesses are permitted.
    allowed = {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATH', 'COMSPEC', 'PATHEXT'}
    for name in list(os.environ):
        if name.upper() not in allowed:
            del os.environ[name]
    GUARDED = True


@contextmanager
def single_instance(path):
    import msvcrt
    with open(path, 'a+b') as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('OFFLINE_ALREADY_RUNNING') from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def fixture():
    return dict(classification='synthetic', instrument='SYNTHETIC_XAUUSD', price=4100.0,
        point_b_established_at_utc='2026-10-21T09:00:00Z',
        objects=[dict(name='synthetic D1 rejection', kind='fvg', timeframe='D1', directional_role='resistance',
            low=4180.0, high=4200.0, midpoint=4190.0, state='swept_rejected',
            native_confirmation=dict(confirmed=True, rejection_direction='bearish', rejection_candle=dict(time='2026-10-21T09:00:00Z', high=4200.0))),
            dict(name='PWL',kind='key_level',timeframe='W1',low=4073.545,high=4073.545,state='untouched')],
        order_flow={'timeframes':{'D1':{'delivery_direction':'bearish','delivery_state':'active'},
            'H1':{'active_leg':{'direction':'bearish','confirmed':True},'delivery_direction':'bearish','delivery_state':'active'}}})


def analyze(inputs):
    if inputs.get('classification') != 'synthetic' or inputs.get('instrument') != 'SYNTHETIC_XAUUSD':
        raise ValueError('SYNTHETIC_INPUT_REQUIRED')
    from scenario_state import build_direction, select_primary_stateful
    from market_reasoning_engine import build_reasoning_state
    from event_monitor import scenario_snapshot, compare_snapshots
    from alert_policy import policy_decision
    from notification_builder import build_message
    branches = [build_direction(d,'bearish',[],inputs['objects'],{},[],inputs['price']) for d in ('bearish','bullish')]
    primary, transition = select_primary_stateful(branches, 'bearish', None)
    if primary is None:
        raise ValueError('NO_SYNTHETIC_TECHNICAL_SCENARIO')
    reasoning = build_reasoning_state(objects=inputs['objects'],session_data={},order_flow=inputs['order_flow'],
        branches=branches,primary=primary,transition=transition,
        volume_context={},news_context={},price=inputs['price'])
    payload = dict(schema_version=2,symbol=inputs['instrument'],generated_at_utc='2026-10-21T10:00:00Z',
        current_price=inputs['price'],primary_scenario=primary,internal_branches=branches,market_state=reasoning,
        driver_context={},usd_context={},macro_context={})
    inactive = deepcopy(payload); inactive['primary_scenario'] = None
    now = datetime(2026,10,21,10,tzinfo=timezone.utc)
    _, initial = policy_decision({}, inactive, {'events':[]}, now=now)
    events = compare_snapshots(scenario_snapshot(inactive),scenario_snapshot(payload))
    policy, _ = policy_decision(initial,payload,{'events':events},now=now)
    message = build_message(payload,policy,preview=True)
    return dict(payload=payload,events=events,policy=policy,message=message,
        point_b_established_at_utc=inputs.get('point_b_established_at_utc'))


def map_scenario(analysis):
    p=analysis['payload']['primary_scenario']
    reasoning=analysis['payload']['market_state']['primary']
    point_a=deepcopy(reasoning['point_a']); point_b=deepcopy(reasoning['point_b'])
    point_a.update(time=point_a.get('confirmed_at'),definition=point_a.get('name'))
    point_b.update(time=analysis.get('point_b_established_at_utc'),definition=point_b.get('name'))
    # A lossless extension retains the exact legacy semantics. No invented trade permission.
    permissions=p.get('permissions', {'entry':False,'manual_review':True})
    return dict(classification='synthetic',instrument='SYNTHETIC_XAUUSD',timeframe='H1',direction=p['direction'],
        point_a=point_a,point_b=point_b,score=p['score'],
        primary=dict(id='primary',direction=p['direction'],state='active',parent_id=None),nested=[],alternatives=[],
        htf=deepcopy(analysis['payload']['market_state'].get('timeframe_hierarchy',{})),
        market_structure=deepcopy(analysis['payload']['market_state']),
        FTA=deepcopy(reasoning['minimum_target']),TA=deepcopy(reasoning['route']),
        liquidity_objectives=[deepcopy(p['point_b'])],invalidation=deepcopy(p['conditions'].get('invalidation')),
        permissions=deepcopy(permissions),legacy_primary=deepcopy(p),permission_provenance='legacy-explicit-or-conservative-deny')


def run_case(output, case='normal', fail_after=None):
    install_guard()
    if case not in CASES:
        raise ValueError('UNKNOWN_SYNTHETIC_CASE')
    output=Path(output).resolve()
    if output == ROOT or output.is_relative_to(ROOT):
        raise ValueError('OUTPUT_MUST_BE_OUTSIDE_BUILD')
    runtime=Path(r'C:\Users\User\MarketRadar').resolve()
    if output==runtime or output.is_relative_to(runtime):
        raise ValueError('PRODUCTION_OUTPUT_FORBIDDEN')
    if any(p.is_symlink() or (p.exists() and getattr(p.stat(), 'st_file_attributes',0)&0x400) for p in (output,*output.parents)):
        raise ValueError('REPARSE_OUTPUT_FORBIDDEN')
    output.mkdir(parents=True,exist_ok=True)
    with single_instance(output/'integration.lock'):
        marker=output/'OFFLINE_ONLY.json'
        if marker.exists():
            if json.loads(marker.read_text()) != {'base':BASE_SHA,'classification':'synthetic'}:
                raise ValueError('OUTPUT_OWNER_MISMATCH')
        elif any(p.name!='integration.lock' for p in output.iterdir()):
            raise ValueError('OUTPUT_NOT_EMPTY')
        else:
            marker.write_text(json.dumps({'base':BASE_SHA,'classification':'synthetic'}))
        private=output/case;private.mkdir(exist_ok=True)
        from offline_evidence_ledger import Ledger,canonical,digest
        from offline_scenario_ledger import ScenarioLedger,ROLES
        from offline_notification_audit import NotificationAudit
        from offline_shadow_assessment import replay
        from offline_shadow_boundary import fingerprint
        inputs=fixture(); analysis=analyze(deepcopy(inputs)); scenario=map_scenario(analysis)
        before=fingerprint(analysis)
        now='2026-10-21T10:00:00Z'
        clock_value=dict(at_utc=now,session_id='synthetic:integration',monotonic=1,uncertainty_seconds=0,health_id='synthetic:healthy')
        clock=lambda:deepcopy(clock_value)
        cp=dict(classification='synthetic',policy_id='synthetic:integration-clock',max_uncertainty_seconds=0)
        with ExitStack() as stack:
            d1=Ledger(private/'d1.db',clock=clock,policy=cp,private_root=private);stack.callback(d1.close)
            d2=ScenarioLedger(private/'d2.db',evidence=d1,clock=clock,clock_policy=cp,private_root=private);stack.callback(d2.close)
            d3=NotificationAudit(private/'d3.db',decisions=d2,clock=clock,clock_policy=cp,private_root=private);stack.callback(d3.close)
            refs={r:None for r in ROLES}
            role_payloads={'technical':dict(inputs=inputs,analysis=analysis),'usd':{'state':'synthetic-neutral'},
                'yields':{'state':'synthetic-neutral'},'volume':{'state':'synthetic-neutral'}}
            if case=='missing_yields': del role_payloads['yields']
            if case=='conflicting_news':role_payloads['news']={'status':'conflicts','explanation':'synthetic conflicting news'}
            for role,value in role_payloads.items():
                raw=canonical(value).encode()
                observed='2026-10-20T10:00:00Z' if role=='yields' and case=='stale_yields' else now
                meta=dict(source_id='synthetic:'+role,item_id=role,source_revision='1',schema_version='synthetic:integration-v1',
                    content_hash=digest(raw),published={'at_utc':observed,'authority':'synthetic:publisher'},
                    observed={'at_utc':observed,'authority':'synthetic:receipt'},quality='synthetic',validation_status='valid',
                    attestation=dict(verification_method='synthetic_fixture_attestation',claimed_source_identity='synthetic:'+role,
                        lineage=['synthetic:'+role],uncertainty='synthetic only',independent=False))
                vid=d1.ingest(role,meta,payload=raw);d1.publish(vid)
                refs[role]=dict(source_id=meta['source_id'],item_id=role)
            cutoff=d1.cutoff(now)
            if fail_after=='evidence':raise RuntimeError('SIMULATED_INTERRUPTION')
            version=d2.record('integration',kind='HYPOTHESIS_FORMED',scenario_id='synthetic:integration',scenario=scenario,
                cutoff=cutoff,refs=refs,authority=dict(kind='synthetic_technical_authority',engine_version='synthetic:legacy-integration'))
            ep=dict(classification='synthetic',policy_id='synthetic:integration-eval',wait_on_conflict=True,
                inputs={r:dict(required=(r!='news' or case=='unknown_news'),max_age_seconds=3600,age_anchor='observed',
                    source_id='synthetic:'+r,accepted_verification_methods=['synthetic_fixture_attestation']) for r in ROLES})
            news=None
            if refs['news']:
                selected=d1.select('synthetic:news','news',cutoff)['selected']
                news=dict(evidence_version_id=selected['version']['version_id'],interpretation=role_payloads['news'])
            evaluation=d2.evaluate('integration',scenario_version_id=version['version_id'],policy=ep,engine_version='synthetic:integration',news=news)
            # Normalize actual journal references to the accepted D4/D5 interchange shape.
            case_input=json.loads((ROOT/'tests/fixtures/d5/base.json').read_text())
            case_input.update(case_id='synthetic:integration:'+case,scope='synthetic_ledgers')
            step=case_input['steps'][0]; bundle=step['snapshot']; data=bundle['payload']
            selected=evaluation['manifest']['inputs']['technical']['selected']; v=selected['version'];m=v['metadata'];ready=selected['ready']
            step['cutoff']['maximum_visible_ledger_sequence']=cutoff['maximum_visible_ledger_sequence']
            bundle['cutoff']=deepcopy(step['cutoff'])
            data['d1'].update(source_id=m['source_id'],item_id=m['item_id'],version_id=v['version_id'],selected_version_id=v['version_id'],
                content_hash=m['content_hash'],evidence_payload=role_payloads['technical'],published=m['published'],observed=m['observed'],
                attestation=m['attestation'],available_at_utc=ready['available_at_utc'],ready_sequence=ready['sequence'])
            d=data['d2'];d.update(scenario_id=version['scenario_id'],scenario_version_id=version['version_id'],scenario=scenario,
                cutoff=cutoff,created_at_utc=version['created_at_utc'],formation_at_utc=version['formation_at_utc'],authority=version['authority'])
            d['evidence_ref']=dict(version_id=v['version_id'],content_hash=m['content_hash'],cutoff=cutoff)
            step['formation']={'primary':version['formation_at_utc']}
            step['targets'].update(original_point_b=scenario['point_b'],current_point_b=scenario['point_b'])
            step['evidence']=[dict(id=v['version_id'],source_id=m['source_id'],item_id=m['item_id'],revision='1',predecessor_id=None,
                observed_at_utc=m['observed']['at_utc'],available_at_utc=ready['available_at_utc'],sequence=ready['sequence'],
                payload=role_payloads['technical'],content_hash=m['content_hash'],status='current',relation='supports',timeframe='H1',max_age_seconds=3600)]
            ev=data['d3']['evaluation'];ev.update(evaluation_id=evaluation['evaluation_id'],scenario_version_id=version['version_id'],cutoff=cutoff,
                evaluated_at_utc=evaluation['evaluated_at_utc'],technical_context_hash=fingerprint(scenario),manifest_fingerprint=fingerprint(d['evidence_ref']),
                knowledge=evaluation['knowledge'],action=evaluation['action'],news_status=evaluation['news_status'])
            clock_value.update(at_utc=step['cutoff']['as_of_utc'],monotonic=11)
            request=dict(evaluation_id=evaluation['evaluation_id'],hypothesis_id='primary',episode_id=data['d3']['episode_id'],
                event_type='NEW',policy=data['d3']['policy'],recipients=data['d3']['recipients'],reason='synthetic integration')
            bundle['envelope']['content_hash']=fingerprint(data);bundle['envelope']['raw_payload_ref']='sha256:'+fingerprint(data)
            step['snapshot_hash']=fingerprint(bundle)
            report=replay(canonical(case_input).encode(),ledger_context={step['event_id']:{'audit':d3,'request':request}}).report
            notification=report['timeline'][0]['notification']
            generated=None
            # A synthetic record only: never preparation, delivery or provider calls.
            if notification['status']=='ELIGIBLE' and analysis['policy'].get('should_notify'):
                elig=next(x for x in d3.projection()['eligibility'].values() if fingerprint(x['eligibility_id'])==notification['eligibility_ref'])
                generated=d3.generate('integration-output',eligibility_id=elig['eligibility_id'],
                    message=dict(title='SYNTHETIC OFFLINE PREVIEW',summary=analysis['message'],action_labels=['Open chart']))
            assert fingerprint(analysis)==before and scenario['legacy_primary']==analysis['payload']['primary_scenario']
            result=dict(base_sha=BASE_SHA,classification='synthetic',case=case,legacy=analysis,mapped_scenario=scenario,
                evaluation=evaluation,assessment=report,notification=notification,synthetic_notification=generated,
                external_actions=0,legacy_transport_enabled=False,ledger_counts=[len(d1.events()),len(d2.events()),len(d3.events())])
            if fail_after=='generation':raise RuntimeError('SIMULATED_INTERRUPTION')
            tmp=private/'report.tmp';tmp.write_text(canonical(result),encoding='utf-8');tmp.replace(private/'report.json')
            return result


def main():
    parser=argparse.ArgumentParser(description='OFFLINE synthetic integration; no live option')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case',choices=CASES,default='normal')
    args=parser.parse_args()
    result=run_case(args.output,args.case)
    print(json.dumps(dict(case=args.case,notification=result['notification']['status'],d5=result['assessment']['final_status'],external_actions=0)))


if __name__=='__main__':
    main()
