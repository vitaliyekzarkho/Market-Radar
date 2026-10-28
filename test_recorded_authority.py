"""Fabricated receipt fixtures exercise verifier mechanics, not historical truth."""
from copy import deepcopy
import json
import unittest
from recorded_authority import verify, Rejected, TrustStore, sha

def raw(x): return json.dumps(x,sort_keys=True,separators=(',',':')).encode()
def fixture():
    policy={'classification':'recorded_offline','contract_version':'recorded_offline/1.0.0-draft.1',
            'policy_id':'test:policy','decision_system_id':'test:consumer','transport_enabled':False,
            'trading_enabled':False,'max_clock_uncertainty_seconds':1}
    unknown={'state':'UNKNOWN','at_utc':None,'uncertainty_seconds':None,'authority_id':None,'evidence_ref':None,'reason':'Not a scenario'}
    payload=b'EXPLICIT TEST DATA - NOT MARKET CAPTURE'
    obs={'observation_id':'test:o1','logical_item_id':'test:item','source_id':'test:source','capture_id':'test:capture',
         'capture_stream_id':'test:stream','source_sequence':1,'kind':'raw','role':'technical','file_ref':'test:file',
         'file_sha256':sha(payload),'payload_sha256':sha(payload),'payload_locator':f'bytes:0:{len(payload)}',
         'scenario_established_at_utc':unknown,'supersedes':None,'derivation':None}
    artifacts={'test:file':payload}; receipts=[]; pins=set()
    for field,kind,sec,rid in [('market_event_at_utc','source_event',0,'test:event'),('captured_at_utc','capture',2,'test:capture'),('available_at_utc','availability',4,'test:ready')]:
        at=f'2026-10-20T10:00:0{sec}Z'
        r={'receipt_id':rid,'kind':kind,'subject_id':'test:o1','subject_payload_sha256':sha(payload),'source_id':'test:source',
           'decision_system_id':'test:consumer','authority_id':'test:authority','at_utc':at,'uncertainty_seconds':0}
        proof=raw(r); h=sha(proof); pins.add(h); artifacts[rid]=proof
        receipts.append(dict(r,artifact_ref=rid,artifact_sha256=h))
        obs[field]={'state':'KNOWN','at_utc':at,'uncertainty_seconds':0,'authority_id':'test:authority','evidence_ref':rid}
    env={'contract_version':policy['contract_version'],'classification':'recorded_offline','dataset_id':'test:dataset',
         'decision_system_id':'test:consumer','policy_id':'test:policy','policy_sha256':sha(raw(policy)),
         'historical_cutoff_utc':'2026-10-20T10:00:04Z','replay_executed_at_utc':None,'observations':[obs],'receipts':receipts}
    trust=TrustStore(raw(policy),frozenset({'test:authority'}),frozenset({'test:source'}),frozenset(pins),'TEST ONLY independent audit simulation')
    return env,artifacts,trust

class AuthorityTests(unittest.TestCase):
    def setUp(self): self.e,self.a,self.t=fixture()
    def go(self): return verify(raw(self.e),artifacts=self.a,trust=self.t)
    def rejected(self):
        with self.assertRaises(Rejected): self.go()
    def retime(self, field, at, uncertainty=0):
        claim=self.e['observations'][0][field]
        claim.update(at_utc=at,uncertainty_seconds=uncertainty)
        r=next(r for r in self.e['receipts'] if r['receipt_id']==claim['evidence_ref'])
        r.update(at_utc=at,uncertainty_seconds=uncertainty)
        body={k:v for k,v in r.items() if k not in {'artifact_ref','artifact_sha256'}}
        proof=raw(body); r['artifact_sha256']=sha(proof); self.a[r['artifact_ref']]=proof
        self.t=TrustStore(self.t.policy_bytes,self.t.authority_ids,self.t.source_ids,
                          self.t.audited_receipt_hashes | {sha(proof)},self.t.admission_reference)
    def test_verified_receipts_not_eligibility(self):
        r=self.go(); self.assertEqual(r['observations'][0]['status'],'VERIFIED'); self.assertFalse(r['generation_allowed']); self.assertFalse(r['delivery_allowed'])
    def test_future_availability(self):
        self.e['historical_cutoff_utc']='2026-10-20T10:00:03Z'; self.assertIn('AFTER_CUTOFF',self.go()['observations'][0]['reasons'])
    def test_synthetic_rejected(self): self.e['classification']='synthetic'; self.rejected()
    def test_extra_key(self): self.e['mtime']='yesterday'; self.rejected()
    def test_bad_date(self): self.e['historical_cutoff_utc']='2026-02-30T00:00:00Z'; self.rejected()
    def test_naive_time(self): self.e['historical_cutoff_utc']='2026-10-20T10:00:04'; self.rejected()
    def test_unknown_not_zero(self):
        self.e['observations'][0]['available_at_utc']=deepcopy(self.e['observations'][0]['scenario_established_at_utc'])
        self.assertIn('available_at_utc:UNKNOWN',self.go()['observations'][0]['reasons'])
    def test_missing_proof(self):
        del self.a['test:ready']; self.assertEqual(self.go()['observations'][0]['authority']['available_at_utc'],'UNVERIFIED')
    def test_self_asserted_receipt_untrusted(self):
        self.t=TrustStore(self.t.policy_bytes,self.t.authority_ids,self.t.source_ids,frozenset(),'')
        self.assertEqual(self.go()['observations'][0]['status'],'UNVERIFIED')
    def test_source_untrusted(self):
        self.t=TrustStore(self.t.policy_bytes,self.t.authority_ids,frozenset(),self.t.audited_receipt_hashes,self.t.admission_reference)
        self.assertIn('SOURCE_UNVERIFIED',self.go()['observations'][0]['reasons'])
    def test_hash_conflict(self): self.a['test:file']=b'changed'; self.rejected()
    def test_payload_hash(self): self.e['observations'][0]['payload_sha256']='0'*64; self.rejected()
    def test_receipt_binding(self): self.e['receipts'][0]['source_id']='other'; self.rejected()
    def test_receipt_tamper(self): self.a['test:ready']=b'{}'; self.rejected()
    def test_capture_id(self): self.e['observations'][0]['capture_id']='wrong'; self.rejected()
    def test_duplicate_observation(self): self.e['observations']*=2; self.rejected()
    def test_duplicate_receipt(self): self.e['receipts'].append(self.e['receipts'][0]); self.rejected()
    def test_locator(self): self.e['observations'][0]['payload_locator']='../../private'; self.rejected()
    def test_locator_bounds(self): self.e['observations'][0]['payload_locator']='bytes:0:9999'; self.rejected()
    def test_policy_digest(self): self.e['policy_sha256']='0'*64; self.rejected()
    def test_input_not_mutated(self):
        before=raw(self.e); self.go(); self.assertEqual(before,raw(self.e))
    def test_duplicate_json(self):
        with self.assertRaises(Rejected): verify(b'{"x":1,"x":2}',artifacts={},trust=self.t)
    def test_nonfinite(self):
        with self.assertRaises(Rejected): verify(b'{"x":1e999}',artifacts={},trust=self.t)
    def test_correction_not_certified(self):
        self.e['observations'][0]['supersedes']='test:old'; self.assertIn('CORRECTION_SELECTION_REQUIRES_R2',self.go()['observations'][0]['reasons'])
    def test_uncertainty_exceeds_policy(self):
        self.retime('available_at_utc','2026-10-20T10:00:04Z',2)
        self.assertEqual(self.go()['observations'][0]['authority']['available_at_utc'],'UNVERIFIED')
    def test_uncertainty_cutoff(self):
        self.retime('available_at_utc','2026-10-20T10:00:04Z',1)
        self.assertIn('AFTER_CUTOFF',self.go()['observations'][0]['reasons'])
    def test_clock_reversal(self):
        self.retime('available_at_utc','2026-10-20T10:00:01Z'); self.rejected()
    def test_overlap(self):
        self.retime('captured_at_utc','2026-10-20T10:00:03Z',1)
        self.retime('available_at_utc','2026-10-20T10:00:04Z',1)
        self.assertIn('CLOCK_ORDER_UNPROVEN',self.go()['observations'][0]['reasons'])
    def test_negative_uncertainty(self):
        self.e['observations'][0]['available_at_utc']['uncertainty_seconds']=-1; self.rejected()
    def test_replay_time_not_availability(self):
        self.e['replay_executed_at_utc']='2026-10-23T00:00:00Z'
        self.e['observations'][0]['available_at_utc']=deepcopy(self.e['observations'][0]['scenario_established_at_utc'])
        self.assertIn('available_at_utc:UNKNOWN',self.go()['observations'][0]['reasons'])

if __name__=='__main__': unittest.main()
