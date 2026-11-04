"""Offline price-structure bridge. Explicitly stops before legacy UTC lifecycle.

Input must be trusted local capture evidence. A content hash alone is not a
third-party authority. No network, MT5 or notification transport is imported.
"""
from pathlib import Path
from copy import deepcopy
from mt5_clock_authority import ClockLedger, digest, epoch
from history_receipts import select_history
from source_time import admitted_frame
from market_features import analyse_timeframe
from order_flow_structure import analyse_frame, nested_parent_check, PARENT_CHILD, timeframe_sync
from dealing_range_engine import analyse_timeframe as analyse_range
from level_state import extract_imbalances, extract_key_levels, build_state
from scenario_state import build_scenario
from session_context import unverified_source_context
from key_levels import unverified_calendar_context


def prepare_runtime(root, cutoff):
    """Publish admitted core inputs to the existing Radar file interfaces.

    Fail closed before legacy collectors bypass the common receipt boundary.
    This is called by run_radar, not a separate live service.
    """
    import json
    from time_ingestion import verified_utc_view
    from cdr_operational import write
    root = Path(root)
    time_view = verified_utc_view(root, cutoff)
    ledger = ClockLedger(root/'data/time_authority/observations.sqlite')
    try:
        records = ledger.records()
    finally:
        ledger.close()
    # Do not overwrite the previous scenario before the context-aware construction.
    result = evaluate(records, cutoff)
    blocked = []
    if result['scenario_status'] == 'EVALUATED':
        state = dict(result['level_state'], time_basis='SOURCE_ORDER_ONLY')
        outputs = {
            'features': {'timeframes':result['features']},
            'ranges': {'timeframes':result['ranges']},
            'order_flow': result['order_flow'], 'states':state,
            'sessions':result['sessions'], 'levels':result['key_levels'],
            # scenario_state.main consumes these interfaces after context producers.
        }
        for folder, payload in outputs.items():
            write(root/'data'/folder/'GOLD_pro.json', payload)
    else:
        blocked.append('INSUFFICIENT_ADMITTED_CORE_DATA')
    summary = dict(status='READY' if not blocked else 'BLOCKED', capture_id=time_view['capture_id'],
                   analysis_status=result['status'], scenario_status=result['scenario_status'],
                   time_basis='SOURCE_ORDER_ONLY', verified_utc_bars=sum(x['utc_feature_admitted'] for x in time_view['rows']),
                   historical_timezone_rule='UNKNOWN', blockers=blocked,
                   analysis_sha256=digest(result), transport_authorized=False, decision_at_utc=cutoff,
                   limitations=['AUXILIARY_TIME_EVIDENCE_DEFERRED','HISTORICAL_UTC_UNKNOWN'])
    # Content-addressed single analysis; stages refer to its hash, not repeated history.
    write(root/'data/admission'/('analysis_'+summary['analysis_sha256']+'.json'), result)
    write(root/'data/admission/latest.json', summary)
    return summary


def evaluate(records, cutoff, mode='current_analysis', previous_scenario=None):
    if previous_scenario and previous_scenario.get('time_basis') != 'SOURCE_ORDER_ONLY':
        raise ValueError('LEGACY_STATE_IMPORT_FORBIDDEN')
    view = select_history(records, cutoff, mode)
    frames, features, structures = {}, {}, {}
    for tf in ('W1', 'D1', 'H4', 'H1', 'M15'):
        frame = admitted_frame(view['rows'], tf)
        frames[tf] = frame
        if len(frame) < 7:
            features[tf] = structures[tf] = {'status': 'DEFERRED', 'reason': 'INSUFFICIENT_ADMITTED_BARS', 'bars_used': len(frame)}
            continue
        features[tf] = analyse_timeframe(tf, admitted_data=frame)
        structures[tf] = analyse_frame(frame, tf)
    nested = {}
    for parent, child, count in PARENT_CHILD:
        nested[parent + '/' + child] = {'status': 'UNKNOWN', 'reason': 'UTC_INTERVAL_MAPPING_UNVERIFIED', 'aligned': False, 'complete': False}
    result = dict(status='PARTIAL_PRICE_STRUCTURE_ONLY', time_basis='SOURCE_ORDER_ONLY',
                admission=view, features=features, order_flow={'timeframes': structures, 'nested_pa': nested},
                sessions=unverified_source_context(), key_levels=unverified_calendar_context(),
                scenario_status='DEFERRED', assessment={'status': 'DEFERRED', 'reasons': ['LEGACY_LEVEL_STATE_UTC_LIFECYCLE_NOT_ADAPTED']},
                eligibility={'status': 'DEFERRED', 'delivery_authorized': False},
                existing_scenario='PRESERVED_NOT_REEVALUATED', trade_authorized=False)
    if all(len(frame) >= 7 for frame in frames.values()):
        ranges = {tf: analyse_range(tf, admitted_data=frames[tf]) for tf in ('W1','D1','H4','H1')}
        flow = result['order_flow']
        flow['timeframe_sync'] = timeframe_sync(structures, nested)
        # Existing PD/PW calculations use closed broker D1/W1 bars, not UTC dates.
        from key_levels import previous_day_levels, previous_week_levels
        from source_time import SourceTime
        levels = {}
        price = float(frames['M15'].iloc[-1]['close'])
        for tf,calc in [('D1',previous_day_levels),('W1',previous_week_levels)]:
            frame=frames[tf].copy();frame['broker_time']=frame['source_time']
            newest=int(frame.iloc[-1]['time'])
            successors=[r['raw_timestamp'] for r in view['rows'] if r['timeframe']==tf and r['raw_timestamp']>newest]
            if not successors:continue
            active=SourceTime('OANDA_MT5_LOCAL','GOLD.pro',tf,min(successors)).reference()
            for name,level in calc(frame,price).items():
                level.update(active_from=active,time_basis='SOURCE_ORDER_ONLY',utc_time=None)
                levels[name]=level
        result['key_levels']={'status':'SOURCE_PERIOD_LEVELS','levels':levels,'calendar_utc_status':'UNKNOWN',
                              'missing':['PMH','PML','MO','DO','WO']}
        objects = extract_key_levels(result['key_levels']) + extract_imbalances({'timeframes': features})
        if objects:
            price = float(frames['M15'].iloc[-1]['close'])
            state = build_state(objects, frames, price, {}, cutoff)
            scenario = build_scenario(state, {'timeframes': ranges}, {}, result['sessions'], flow,
                                      {}, {}, {}, {}, {}, deepcopy(previous_scenario or {}), cutoff)
            scenario['time_basis'] = 'SOURCE_ORDER_ONLY'
            state['generated_at_utc'] = None
            scenario['generated_at_utc'] = None
            state['as_of_cutoff_utc'] = cutoff
            scenario['as_of_cutoff_utc'] = cutoff
            scenario['evaluation_time_semantics'] = 'AS_OF_CUTOFF_NOT_ORIGINAL_HYPOTHESIS_FORMATION'
            result.update(status='CURRENT_PA_HTF_EVALUATED', scenario_status='EVALUATED',
                          scenario=scenario, level_state=state, ranges=ranges,
                          existing_scenario='OFFLINE_REEVALUATED_NO_PRODUCTION_WRITE',
                          assessment={'status':'DEFERRED', 'reasons':['UTC_CAUSAL_HISTORY_UNVERIFIED','NO_NEWS_OR_INTERMARKET_RECEIPTS']})
    return result


def previous_scenario_at(root, cutoff, mode):
    """Read only this isolated audit chain; never import production state."""
    from recorded_ledger import RecordedLedger
    from r5_bridge import retrospective_context
    path = Path(root)/'data/cdr/recorded.db'
    if not path.exists():
        return None
    ledger = RecordedLedger(path, context=retrospective_context(), dataset_id='captured:operational-audit-v1')
    try:
        for event in reversed(ledger.events()):
            if event.get('stage') != 'scenario' or event.get('receipt_lineage', {}).get('mode') != mode:
                continue
            if epoch(event['at_utc']) >= epoch(cutoff):
                continue  # repeat of same cutoff is deterministic, not a revision
            scenario = event.get('technical_analysis', {}).get('scenario')
            if scenario:
                return deepcopy(scenario)
        return None
    finally:
        ledger.close()


def journal_result(root, cycle_id, result):
    """Use accepted C/D/R ledger + DecisionJournal, never overwrite a scenario."""
    from recorded_ledger import RecordedLedger
    from r5_bridge import retrospective_context
    from cdr_operational import publish_journal
    root = Path(root).resolve()
    if str(root).casefold().startswith('c:\\users\\user\\marketradar') or not (root/'OFFLINE_ADMISSION_SANDBOX').is_file():
        raise ValueError('EXPLICIT_OFFLINE_SANDBOX_REQUIRED')
    if not cycle_id or len(cycle_id) > 120:
        raise ValueError('CYCLE_ID_REQUIRED')
    out = root/'data/cdr'
    out.mkdir(parents=True, exist_ok=True)
    ledger = RecordedLedger(out/'recorded.db', context=retrospective_context(), dataset_id='captured:operational-audit-v1')
    fingerprint = digest(result)
    try:
        with ledger.transaction():
            for stage in ('capture', 'scenario', 'policy', 'notification'):
                key = cycle_id + ':' + stage
                old = [e for e in ledger.events() if e.get('audit_key') == key]
                if old:
                    if old[0].get('analysis_sha256') != fingerprint:
                        raise ValueError('CYCLE_CONTENT_CONFLICT')
                    continue
                event = dict(kind='OPERATIONAL_STAGE', audit_key=key, cycle_id=cycle_id, stage=stage,
                    at_utc=result['admission']['decision_at_utc'], at_utc_authority='REQUESTED_AS_OF_CUTOFF_NOT_EXECUTION_TIME',
                    status='DEFERRED', analysis_sha256=fingerprint,
                    receipt_lineage=deepcopy(result['admission']),
                    technical_analysis={'features': result['features'], 'order_flow': result['order_flow'], 'scenario':result.get('scenario')},
                    assessment=result['assessment'], eligibility=result['eligibility'],
                    hypothesis_created=False, should_notify=False, trade_authorized=False,
                    existing_radar_state=result['existing_scenario'], outcome='NOT_GENERATED_UTC_CAUSAL_ASSESSMENT_DEFERRED')
                ledger.append(key + ':stage', event)
        publish_journal(out, ledger)
        return {'stages': 4, 'analysis_sha256': fingerprint, 'status': 'DEFERRED'}
    finally:
        ledger.close()
