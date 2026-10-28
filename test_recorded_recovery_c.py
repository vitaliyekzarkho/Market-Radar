import tempfile,unittest
from pathlib import Path
from copy import deepcopy
from recovery_fixtures import case,add_observation,raw
from recorded_ledger import RecordedLedger
from recorded_scenario import RecordedScenarios
from recorded_context import ROLES
from recorded_authority import Rejected

def scenario():
    return dict(point_a={'price':4180,'time':'2026-10-20T09:00:00Z'},point_b={'price':4073.545,'time':'2026-10-20T09:00:00Z'},
                direction='bearish',score=99,invalidation={'above':4200},FTA=4058.97,TA=[4044.76],permissions={'entry':False},
                primary={'id':'h1','direction':'bearish','parent_id':None,'state':'active'},nested=[],alternatives=[],htf={'D1':'bearish'})
def scenario_case():
    e,a,c=case();s=scenario();c=add_observation(e,a,c,'test:s1','2026-10-20T10:00:10Z',kind='scenario',payload=s,dependencies=['test:o1'])
    e['historical_cutoff_utc']='2026-10-20T10:00:30Z'
    return e,a,c

class RecoveryC(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
    def setup_case(self,e,a,c):
        l=RecordedLedger(Path(self.tmp.name)/'l.db',context=c,dataset_id=e['dataset_id']);self.addCleanup(l.close);l.ingest('all',raw(e),a)
        return l,RecordedScenarios(l)
    def record(self,sc,**kw):
        p=dict(key='first',scenario_id='route',observation_id='test:s1',cutoff='2026-10-20T10:00:30Z',
               refs={r:('test:o1' if r=='technical' else None) for r in ROLES},event_type='HYPOTHESIS_FORMED',target_id='h1');p.update(kw)
        return sc.record(**p)
    def test_preserve_exact_scenario(self):
        e,a,c=scenario_case();l,s=self.setup_case(e,a,c);r=self.record(s)
        self.assertEqual(r['technical'],scenario());self.assertEqual(r['status'],'VERIFIED');self.assertFalse(r['technical']['permissions']['entry'])
        self.assertEqual(r,self.record(s))
    def test_establishment_future(self):
        e,a,c=scenario_case();l,s=self.setup_case(e,a,c);r=self.record(s,cutoff='2026-10-20T10:00:05Z')
        self.assertEqual(r['status'],'DEFERRED')
    def test_missing_mandatory_role(self):
        e,a,c=scenario_case();l,s=self.setup_case(e,a,c)
        refs={r:None for r in ROLES}
        with self.assertRaises(Rejected): self.record(s,refs=refs)
    def test_no_new_id_via_revision(self):
        e,a,c=scenario_case();l,s=self.setup_case(e,a,c)
        with self.assertRaises(Rejected):self.record(s,event_type='HYPOTHESIS_REVISED')
        self.assertEqual(s.history('route'),[])
    def test_stale(self):
        e,a,c=scenario_case();l,s=self.setup_case(e,a,c);r=self.record(s,cutoff='2026-10-21T10:00:30Z')
        self.assertIn('technical:STALE',r['reasons'])
    def test_unknown_establishment(self):
        e,a,c=scenario_case();e['observations'][1]['scenario_established_at_utc']=deepcopy(e['observations'][0]['scenario_established_at_utc'])
        l,s=self.setup_case(e,a,c);self.assertIn('ESTABLISHMENT_UNKNOWN',self.record(s)['reasons'])
    def test_target_progression_same_hypothesis(self):
        e,a,c=scenario_case();nexts=scenario();nexts['point_b']={'price':4019.92,'time':None}
        c=add_observation(e,a,c,'test:s2','2026-10-20T10:01:00Z',kind='scenario',payload=nexts,dependencies=['test:o1'])
        l,s=self.setup_case(e,a,c);self.record(s);r=self.record(s,key='second',observation_id='test:s2',cutoff='2026-10-20T10:01:00Z',event_type='HYPOTHESIS_REVISED')
        self.assertEqual(r['technical']['primary']['id'],'h1');self.assertIsNone(r['technical']['point_b']['time']);self.assertEqual(len(s.history('route')),2)
        self.assertEqual(r['status'],'DEFERRED')
