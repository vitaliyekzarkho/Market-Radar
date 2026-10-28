"""Recorded scenario history preserving the exact source technical payload."""
from recorded_context import canonical, ROLES
from recorded_authority import decode, Rejected, instant, interval, strict_utc

def hypotheses(s):
    hs=[s['primary'],*s['nested'],*s['alternatives']]
    if not hs or any(not isinstance(h,dict) or not isinstance(h.get('id'),str) or not h['id'] for h in hs):
        raise Rejected('HYPOTHESIS_ID_REQUIRED')
    ids=[h['id'] for h in hs]
    if len(set(ids))!=len(ids):raise Rejected('DUPLICATE_HYPOTHESIS_ID')
    return {h['id']:h for h in hs}

class RecordedScenarios:
    def __init__(self,ledger):self.ledger=ledger
    def observe_snapshot(self,key,*,scenario_id,observation_id,cutoff,refs):
        """Unadmitted legacy snapshot, never a historical formation assertion.

        Does not enter the VERIFIED lifecycle projection used by record().
        Keeps full hierarchy for audit without fabricating one formation per ID.
        """
        with self.ledger.transaction():
            self.ledger.integrity()
            if not strict_utc(cutoff):raise Rejected('CUTOFF_INVALID')
            rows=self.ledger.observations()
            if observation_id not in rows:raise Rejected('SCENARIO_OBSERVATION_MISSING')
            o=rows[observation_id]['body']
            if o['kind']!='scenario':raise Rejected('SCENARIO_KIND_REQUIRED')
            technical=decode(self.ledger.payload(observation_id));hypotheses(technical)
            required={'point_a','point_b','direction','score','invalidation','FTA','TA','permissions','primary','nested','alternatives','htf'}
            if not required<=set(technical):raise Rejected('LOSSLESS_SCENARIO_REQUIRED')
            if technical['direction'] not in {'bullish','bearish'} or type(technical.get('permissions',{}).get('entry')) is not bool:
                raise Rejected('TECHNICAL_PERMISSION_EXPLICIT_REQUIRED')
            if set(refs)!=ROLES:raise Rejected('ALL_ROLE_REFERENCES_REQUIRED')
            deps=set(o['derivation']['required_input_ids']+o['derivation']['optional_input_ids'])
            if deps!={v for v in refs.values() if v is not None}:raise Rejected('SCENARIO_INPUT_CLOSURE_MISMATCH')
            history=self.history(scenario_id)
            for prior in history:
                if prior['key']==key:
                    request=prior['request']
                    if request['observation_id']!=observation_id or request['cutoff']!=cutoff or request['refs']!=refs:
                        raise Rejected('IDEMPOTENCY_CONFLICT')
                    return prior
            if history and instant(cutoff)<instant(history[-1]['request']['cutoff']):raise Rejected('SCENARIO_ORDER_INVALID')
            previous=history[-1] if history else None
            changes=[]
            if previous:
                old=previous['technical'];old_ids=hypotheses(old);new_ids=hypotheses(technical)
                for hid in sorted(set(new_ids)-set(old_ids)):changes.append({'kind':'HYPOTHESIS_FIRST_OBSERVED','id':hid,'formation_at_utc':None})
                for hid in sorted(set(old_ids)-set(new_ids)):changes.append({'kind':'HYPOTHESIS_NOT_PRESENT','id':hid,'refutation_proven':False})
                transition=technical.get('legacy_market_state',{}).get('hypothesis_transition') or {}
                if old['direction']!=technical['direction']:
                    changes.append({'kind':'PRODUCTION_REFUTATION_OBSERVED' if transition.get('switch_confirmed') is True else 'DIRECTION_CHANGE_UNCONFIRMED',
                        'id':old['primary']['id'],'production_transition':transition,'independently_verified':False})
                if old['point_b']!=technical['point_b']:
                    changes.append({'kind':'TARGET_CHANGE_OBSERVED','before':old['point_b'],'after':technical['point_b'],
                        'historical_availability':'UNKNOWN','structural_invalidity_inferred':False})
                if old.get('invalidation')!=technical.get('invalidation'):changes.append({'kind':'INVALIDATION_CONDITION_CHANGED','before':old.get('invalidation'),'after':technical.get('invalidation')})
                if not changes and old!=technical:changes.append({'kind':'CONTEXT_REVISION_OBSERVED'})
            else:changes=[{'kind':'LEGACY_BASELINE_OBSERVED','formation_at_utc':None}]
            result=dict(kind='SCENARIO',key=key,scenario_id=scenario_id,request=dict(observation_id=observation_id,cutoff=cutoff,refs=refs,event_type='LEGACY_OBSERVED_ONLY',target_id=technical['primary']['id']),
                technical=technical,source_payload_sha256=o['payload_sha256'],authority_class=self.ledger.context.mode,status='DEFERRED',
                reasons=['OBSERVED_SNAPSHOT_NOT_HISTORICAL_FORMATION','INPUT_AVAILABILITY_UNKNOWN'],scenario_established_at_utc=o['scenario_established_at_utc'],
                observed_changes=changes,previous_version=previous['source_payload_sha256'] if previous else None,
                significant_change=bool(changes),trading_permission=False)
            return self.ledger.append('scenario:'+key,result)
    def history(self,scenario_id):
        self.ledger.integrity()
        return [e for e in self.ledger.events() if e['kind']=='SCENARIO' and e['scenario_id']==scenario_id]
    def record(self,key,*,scenario_id,observation_id,cutoff,refs,event_type,target_id):
        with self.ledger.transaction():
            return self._record(key,scenario_id=scenario_id,observation_id=observation_id,cutoff=cutoff,refs=refs,event_type=event_type,target_id=target_id)
    def _record(self,key,*,scenario_id,observation_id,cutoff,refs,event_type,target_id):
        if not strict_utc(cutoff):raise Rejected('CUTOFF_INVALID')
        if event_type not in {'HYPOTHESIS_FORMED','HYPOTHESIS_REVISED','HYPOTHESIS_REFUTED'}:raise Rejected('LIFECYCLE_EVENT_INVALID')
        rows=self.ledger.observations()
        if observation_id not in rows:raise Rejected('SCENARIO_OBSERVATION_MISSING')
        o=rows[observation_id]['body'];a=rows[observation_id]['assessment']
        if o['kind']!='scenario':raise Rejected('SCENARIO_KIND_REQUIRED')
        s=decode(self.ledger.payload(observation_id))
        required={'point_a','point_b','direction','score','invalidation','FTA','TA','permissions','primary','nested','alternatives','htf'}
        if not isinstance(s,dict) or not required <= set(s):raise Rejected('LOSSLESS_SCENARIO_REQUIRED')
        if s['direction'] not in {'bearish','bullish'} or not isinstance(s['permissions'],dict) or type(s['permissions'].get('entry')) is not bool:
            raise Rejected('TECHNICAL_PERMISSION_EXPLICIT_REQUIRED')
        hs=hypotheses(s)
        if target_id not in hs:raise Rejected('TARGET_HYPOTHESIS_MISSING')
        if set(refs)!=ROLES:raise Rejected('ALL_ROLE_REFERENCES_REQUIRED')
        deps=set(o['derivation']['required_input_ids']+o['derivation']['optional_input_ids'])
        if {v for v in refs.values() if v is not None}!=deps:raise Rejected('SCENARIO_INPUT_CLOSURE_MISMATCH')
        reasons=[];policy=self.ledger.context.policy();cut=instant(cutoff)
        for point in ('point_a','point_b'):
            value=s[point]
            if not isinstance(value,dict) or not value.get('time'):
                reasons.append(point+':ESTABLISHMENT_UNKNOWN')
            else:
                try:
                    if not strict_utc(value['time']):raise ValueError('UTC required')
                    if instant(value['time'])>cut:reasons.append(point+':FUTURE')
                    if (a['authority']['scenario_established_at_utc']=='VERIFIED'
                        and instant(value['time'])>interval(o['scenario_established_at_utc'])[0]):
                        reasons.append(point+':AFTER_SCENARIO_ESTABLISHMENT')
                except (ValueError,TypeError):reasons.append(point+':TIME_INVALID')
        for role,oid in refs.items():
            rule=policy['inputs'][role]
            if oid is None:
                if rule['required']:reasons.append(role+':MISSING')
                continue
            if oid not in rows:raise Rejected('REFERENCE_MISSING')
            dep=rows[oid]['body']
            if dep['role']!=role or dep['source_id'] not in rule['source_ids']:raise Rejected('REFERENCE_ROLE_OR_SOURCE_CONFLICT')
            if self.ledger.dependency_status(oid,cutoff)!='VERIFIED':reasons.append(role+':UNKNOWN');continue
            if rows[oid]['assessment']['authority'].get(rule['age_anchor'])!='VERIFIED':reasons.append(role+':AGE_UNKNOWN');continue
            lo,hi=interval(dep[rule['age_anchor']])
            if hi>cut: reasons.append(role+':FUTURE')
            elif (cut-lo).total_seconds()>rule['max_age_seconds']:reasons.append(role+':STALE')
            # Conflict flag must be in the immutable source bytes.
            try: body=decode(self.ledger.payload(oid))
            except Rejected: body={}
            if isinstance(body,dict) and body.get('status')=='conflicts':reasons.append(role+':CONFLICT')
        if self.ledger.dependency_status(observation_id,cutoff)!='VERIFIED':reasons.append('SCENARIO_LINEAGE_UNKNOWN')
        if a['authority']['scenario_established_at_utc']!='VERIFIED':reasons.append('ESTABLISHMENT_UNKNOWN')
        elif interval(o['scenario_established_at_utc'])[1]>cut:reasons.append('ESTABLISHMENT_FUTURE')
        previous=self.history(scenario_id)
        # Retry compares exact prior request, without recomputing historical result.
        request=dict(observation_id=observation_id,cutoff=cutoff,refs=refs,event_type=event_type,target_id=target_id)
        for prev in previous:
            if prev['key']==key:
                if prev['request']!=request:raise Rejected('IDEMPOTENCY_CONFLICT')
                return prev
        if previous and cut<instant(previous[-1]['request']['cutoff']):raise Rejected('SCENARIO_ORDER_INVALID')
        effective=[p for p in previous if p['status']=='VERIFIED']
        old=hypotheses(effective[-1]['technical']) if effective else {}
        if set(old)-set(hs):raise Rejected('HYPOTHESIS_REMOVAL_FORBIDDEN')
        added=set(hs)-set(old)
        if event_type=='HYPOTHESIS_FORMED':
            if added!={target_id}:raise Rejected('FORMATION_EXACTLY_ONE_NEW_ID')
        elif added or target_id not in old:raise Rejected('NEW_HYPOTHESIS_REQUIRES_FORMATION')
        for hid in set(old)&set(hs):
            for field in ('direction','parent_id'):
                if old[hid].get(field)!=hs[hid].get(field):raise Rejected('HYPOTHESIS_IDENTITY_CHANGED')
            if old[hid].get('state')=='refuted' and hs[hid].get('state')!='refuted':raise Rejected('REFUTED_ID_REUSED')
            changed=old[hid].get('state')!=hs[hid].get('state')
            if changed and hs[hid].get('state')=='refuted' and (event_type!='HYPOTHESIS_REFUTED' or hid!=target_id):
                raise Rejected('REFUTATION_EVENT_REQUIRED')
        if event_type=='HYPOTHESIS_REFUTED' and hs[target_id].get('state')!='refuted':raise Rejected('REFUTED_STATE_REQUIRED')
        result=dict(kind='SCENARIO',key=key,scenario_id=scenario_id,request=request,technical=s,
                    source_payload_sha256=o['payload_sha256'],authority_class=self.ledger.context.mode,
                    status='DEFERRED' if reasons else 'VERIFIED',reasons=sorted(set(reasons)),
                    scenario_established_at_utc=o['scenario_established_at_utc'])
        with self.ledger.transaction():return self.ledger.append('scenario:'+key,result)
