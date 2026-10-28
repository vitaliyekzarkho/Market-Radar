import unittest,tempfile,socket
from pathlib import Path
from dataclasses import replace
from copy import deepcopy
from recovery_fixtures import case,add_observation,raw
from test_recorded_recovery_c import scenario_case,scenario
from test_recorded_recovery_d import command
from recorded_context import AuthorityContext,ROLES
from recorded_authority import sha,Rejected,TrustStore
from recorded_ledger import RecordedLedger
from recorded_assessment import replay_recorded
from evidence_recorder import read_file

class RecoveryF(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
    def ledger(self,e,a,c,name='l.db'):
        l=RecordedLedger(Path(self.tmp.name)/name,context=c,dataset_id=e['dataset_id']);self.addCleanup(l.close);l.ingest('all',raw(e),a);return l
    def test_correction_fork_rejected(self):
        e,a,c=case();c=add_observation(e,a,c,'test:o2','2026-10-20T10:00:10Z',predecessor='test:o1')
        c=add_observation(e,a,c,'test:o3','2026-10-20T10:00:11Z',predecessor='test:o1')
        with self.assertRaises(Rejected):self.ledger(e,a,c)
    def test_cycle_rejected(self):
        e,a,c=case();c=add_observation(e,a,c,'test:o2','2026-10-20T10:00:10Z',predecessor='test:o1')
        e['observations'][0]['supersedes']='test:o2'
        with self.assertRaises(Rejected):self.ledger(e,a,c)
    def test_refutation_and_no_reactivation(self):
        e,a,c=scenario_case();s=scenario();s['primary']['state']='refuted'
        c=add_observation(e,a,c,'test:s2','2026-10-20T10:01:00Z',kind='scenario',payload=s,dependencies=['test:o1'])
        c=add_observation(e,a,c,'test:s3','2026-10-20T10:02:00Z',kind='scenario',payload=scenario(),dependencies=['test:o1'])
        l=self.ledger(e,a,c);r=replay_recorded(l,[command(),command('two','test:s2','2026-10-20T10:01:00Z','HYPOTHESIS_REFUTED')])
        self.assertEqual(r['results'][1]['eligibility']['status'],'DEFERRED');self.assertIsNone(r['results'][1]['preview'])
        before=l.events()
        with self.assertRaises(Rejected):replay_recorded(l,[command('three','test:s3','2026-10-20T10:02:00Z','HYPOTHESIS_REVISED')])
        self.assertEqual(before,l.events())
    def test_conflicting_input_defers(self):
        e,a,c=case();c=add_observation(e,a,c,'test:o2','2026-10-20T10:00:10Z',predecessor='test:o1',payload={'status':'conflicts'})
        c=add_observation(e,a,c,'test:s1','2026-10-20T10:00:20Z',kind='scenario',payload=scenario(),dependencies=['test:o2'])
        l=self.ledger(e,a,c);cmd=command();cmd['refs']['technical']='test:o2'
        r=replay_recorded(l,[cmd])['results'][0]
        self.assertIn('technical:CONFLICT',r['scenario']['reasons']);self.assertIsNone(r['preview'])
    def test_obsolete_dependency_cannot_justify(self):
        e,a,c=scenario_case();c=add_observation(e,a,c,'test:o2','2026-10-20T10:00:15Z',predecessor='test:o1')
        r=replay_recorded(self.ledger(e,a,c),[command()])['results'][0]
        self.assertEqual(r['eligibility']['status'],'DEFERRED')
    def test_missing_completion_proof(self):
        e,a,c=scenario_case();a.pop('test:s1:completion');r=replay_recorded(self.ledger(e,a,c),[command()])['results'][0]
        self.assertEqual(r['eligibility']['status'],'DEFERRED')
    def test_test_pins_cannot_promote_to_captured(self):
        e,a,c=case();p=c.policy();p['authority_class']='CAPTURED_AUTHORITY'
        bad=AuthorityContext('CAPTURED_AUTHORITY',replace(c.trust,policy_bytes=raw(p)))
        with self.assertRaises(Rejected):bad.policy()
    def test_captured_without_audited_receipts_stays_unknown(self):
        # Captured-labelled assertions, no real receipts and no fabricated audit.
        e,a,c=case();unknown=deepcopy(e['observations'][0]['scenario_established_at_utc'])
        e['dataset_id']='captured:unadmitted';e['receipts']=[]
        o=e['observations'][0];o['source_id']='captured:source'
        for k in ('market_event_at_utc','captured_at_utc','available_at_utc'):o[k]=deepcopy(unknown)
        p=c.policy();p['authority_class']='CAPTURED_AUTHORITY'
        for rule in p['inputs'].values():rule['source_ids']=['captured:source']
        t=TrustStore(raw(p),frozenset({'captured:authority'}),frozenset({'captured:source'}),frozenset(),'Unadmitted source policy; zero receipt pins')
        e['policy_sha256']=sha(t.policy_bytes)
        r=AuthorityContext('CAPTURED_AUTHORITY',t).assess(raw(e),{'test:file':a['test:file']})
        self.assertEqual(r['observations'][0]['status'],'UNVERIFIED')
    def test_journal_tail_corruption_detected(self):
        e,a,c=scenario_case();l=self.ledger(e,a,c);replay_recorded(l,[command()]);l.db.execute('DELETE FROM events WHERE seq=(SELECT MAX(seq) FROM events)')
        with self.assertRaises(Rejected):l.integrity()
    def test_assessment_corruption_detected(self):
        e,a,c=case();l=self.ledger(e,a,c);l.db.execute("UPDATE observations SET assessment='{}'")
        with self.assertRaises(Rejected):l.integrity()
    def test_production_read_denied(self):
        with self.assertRaises(PermissionError):open('C:/Users/User/MarketRadar/DO_NOT_READ.txt','rb')
    def test_network_denied(self):
        with socket.socket() as s:
            with self.assertRaises(PermissionError):s.connect(('127.0.0.1',9))
    def test_live_module_denied(self):
        import sys
        # Existing tests may have temporarily mocked the module; remove only
        # a preexisting fixture binding and restore it after the probe.
        old=sys.modules.pop('MetaTrader5',None)
        try:
            with self.assertRaises((PermissionError,ImportError)):__import__('MetaTrader5')
        finally:
            if old is not None:sys.modules['MetaTrader5']=old
    def test_secret_file_denied(self):
        with self.assertRaises(PermissionError):open(Path(self.tmp.name)/'.env','rb')
    def test_source_inputs_not_modified(self):
        e,a,c=scenario_case();before=raw(e);before_a=deepcopy(a);l=self.ledger(e,a,c);replay_recorded(l,[command()])
        self.assertEqual(before,raw(e));self.assertEqual(before_a,a)
    def test_backdated_replacement_rejected(self):
        e,a,c=case();c=add_observation(e,a,c,'test:o2','2026-10-20T09:00:00Z',predecessor='test:o1')
        with self.assertRaises(Rejected):self.ledger(e,a,c)
    def test_target_time_after_establishment_defers(self):
        e,a,c=case();s=scenario();s['point_b']['time']='2026-10-20T10:00:20Z'
        c=add_observation(e,a,c,'test:s1','2026-10-20T10:00:10Z',kind='scenario',payload=s,dependencies=['test:o1'])
        r=replay_recorded(self.ledger(e,a,c),[command()])['results'][0]
        self.assertIn('point_b:AFTER_SCENARIO_ESTABLISHMENT',r['scenario']['reasons']);self.assertIsNone(r['preview'])
