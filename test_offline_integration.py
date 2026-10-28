"""Integration and safety tests run the guarded coordinator in fresh processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parent


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out=Path(self.temp.name)/'output'

    def run_case(self,case='normal',fail=None):
        code='from offline_integration import run_case; import sys; run_case(sys.argv[1],sys.argv[2],fail_after='+repr(fail)+')'
        p=subprocess.run([sys.executable,'-B','-c',code,str(self.out),case],cwd=ROOT,capture_output=True,text=True,timeout=30)
        if fail:
            self.assertNotEqual(p.returncode,0); self.assertIn('SIMULATED_INTERRUPTION',p.stderr)
            return None
        self.assertEqual(p.returncode,0,p.stderr)
        return json.loads((self.out/case/'report.json').read_text(encoding='utf-8'))

    def test_end_to_end_actual_analysis_and_journals(self):
        r=self.run_case()
        self.assertEqual(r['assessment']['final_status'],'SHADOW_CONSISTENT')
        self.assertEqual(r['notification']['mode'],'ACCEPTED_SYNTHETIC_LEDGER')
        self.assertEqual(r['notification']['status'],'ELIGIBLE')
        self.assertIsNotNone(r['synthetic_notification'])
        self.assertTrue(all(n>0 for n in r['ledger_counts']))
        self.assertFalse(r['notification']['delivery_authorized'])
        self.assertEqual(r['external_actions'],0)

    def test_mapping_preserves_technical_semantics(self):
        r=self.run_case(); old=r['legacy']['payload']['primary_scenario']; new=r['mapped_scenario']
        self.assertEqual(new['legacy_primary'],old)
        for k in ('score','direction'):self.assertEqual(old[k],new[k])
        self.assertEqual(old['point_b']['price'],new['point_b']['price'])
        self.assertEqual(new['point_a']['price'],r['legacy']['payload']['market_state']['primary']['point_a']['price'])
        self.assertEqual(new['invalidation'],old['conditions']['invalidation'])
        self.assertFalse(new['permissions']['entry'])

    def test_repeated_event_and_restart_are_idempotent(self):
        a=self.run_case(); b=self.run_case()
        self.assertEqual(a,b)

    def test_restart_after_evidence_interruption(self):
        self.run_case(fail='evidence'); a=self.run_case(); b=self.run_case()
        self.assertEqual(a,b)

    def test_restart_after_generation_interruption(self):
        self.run_case(fail='generation'); a=self.run_case(); b=self.run_case()
        self.assertEqual(a,b)

    def test_missing_yields_no_notification(self):
        r=self.run_case('missing_yields');self.assertEqual(r['notification']['status'],'UNKNOWN');self.assertIsNone(r['synthetic_notification'])

    def test_stale_yields_no_notification(self):
        r=self.run_case('stale_yields');self.assertEqual(r['notification']['status'],'UNKNOWN');self.assertIsNone(r['synthetic_notification'])

    def test_missing_required_news(self):
        r=self.run_case('unknown_news');self.assertEqual(r['notification']['status'],'UNKNOWN');self.assertIsNone(r['synthetic_notification'])

    def test_conflicting_news_does_not_reverse_technical(self):
        r=self.run_case('conflicting_news');self.assertEqual(r['notification']['status'],'DEFERRED')
        self.assertEqual(r['mapped_scenario']['direction'],'bearish');self.assertIsNone(r['synthetic_notification'])

    def test_external_actions_and_credentials_blocked(self):
        code="""
from offline_integration import install_guard
import os
os.environ['TELEGRAM_BOT_TOKEN']='synthetic-sentinel'
install_guard()
assert 'TELEGRAM_BOT_TOKEN' not in os.environ
actions=[lambda:__import__('MetaTrader5'),lambda:__import__('telegram_notifier'),
 lambda:open(r'C:\\Users\\User\\MarketRadar\\run_radar.py'),
 lambda:__import__('socket').create_connection(('example.com',443)),
 lambda:__import__('subprocess').run(['cmd','/c','exit'])]
for action in actions:
 try:action()
 except (PermissionError,ImportError):pass
 else:raise AssertionError('external action allowed')
"""
        p=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stderr)

    def test_existing_unowned_output_rejected(self):
        self.out.mkdir();(self.out/'unrelated.txt').write_text('sentinel')
        p=subprocess.run([sys.executable,'-B','offline_integration.py','--output',str(self.out)],cwd=ROOT,capture_output=True,text=True,timeout=15)
        self.assertNotEqual(p.returncode,0);self.assertIn('OUTPUT_NOT_EMPTY',p.stderr)
        self.assertEqual((self.out/'unrelated.txt').read_text(),'sentinel')

    def test_single_instance_and_crash_release(self):
        self.out.mkdir();signal=self.out/'signal'
        code="from offline_integration import single_instance;from pathlib import Path;import sys,time;\nwith single_instance(Path(sys.argv[1])/'integration.lock'):\n Path(sys.argv[1],'signal').write_text('held');time.sleep(20)"
        child=subprocess.Popen([sys.executable,'-B','-c',code,str(self.out)],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+10
            while not signal.exists() and time.monotonic()<deadline:time.sleep(.05)
            self.assertTrue(signal.exists())
            p=subprocess.run([sys.executable,'-B','offline_integration.py','--output',str(self.out)],cwd=ROOT,capture_output=True,text=True,timeout=10)
            self.assertIn('OFFLINE_ALREADY_RUNNING',p.stderr)
        finally:
            child.kill();child.communicate(timeout=5)
        signal.unlink()
        self.assertEqual(self.run_case()['notification']['status'],'ELIGIBLE')

    def test_missing_point_time_fails_closed(self):
        code="from offline_integration import fixture,analyze,map_scenario;f=fixture();f.pop('point_b_established_at_utc');s=map_scenario(analyze(f));assert s['point_b']['time'] is None"
        p=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stderr)


if __name__=='__main__':unittest.main()
