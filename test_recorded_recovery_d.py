import tempfile,unittest
from pathlib import Path
from copy import deepcopy
from dataclasses import replace
from test_recorded_recovery_c import scenario_case,scenario
from recovery_fixtures import add_observation,raw
from recorded_authority import sha,Rejected
from recorded_context import AuthorityContext,ROLES
from recorded_ledger import RecordedLedger
from recorded_assessment import replay_recorded,RecordedNotificationAudit

def command(key='one',oid='test:s1',cutoff='2026-10-20T10:00:30Z',event='HYPOTHESIS_FORMED'):
    return dict(key=key,scenario_id='route',observation_id=oid,cutoff=cutoff,
        refs={r:('test:o1' if r=='technical' else None) for r in ROLES},event_type=event,target_id='h1')

class RecoveryD(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
    def ledger(self,e,a,c):
        l=RecordedLedger(Path(self.tmp.name)/'l.db',context=c,dataset_id=e['dataset_id']);self.addCleanup(l.close);l.ingest('all',raw(e),a);return l
    def test_test_only_eligibility_no_trade_permission(self):
        e,a,c=scenario_case();l=self.ledger(e,a,c);r=replay_recorded(l,[command()])['results'][0]
        self.assertEqual(r['eligibility']['status'],'ELIGIBLE');self.assertFalse(r['eligibility']['trade_authorized'])
        self.assertFalse(r['eligibility']['assessment']['historical_claim']);self.assertIn('TEST_AUTHORITY',r['preview']['message'])
        self.assertFalse(r['scenario']['technical']['permissions']['entry'])
    def test_unknown_no_generation(self):
        e,a,c=scenario_case();a.pop('test:ready');l=self.ledger(e,a,c);r=replay_recorded(l,[command()])['results'][0]
        self.assertEqual(r['eligibility']['status'],'DEFERRED');self.assertIsNone(r['preview'])
        with self.assertRaises(Rejected):RecordedNotificationAudit(l).generate('bad',eligibility_key='eligibility:one')
    def test_deterministic_restart_multi_observation(self):
        e,a,c=scenario_case();s=scenario();s['point_b']['price']=4019.92
        c=add_observation(e,a,c,'test:s2','2026-10-20T10:01:00Z',kind='scenario',payload=s,dependencies=['test:o1'])
        l=self.ledger(e,a,c);commands=[command(),command('two','test:s2','2026-10-20T10:01:00Z','HYPOTHESIS_REVISED')]
        first=replay_recorded(l,commands);count=len(l.events())
        other=RecordedLedger(l.path,context=c,dataset_id=e['dataset_id']);self.addCleanup(other.close)
        self.assertEqual(first,replay_recorded(other,commands));self.assertEqual(count,len(other.events()))
    def test_entry_policy_enforced(self):
        e,a,c=scenario_case();p=c.policy();p['require_entry_permission']=True
        c=AuthorityContext(c.mode,replace(c.trust,policy_bytes=raw(p)));e['policy_sha256']=sha(c.trust.policy_bytes)
        r=replay_recorded(self.ledger(e,a,c),[command()])['results'][0]
        self.assertIn('TECHNICAL_PERMISSION_REQUIRED',r['eligibility']['reasons']);self.assertIsNone(r['preview'])
    def test_required_missing_yields(self):
        e,a,c=scenario_case();p=c.policy();p['inputs']['yields']['required']=True
        c=AuthorityContext(c.mode,replace(c.trust,policy_bytes=raw(p)));e['policy_sha256']=sha(c.trust.policy_bytes)
        r=replay_recorded(self.ledger(e,a,c),[command()])['results'][0]
        self.assertEqual(r['eligibility']['status'],'DEFERRED');self.assertIn('yields:MISSING',r['scenario']['reasons'])
    def test_old_synthetic_api_still_rejects_recorded(self):
        from offline_shadow_boundary import _policy,InputRejected
        with self.assertRaises(InputRejected):_policy({'classification':'recorded_offline'})
    def test_exact_retry_no_duplicate_preview(self):
        e,a,c=scenario_case();l=self.ledger(e,a,c);replay_recorded(l,[command()]);n=len(l.events())
        RecordedNotificationAudit(l).generate('another',eligibility_key='eligibility:one')
        self.assertEqual(n,len(l.events()))
