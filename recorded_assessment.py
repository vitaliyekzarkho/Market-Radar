"""Explicit recorded equivalents of D4 boundary, D5 replay and D3 eligibility.

Synthetic entrypoints remain unchanged. No transport functions exist here.
"""
from recorded_authority import Rejected, sha
from recorded_context import canonical
from recorded_scenario import RecordedScenarios

def assess_recorded(ledger,scenario_key):
    ledger.integrity();ledger.context.policy()
    rows=[e for e in ledger.events() if e['kind']=='SCENARIO' and e['key']==scenario_key]
    if len(rows)!=1:raise Rejected('EXACT_SCENARIO_REFERENCE_REQUIRED')
    r=rows[0]; reasons=list(r['reasons'])
    # New corrections never rewrite the old report; reassessment is conservative.
    for oid in [r['request']['observation_id'],*[x for x in r['request']['refs'].values() if x]]:
        if ledger.dependency_status(oid,r['request']['cutoff'])!='VERIFIED':reasons.append('CURRENT_ASOF_LINEAGE_UNKNOWN')
    return {'domain':'recorded_offline','authority_class':ledger.context.mode,'scenario_key':scenario_key,
            'scenario_sha256':r['source_payload_sha256'],'cutoff':r['request']['cutoff'],
            'status':'DEFERRED' if reasons else 'VERIFIED','reasons':sorted(set(reasons)),
            'historical_claim':ledger.context.mode=='CAPTURED_AUTHORITY' and not reasons,
            'technical':r['technical']}

class RecordedNotificationAudit:
    def __init__(self,ledger):self.ledger=ledger
    def assess(self,key,*,scenario_key):
        with self.ledger.transaction():return self._assess(key,scenario_key=scenario_key)
    def _assess(self,key,*,scenario_key):
        # Immutable retry uses exact scenario reference, never current clock.
        old=[e for e in self.ledger.events() if e['kind']=='ELIGIBILITY' and e['key']==key]
        if old:
            self.ledger.integrity()
            if old[0]['scenario_key']!=scenario_key:raise Rejected('IDEMPOTENCY_CONFLICT')
            return old[0]
        r=assess_recorded(self.ledger,scenario_key);p=self.ledger.context.policy(); reasons=list(r['reasons'])
        if r['technical']['primary'].get('state')!='active':reasons.append('HYPOTHESIS_INACTIVE')
        if p['require_entry_permission'] and r['technical']['permissions']['entry'] is not True:
            reasons.append('TECHNICAL_PERMISSION_REQUIRED')
        e=dict(kind='ELIGIBILITY',key=key,scenario_key=scenario_key,assessment=r,
               status='DEFERRED' if reasons else 'ELIGIBLE',reasons=sorted(set(reasons)),
               authority_class=self.ledger.context.mode,trade_authorized=False,delivery_authorized=False)
        with self.ledger.transaction():return self.ledger.append('eligibility:'+key,e)
    def generate(self,key,*,eligibility_key):
        with self.ledger.transaction():return self._generate(key,eligibility_key=eligibility_key)
    def _generate(self,key,*,eligibility_key):
        self.ledger.integrity()
        found=[e for e in self.ledger.events() if e['kind']=='ELIGIBILITY' and e['key']==eligibility_key]
        if len(found)!=1 or found[0]['status']!='ELIGIBLE':raise Rejected('GENERATION_REQUIRES_ELIGIBLE')
        eligibility=found[0]
        current=assess_recorded(self.ledger,eligibility['scenario_key'])
        if current['status']!='VERIFIED':raise Rejected('GENERATION_LINEAGE_NO_LONGER_VERIFIED')
        # Semantic key is scoped to exact scenario bytes and cutoff, not delivery.
        semantic=sha(canonical([current['scenario_sha256'],current['cutoff'],self.ledger.context.mode]).encode())
        existing=[e for e in self.ledger.events() if e['kind']=='NOTIFICATION_PREVIEW' and e['semantic_id']==semantic]
        if existing:return existing[0]
        msg='OFFLINE PREVIEW ['+self.ledger.context.mode+'] '+canonical(current['technical'])
        e=dict(kind='NOTIFICATION_PREVIEW',key=key,eligibility_key=eligibility_key,semantic_id=semantic,
               authority_class=self.ledger.context.mode,message=msg,delivery_authorized=False,
               historical_generation_at_utc=None,reason='Replay preview is not evidence of original generation')
        with self.ledger.transaction():return self.ledger.append('preview:'+key,e)

def replay_recorded(ledger,commands):
    """Order is caller's historical decision order; retrograde commands reject.

    All evidence must already be staged/verified; replay does not collect data.
    """
    scenarios=RecordedScenarios(ledger);audit=RecordedNotificationAudit(ledger);out=[]
    for command in commands:
        r=scenarios.record(**command)
        e=audit.assess('eligibility:'+command['key'],scenario_key=command['key'])
        preview=audit.generate('preview:'+command['key'],eligibility_key=e['key']) if e['status']=='ELIGIBLE' else None
        out.append({'scenario':r,'eligibility':e,'preview':preview})
    return {'classification':'recorded_offline','authority_class':ledger.context.mode,'results':out,'live_actions':0}
