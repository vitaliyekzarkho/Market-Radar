"""Explicit non-trading observations, outside the SCENARIO lifecycle."""
from recorded_authority import Rejected, decode, strict_utc
from recorded_context import ROLES

def record_nonpublishable(ledger,key,*,observation_id,cutoff,refs):
    with ledger.transaction():
        ledger.integrity()
        if not strict_utc(cutoff):raise Rejected('CUTOFF_INVALID')
        o=ledger.observations()[observation_id]['body']
        value=decode(ledger.payload(observation_id))
        if (value.get('observation_type')!='NON_PUBLISHABLE' or value.get('publication_state')!='silent'
            or value.get('primary') is not None or value.get('legacy_primary') is not None
            or value.get('direction') is not None or value.get('point_a') is not None
            or value.get('point_b') is not None or value.get('entry_allowed') is not False
            or value.get('permissions')!={'entry':False,'trade':False}):
            raise Rejected('NON_PUBLISHABLE_CONTRACT_REQUIRED')
        if set(refs)!=ROLES:raise Rejected('ALL_ROLE_REFERENCES_REQUIRED')
        dependencies=set(o['derivation']['required_input_ids']+o['derivation']['optional_input_ids'])
        if dependencies!={v for v in refs.values() if v}:raise Rejected('SCENARIO_INPUT_CLOSURE_MISMATCH')
        reasons=['NO_PUBLISHED_PRIMARY','NO_DIRECTIONAL_SCENARIO','ASSESSMENT_NOT_APPLICABLE','HISTORICAL_AVAILABILITY_UNKNOWN']
        observation=dict(kind='NON_PUBLISHABLE_OBSERVATION',key=key,observation_id=observation_id,
            source_payload_sha256=o['payload_sha256'],cutoff=cutoff,refs=refs,technical=value,
            status='NOT_EVALUATED',reasons=reasons,trade_authorized=False,entry_allowed=False)
        observation=ledger.append('nonpublishable:'+key,observation)
        outcome=dict(kind='NON_ASSESSMENT',key=key+':eligibility',observation_key=key,
            status='NOT_ELIGIBLE',assessment_status='NOT_EVALUATED',reasons=reasons,
            trade_authorized=False,delivery_authorized=False,hypothesis_created=False)
        outcome=ledger.append('nonassessment:'+key,outcome)
        return observation,outcome
