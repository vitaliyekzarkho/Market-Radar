"""Separate present availability, source-series closure and UTC admission.

No scoring, trading, native access, UTC history shift or production promotion.
Source-relative closure can support price/order-only consumers; existing legacy
UTC/session consumers still require independently proven UTC labels.
"""
from copy import deepcopy
from mt5_clock_authority import candidate,digest,epoch,utc,PERIODS

def assess(capture,*,decision_at_utc,mode='current_analysis'):
    if mode not in {'current_analysis','historical_replay','shadow'}:raise ValueError('MODE_REQUIRED')
    authority=candidate(capture)
    capture_hash=digest(capture)
    cutoff=epoch(decision_at_utc)
    observed=epoch(capture['shutdown_end_utc'])+authority['hi']
    available=cutoff>=observed
    fresh=cutoff<=epoch(authority['expires_at_utc'])
    groups={tf:[] for tf in PERIODS}
    ticks=[m['value']['time_msc']/1000 for s in capture['samples'] for m in s['measurements'] if m['kind']=='tick' and m.get('value')]
    for sample in capture['samples']:
        for m in sample['measurements']:
            if m['kind']=='bars':groups[m['timeframe']].append(m)
    rows=[]
    for tf,batches in groups.items():
        if len(batches)<2:continue
        last=batches[-1]['rows'];prior=batches[-2]['rows']
        stamps=[b['time'] for b in last]
        if not stamps or stamps!=sorted(set(stamps)):raise ValueError('SOURCE_ORDER_INVALID')
        prior_by_time={b['time']:b for b in prior}
        # Check current tip in the *source clock*, without mapping past rows.
        # Period is a conservative upper bound; gaps/DST ambiguity defer.
        tip=stamps[-1]
        tip_consistent=tip<=ticks[-1]<tip+PERIODS[tf]
        for i,bar in enumerate(last):
            stable=prior_by_time.get(bar['time'])==bar
            successor=i+1<len(last)
            closed=bool(stable and successor and tip_consistent)
            reasons=[]
            if not closed:reasons.append('CURRENT_FUTURE_OR_UNPROVEN_SOURCE_CLOSURE')
            if not available:reasons.append('CAPTURE_NOT_AVAILABLE_AT_CUTOFF')
            if mode=='current_analysis' and not fresh:reasons.append('CURRENT_CAPTURE_EXPIRED')
            # A capture proves availability at its receipt, never before it.
            allowed=closed and available and (fresh or mode!='current_analysis')
            rows.append(dict(timeframe=tf,raw_timestamp=bar['time'],raw_ohlcv=deepcopy(bar),content_sha256=digest(bar),capture_sha256=capture_hash,
                BAR_CLOSED=closed,closure_authority='STABLE_SOURCE_SUCCESSOR_WITH_FRESH_TIP' if closed else 'UNKNOWN',closure_time_utc=None,
                DATA_AVAILABLE_NOW=available and fresh,first_observed_at_utc=utc(observed),
                HISTORICALLY_AVAILABLE_AT=utc(observed),historical_availability_semantics='Known at this capture only; earliest prior availability UNKNOWN',
                historical_availability_before_capture='UNKNOWN',verified_open_utc=None,
                price_order_only_admitted=allowed,legacy_utc_pipeline_admitted=False,mode=mode,reasons=reasons,
                method_limit='Source-series closure proof, not a verified UTC history/session conversion'))
    return dict(mode=mode,decision_at_utc=decision_at_utc,capture_sha256=capture_hash,authority=authority,rows=rows,
        status='DEFERRED_LEGACY_UTC_MAPPING',legacy_pa_htf_restored=False,
        reason='Legacy UTC/session consumers require verified historical open timestamps; no historical availability requirement is imposed on present-only analysis',trade_authorized=False)

def export_legacy_csv(*args,**kwargs):
    raise ValueError('VERIFIED_HISTORICAL_UTC_MAPPING_REQUIRED_NO_FAKE_UTC_EXPORT')
