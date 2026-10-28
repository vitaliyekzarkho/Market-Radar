"""Invoke audited legacy calculation pipeline, with isolated paths and virtual clock."""
from contextlib import contextmanager,ExitStack,redirect_stdout
from datetime import datetime
from pathlib import Path
import importlib,io,json,threading
from copy import deepcopy
from r5_input import legacy_csv,time_utc

CORE=('W1','D1','H4','H1','M15')
MODULES=('market_features','key_levels','dealing_range_engine','order_flow_structure','session_context','level_state','scenario_state')
LOCK=threading.RLock()

@contextmanager
def bind(module,root,symbol,cutoff):
    saved={};filename=symbol.replace('.','_');at=time_utc(cutoff)
    class ReplayDatetime(datetime):
        @classmethod
        def now(cls,tz=None):return at.astimezone(tz) if tz else at.replace(tzinfo=None)
    for name,value in list(vars(module).items()):
        new=value
        if isinstance(value,Path) and not value.is_absolute():new=root/Path(str(value).replace('GOLD_pro',filename))
        elif name=='SYMBOL':new=symbol
        elif name=='SYMBOL_FILE':new=filename
        elif name=='datetime':new=ReplayDatetime
        if new is not value:saved[name]=value;setattr(module,name,new)
    try:yield
    finally:
        for name,value in saved.items():setattr(module,name,value)

def analyze(views,*,root,symbol,cutoff,previous=None):
    root=Path(root);root.mkdir(parents=True,exist_ok=True);filename=symbol.replace('.','_')
    missing=[tf for tf in CORE if tf not in views or len(views[tf]['rows'])<7]
    missingvolume=[tf for tf in CORE if tf in views and views[tf]['missing_tick_volume_rows']]
    missingbroker=[tf for tf in CORE if tf in views and not {'broker_time','time'}<=set(views[tf]['header'])]
    if missing or missingvolume or missingbroker:return {'status':'INSUFFICIENT_DATA','reasons':{'required_timeframes':missing,'legacy_requires_tick_volume':missingvolume,'legacy_requires_broker_time':missingbroker},'calls':[]}
    rawdir=root/'data/raw'/filename;rawdir.mkdir(parents=True,exist_ok=True)
    for tf,v in views.items():(rawdir/(tf+'.csv')).write_bytes(legacy_csv(v))
    if previous and previous.get('scenario'):
        p=root/'data/scenario_states'/f'{filename}.json';p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(previous['scenario'],ensure_ascii=False),encoding='utf-8')
    trace=[];log=io.StringIO();corrections=[]
    with LOCK,ExitStack() as stack,redirect_stdout(log):
        modules={name:importlib.import_module(name) for name in MODULES}
        for m in modules.values():stack.enter_context(bind(m,root,symbol,cutoff))
        pulse=importlib.import_module('market_pulse_engine');stack.enter_context(bind(pulse,root,symbol,cutoff))
        if 'M5' in views and views['M5']['rows'] and not views['M5']['missing_tick_volume_rows']:
            pulse_rows=pulse.normalize_rows([r['original'] for r in views['M5']['rows']])
            pulse_payload=pulse.build_payload(pulse_rows,(previous or {}).get('pulse'),source='recorded M5; availability separately verified')
            trace+=['market_pulse_engine.normalize_rows','market_pulse_engine.build_payload']
        else:pulse_payload={'symbol':symbol,'pulse':{'available':False,'reason':'M5_OR_VOLUME_MISSING','scoring_applied':False,'telegram_eligible':False}}
        p=pulse.OUTPUT_FILE;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(pulse_payload),encoding='utf-8')
        for name,m in modules.items():
            trace.append(name+'.main')
            m.main()
            output=getattr(m,'OUTPUT_FILE',None)
            if output and output.exists():
                result=json.loads(output.read_text('utf-8'))
                if result.get('symbol')!=symbol:
                    corrections.append({'module':name,'field':'symbol','from':result.get('symbol'),'to':symbol})
                    result['symbol']=symbol;output.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        scenario=json.loads(modules['scenario_state'].OUTPUT_FILE.read_text('utf-8'))
        from event_monitor import scenario_snapshot,compare_snapshots
        from alert_policy import policy_decision
        from notification_builder import build_message
        old=(previous or {}).get('scenario')
        events=compare_snapshots(scenario_snapshot(old),scenario_snapshot(scenario)) if old else []
        policy,state=policy_decision((previous or {}).get('policy_state',{}),scenario,{'events':events},now=time_utc(cutoff))
        message=build_message(scenario,policy,preview=True)
        trace+=['market_reasoning_engine.build_reasoning_state (via scenario_state.main)',
                'event_monitor.scenario_snapshot','event_monitor.compare_snapshots','alert_policy.policy_decision','notification_builder.build_message']
    (root/'legacy.log').write_text(log.getvalue(),encoding='utf-8')
    return {'status':'ANALYZED_RETROSPECTIVELY','scenario':scenario,'policy':policy,'policy_state':state,'events':events,
            'message_preview':message,'pulse':pulse_payload,'calls':trace,'header_normalizations':corrections,
            'generated_timestamp_semantics':'virtual evaluation cutoff; NOT historical availability or original generation'}
