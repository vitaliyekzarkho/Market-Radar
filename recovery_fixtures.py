"""Fabricated TEST_AUTHORITY only. Never genuine historical market evidence."""
from copy import deepcopy
from test_recorded_authority import fixture, raw
from recorded_authority import TrustStore, sha
from recorded_context import AuthorityContext, ROLES

def case():
    env,artifacts,t=fixture()
    import json
    p=json.loads(t.policy_bytes)
    p.update(authority_class='TEST_AUTHORITY',conflict_behavior='WAIT',missing_behavior='DEFERRED',
        require_entry_permission=False,inputs={r:dict(required=r=='technical',max_age_seconds=3600,
        age_anchor='available_at_utc',source_ids=['test:source']) for r in sorted(ROLES)})
    t=TrustStore(raw(p),t.authority_ids,t.source_ids,t.audited_receipt_hashes,t.admission_reference)
    env['policy_sha256']=sha(t.policy_bytes)
    return env,artifacts,AuthorityContext('TEST_AUTHORITY',t)

def add_observation(env,artifacts,ctx,oid,at,*,predecessor=None,kind='raw',role='technical',payload=None,dependencies=None):
    """Create contract fixture receipts, explicitly under test authority."""
    old=deepcopy(env['observations'][0]); old.update(observation_id=oid,logical_item_id='test:item',
        capture_id=oid+':capture',source_sequence=len(env['observations'])+1,supersedes=predecessor,kind=kind,role=role)
    b=raw(payload if payload is not None else {'fixture':'TEST ONLY','id':oid})
    old.update(file_ref=oid+':file',file_sha256=sha(b),payload_sha256=sha(b),payload_locator=f'bytes:0:{len(b)}')
    artifacts[old['file_ref']]=b; pins=set(ctx.trust.audited_receipt_hashes)
    for field,rkind in [('market_event_at_utc','source_event'),('captured_at_utc','capture'),
                        ('available_at_utc','availability'),('scenario_established_at_utc','scenario_establishment')]:
        if field=='scenario_established_at_utc' and kind!='scenario': continue
        rid=oid+':'+rkind
        rec=dict(receipt_id=rid,kind=rkind,subject_id=oid,subject_payload_sha256=sha(b),source_id='test:source',
                 decision_system_id=env['decision_system_id'],authority_id='test:authority',at_utc=at,uncertainty_seconds=0)
        proof=raw(rec); artifacts[rid]=proof; pins.add(sha(proof))
        env['receipts'].append(dict(rec,artifact_ref=rid,artifact_sha256=sha(proof)))
        old[field]=dict(state='KNOWN',at_utc=at,uncertainty_seconds=0,authority_id='test:authority',evidence_ref=rid)
    if kind!='raw':
        rid=oid+':completion'
        old['derivation']=dict(required_input_ids=dependencies or [],optional_input_ids=[],engine_sha256='1'*64,
                              config_sha256='2'*64,completion_receipt_ref=rid)
        rec=dict(receipt_id=rid,kind='derivation_completion',subject_id=oid,subject_payload_sha256=sha(b),source_id='test:source',
                 decision_system_id=env['decision_system_id'],authority_id='test:authority',at_utc=at,uncertainty_seconds=0)
        proof=raw(dict(rec,derivation_sha256=sha(raw(old['derivation'])))); artifacts[rid]=proof; pins.add(sha(proof))
        env['receipts'].append(dict(rec,artifact_ref=rid,artifact_sha256=sha(proof)))
        old['logical_item_id']=oid+':chain'
    env['observations'].append(old)
    t=ctx.trust
    return AuthorityContext(ctx.mode,TrustStore(t.policy_bytes,t.authority_ids,t.source_ids,frozenset(pins),t.admission_reference))
