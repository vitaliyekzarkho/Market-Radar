import unittest,tempfile,os,json,hashlib,shutil,io
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout
import scenario_state as s
import alert_policy as ap
ROOT=Path(__file__).resolve().parent
class AdministrativeMigrationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.cwd=Path.cwd();os.chdir(self.tmp.name);self.addCleanup(os.chdir,self.cwd)
  shutil.copytree(ROOT/'fixtures/legacy_deployment','data')
  self.raw=s.OUTPUT_FILE.read_bytes();self.sha=hashlib.sha256(self.raw).hexdigest()
  Path('data/admission/legacy_migration_authorization.json').write_text(json.dumps({'policy':'ARCHIVE_UNVERIFIED_REINITIALIZE','legacy_sha256':self.sha}))
 def run_main(self):
  with patch.dict(os.environ,{'MARKET_RADAR_ADMISSION':'1'}),redirect_stdout(io.StringIO()):s.main()
  return json.loads(s.OUTPUT_FILE.read_text(encoding='utf-8'))
 def test_real_case_archive_restart_and_no_false_reversal_notification(self):
  old=json.loads(self.raw);self.assertEqual(old['primary_scenario']['direction'],'bearish')
  new=self.run_main();m=new['migration']
  self.assertEqual(Path(m['archive']).read_bytes(),self.raw)
  self.assertEqual(m['status'],'LEGACY_UNVERIFIED');self.assertFalse(m['market_reversal_confirmed'])
  self.assertEqual(m['historical_availability'],'UNKNOWN')
  self.assertNotIn('4179.07000',str(new['primary_scenario']['conditions']))
  events={'events':[{'type':'DIRECTION_CHANGED'}]}
  _,old_policy=ap.policy_decision({},old,{})
  old_policy['last_notified_fingerprint']='preserved-delivery-marker'
  output,state=ap.policy_decision(old_policy,new,events)
  self.assertFalse(output['should_notify']);self.assertEqual(output['notification_type'],'NONE')
  self.assertEqual(state['last_notified_fingerprint'],'preserved-delivery-marker')
  restarted=self.run_main();self.assertEqual(restarted['migration'],m)
  self.assertEqual(Path(m['archive']).read_bytes(),self.raw)
  self.assertEqual(restarted['primary_scenario']['direction'],new['primary_scenario']['direction'])
  repeat,again=ap.policy_decision(state,restarted,{})
  self.assertFalse(repeat['should_notify']);self.assertEqual(again['administrative_migration_id'],self.sha)
 def test_wrong_authorization_hash_does_not_reset_state(self):
  Path('data/admission/legacy_migration_authorization.json').write_text(json.dumps({'policy':'ARCHIVE_UNVERIFIED_REINITIALIZE','legacy_sha256':'wrong'}))
  with self.assertRaisesRegex(ValueError,'MIGRATION'):self.run_main()
  self.assertEqual(s.OUTPUT_FILE.read_bytes(),self.raw)
 def test_archive_conflict_fails_before_overwrite(self):
  p=Path('data/admission/migration')/(self.sha+'.legacy.json');p.parent.mkdir(parents=True);p.write_bytes(b'conflict')
  with self.assertRaisesRegex(ValueError,'ARCHIVE_CONFLICT'):self.run_main()
  self.assertEqual(s.OUTPUT_FILE.read_bytes(),self.raw)
if __name__=='__main__':unittest.main()
