import tempfile,unittest,json,subprocess,sys
from pathlib import Path
from copy import deepcopy
from dataclasses import replace
from r5_fixtures import candles
from r5_legacy import CORE
from r5_bridge import run_replay
from recorded_authority import Rejected,sha
from recorded_context import AuthorityContext,canonical
from recovery_fixtures import case,add_observation,raw

ROOT=Path(__file__).resolve().parent

def setup_input(root,*,symbols=('XAUUSD',),second=False):
    root.mkdir();files={}
    for tf in (*CORE,'M5'):
        name=tf+'.csv';(root/name).write_bytes(candles(tf,count=40));files[tf]=name
    events=[dict(id='event:'+s,symbol=s,cutoff='2026-10-20T12:00:00Z',files=files) for s in symbols]
    if second:events.append(dict(events[0],id='event:later',cutoff='2026-10-20T12:15:00Z'))
    manifest=dict(mode='retrospective',events=events)
    (root/'manifest.json').write_text(canonical(manifest),encoding='utf-8')
    return root/'manifest.json',manifest

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def test_retrospective_multi_symbol_sequence_restart(self):
        path,m=setup_input(self.root/'input',symbols=('XAUUSD','EURUSD'),second=True)
        first=run_replay(path,self.root/'out');second=run_replay(path,self.root/'out')
        self.assertEqual(first,second);self.assertEqual(len(first['events']),3)
        for e in first['events']:
            self.assertEqual(e['analysis']['status'],'ANALYZED_RETROSPECTIVELY')
            self.assertEqual(e['eligibility']['status'],'DEFERRED');self.assertFalse(e['historical_notification_generated'])
            self.assertEqual(e['authority_class'],'UNVERIFIED_HISTORICAL')
            if e['recorded_scenario']:
                self.assertEqual(e['recorded_scenario']['technical']['legacy_primary'],e['analysis']['scenario']['primary_scenario'])
    def test_interruption_resume_and_duplicate_event(self):
        path,m=setup_input(self.root/'input');m['events'].append(deepcopy(m['events'][0]));path.write_text(canonical(m))
        with self.assertRaises(RuntimeError):run_replay(path,self.root/'out',fail_after=m['events'][0]['id'])
        restored=run_replay(path,self.root/'out');fresh=run_replay(path,self.root/'fresh')
        self.assertEqual(restored,fresh);self.assertEqual(len(restored['events']),1)
    def test_missing_tf_explicit(self):
        path,m=setup_input(self.root/'input');del m['events'][0]['files']['D1'];path.write_text(canonical(m))
        e=run_replay(path,self.root/'out')['events'][0]
        self.assertEqual(e['analysis']['status'],'INSUFFICIENT_DATA');self.assertEqual(e['eligibility']['status'],'DEFERRED')
    def test_recorded_mode_requires_external_context(self):
        path,m=setup_input(self.root/'input');m['mode']='recorded';path.write_text(canonical(m))
        with self.assertRaises(Rejected):run_replay(path,self.root/'out')
    def test_recorded_receipts_do_not_attest_recomputed_scenario(self):
        path,m=setup_input(self.root/'input');e,a,c=case();e['observations']=[];e['receipts']=[];a={}
        # Fixture helper needs one template; use original, then add each distinct chain.
        template,_,_=case();e['observations']=template['observations'];e['receipts']=template['receipts'];_,a,_=case()
        mapping={}
        for tf in (*CORE,'M5'):
            oid='test:bars:'+tf;b=(path.parent/(tf+'.csv')).read_bytes()
            c=add_observation(e,a,c,oid,'2026-10-20T12:00:00Z',payload={})
            o=e['observations'][-1];o['logical_item_id']=oid
            # Bind exact CSV bytes in this clearly artificial receipt fixture.
            o.update(file_sha256=sha(b),payload_sha256=sha(b),payload_locator=f'bytes:0:{len(b)}');a[o['file_ref']]=b
            pins=set(c.trust.audited_receipt_hashes)
            for r in e['receipts']:
                if r['subject_id']==oid:
                    r['subject_payload_sha256']=sha(b);proof=raw({k:v for k,v in r.items() if k not in {'artifact_ref','artifact_sha256'}})
                    r['artifact_sha256']=sha(proof);a[r['artifact_ref']]=proof;pins.add(sha(proof))
            c=AuthorityContext(c.mode,replace(c.trust,audited_receipt_hashes=frozenset(pins)));mapping[tf]=oid
        p=c.policy();p['inputs']['technical']['source_ids'].append('test:r5-derived')
        c=AuthorityContext(c.mode,replace(c.trust,source_ids=c.trust.source_ids|{'test:r5-derived'},policy_bytes=raw(p)))
        e['policy_sha256']=sha(c.trust.policy_bytes);e['historical_cutoff_utc']='2026-10-20T12:00:00Z'
        (path.parent/'envelope.json').write_bytes(raw(e));paths={}
        for n,(ref,b) in enumerate(a.items()):
            name=f'artifact-{n}.bin';(path.parent/name).write_bytes(b);paths[ref]=name
        m.update(mode='recorded',dataset_id=e['dataset_id'])
        m['events'][0].update(envelope='envelope.json',artifacts=paths,series_observations=mapping);path.write_text(canonical(m))
        r=run_replay(path,self.root/'out',context=c)['events'][0]
        self.assertTrue(all(v=='VERIFIED' for v in r['source_selection'].values()))
        self.assertEqual(r['authority_class'],'TEST_AUTHORITY');self.assertEqual(r['eligibility']['status'],'DEFERRED')
        m['events'][0]['cutoff']='2026-10-20T11:59:59Z';path.write_text(canonical(m))
        future=run_replay(path,self.root/'future',context=c)['events'][0]
        self.assertTrue(all(v=='UNKNOWN_OR_SUPERSEDED' for v in future['source_selection'].values()))
        self.assertEqual(future['eligibility']['status'],'DEFERRED')
        m['events'][0]['cutoff']='2026-10-20T13:00:01Z';path.write_text(canonical(m))
        stale=run_replay(path,self.root/'stale',context=c)['events'][0]
        self.assertTrue(all(v=='STALE' for v in stale['source_selection'].values()))
        self.assertEqual(stale['eligibility']['status'],'DEFERRED')
    def test_cli_and_safety(self):
        path,m=setup_input(self.root/'input');m['events'][0]['files']={};path.write_text(canonical(m))
        p=subprocess.run([sys.executable,'-B',str(ROOT/'r5_offline.py'),str(path),str(self.root/'out')],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr);self.assertTrue((self.root/'out/report.json').exists())
        code="from r5_offline import install_guard; install_guard(); import socket; socket.socket().connect(('127.0.0.1',9))"
        p=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0);self.assertIn('R5_EXTERNAL_ACTION_FORBIDDEN',p.stderr)
    def test_same_output_root_locked(self):
        from offline_integration import single_instance
        path,m=setup_input(self.root/'input');out=self.root/'out';out.mkdir()
        with single_instance(out/'r5.lock'):
            with self.assertRaises(RuntimeError):run_replay(path,out)
    def test_output_owner_conflict(self):
        path,m=setup_input(self.root/'input');m['events']=[];path.write_text(canonical(m));run_replay(path,self.root/'out')
        m['description']='changed input';path.write_text(canonical(m))
        with self.assertRaises(Rejected):run_replay(path,self.root/'out')
    def test_production_output_rejected(self):
        path,m=setup_input(self.root/'input')
        with self.assertRaises(Rejected):run_replay(path,Path('C:/Users/User/MarketRadar'))
    def test_retrograde_event_order(self):
        path,m=setup_input(self.root/'input',second=True)
        for e in m['events']:e['files']={}
        m['events'].reverse();path.write_text(canonical(m))
        with self.assertRaises(Rejected):run_replay(path,self.root/'out')
    def test_computed_scenario_reaches_recorded_lifecycle(self):
        from r5_fixtures import rejection_candles
        path,m=setup_input(self.root/'input')
        for tf in (*CORE,'M5'):(path.parent/(tf+'.csv')).write_bytes(rejection_candles(tf))
        r=run_replay(path,self.root/'out')['events'][0]
        self.assertIsNotNone(r['analysis']['scenario']['primary_scenario'])
        self.assertIsNotNone(r['recorded_scenario'])
        self.assertEqual(r['recorded_scenario']['technical']['legacy_primary'],r['analysis']['scenario']['primary_scenario'])
        self.assertEqual(r['eligibility']['status'],'DEFERRED')
        self.assertIn('ESTABLISHMENT_UNKNOWN',r['recorded_scenario']['reasons'])
