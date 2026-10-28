import unittest,json,tempfile,sqlite3,csv,copy
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from cdr_operational import mapped,observe
from decision_journal import DecisionJournal
from mt5_time_validation import inspect_times,require_admitted_capture
ROOT=Path(__file__).resolve().parent
SILENT=json.loads((ROOT/'verification_inputs/native_first_run/scenario.json').read_text('utf-8'))

class NoPrimaryTests(unittest.TestCase):
 def fixture(self,root,payload=None):
  for name,value in [('scenario_states',SILENT if payload is None else payload),('alert_policy',{'notification_type':'NONE','should_notify':False}),('notifications',{'notification_type':'NONE','message':''})]:
   p=root/'data'/name/'GOLD_pro.json';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value),encoding='utf-8')
 def test_real_silent_does_not_invent_hypothesis_or_targets(self):
  t=mapped(SILENT)
  self.assertEqual(t['observation_type'],'NON_PUBLISHABLE')
  for key in ['primary','direction','point_a','point_b','FTA','TA']:self.assertIsNone(t[key])
  self.assertFalse(t['entry_allowed']);self.assertEqual(t['permissions'],{'entry':False,'trade':False})
 def test_nested_context_and_htf_preserved_without_primary(self):
  t=mapped(SILENT);self.assertEqual(t['legacy_market_state'],SILENT['market_state'])
  self.assertEqual(t['legacy_market_state']['active_counter_leg']['direction'],'bullish')
  self.assertEqual(t['htf'],SILENT['market_state']['timeframe_hierarchy'])
 def test_full_chain_no_eligibility_and_restart_retry(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.fixture(root)
   stages=[observe(root,s,'native_silent') for s in ['capture','scenario','policy','notification']]
   self.assertEqual(stages[1]['assessment']['status'],'NOT_EVALUATED')
   self.assertEqual(stages[1]['eligibility']['status'],'NOT_ELIGIBLE')
   self.assertFalse(stages[1]['eligibility']['delivery_authorized'])
   self.assertEqual(stages[-1]['outcome'],'NO_NOTIFICATION')
   self.assertEqual(observe(root,'notification','native_silent'),stages[-1])
   self.assertEqual(len(DecisionJournal(root/'data/cdr/journal').scan()[0]),4)
   db=sqlite3.connect(root/'data/cdr/recorded.db');events=[json.loads(r[0]) for r in db.execute('SELECT body FROM events')];db.close()
   self.assertFalse(any(e['kind'] in {'SCENARIO','NOTIFICATION_PREVIEW','ELIGIBILITY'} for e in events))
   self.assertEqual(sum(e['kind']=='NON_ASSESSMENT' for e in events),1)
 def test_policy_conflict_is_not_silently_overridden(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.fixture(root);observe(root,'scenario','x')
   (root/'data/alert_policy/GOLD_pro.json').write_text('{"should_notify":true,"notification_type":"NEW"}')
   with self.assertRaisesRegex(RuntimeError,'NO_PRIMARY_POLICY_CONFLICT'):observe(root,'policy','x')
 def test_published_fixture_keeps_existing_contract(self):
  s=json.loads((ROOT/'verification_inputs/recorded_data/scenario_states/GOLD_pro.json').read_text('utf-8'))
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.fixture(root,s);e=observe(root,'scenario','published')
   self.assertEqual(e['assessment']['kind'],'SCENARIO');self.assertEqual(e['technical']['legacy_primary'],s['primary_scenario'])
   self.assertEqual(e['eligibility']['status'],'DEFERRED')
 def test_invalid_published_direction_still_rejected(self):
  s=copy.deepcopy(SILENT);s['publication_state']='building_context';s['primary_scenario']={'direction':None}
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.fixture(root,s)
   with self.assertRaisesRegex(Exception,'TECHNICAL_PERMISSION_EXPLICIT_REQUIRED'):observe(root,'scenario','invalid')
 def test_null_primary_in_non_silent_state_is_not_nonpublishable(self):
  s=copy.deepcopy(SILENT);s['publication_state']='building_context'
  self.assertNotEqual(mapped(s).get('observation_type'),'NON_PUBLISHABLE')
 def test_nonempty_notification_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.fixture(root);observe(root,'scenario','x')
   (root/'data/notifications/GOLD_pro.json').write_text('{"message":"BUY"}')
   with self.assertRaisesRegex(RuntimeError,'NO_PRIMARY_NOTIFICATION_CONFLICT'):observe(root,'notification','x')

class TimeTests(unittest.TestCase):
 def test_all_five_actual_series_are_unverified_without_authority(self):
  receipt=next((ROOT/'verification_inputs/native_first_run/receipts').glob('*/receipt.json'))
  reports={}
  for o in json.loads(receipt.read_text())['observations']:
   rows=list(csv.DictReader((receipt.parent/(o['timeframe']+'.csv')).read_text().splitlines()))
   reports[o['timeframe']]=inspect_times(rows,o['timeframe'],o['received_at_utc'])
   self.assertEqual(len(reports[o['timeframe']]),500)
   self.assertTrue(all(r['time_status']=='TIME_UNVERIFIED' and r['is_closed'] is None and not r['historical_confirmation_allowed'] for r in reports[o['timeframe']]))
  self.assertEqual(sum('CANDIDATE_OPEN_AFTER_RECEIPT' in r['reasons'] for r in reports['M15']),3)
  self.assertEqual(sum('CANDIDATE_CLOSE_AFTER_RECEIPT' in r['reasons'] for r in reports['H1']),1)
  with self.assertRaisesRegex(RuntimeError,'TIME_UNVERIFIED'):require_admitted_capture(reports)
 def test_epoch_not_shifted_and_past_not_promoted(self):
  epoch=1793095200;r=inspect_times([{'time':epoch}],'M15','2026-10-27T12:00:00Z')[0]
  self.assertEqual(r['raw_mt5_epoch'],epoch);self.assertEqual(datetime.fromisoformat(r['candidate_utc_open']).timestamp(),epoch)
  self.assertIsNone(r['is_closed']);self.assertFalse(r['historical_confirmation_allowed'])
 def test_future_close_even_when_open_in_past(self):
  t=int(datetime(2026,10,27,9,tzinfo=timezone.utc).timestamp());r=inspect_times([{'time':t}],'H1','2026-10-27T09:35:47Z')[0]
  self.assertNotIn('CANDIDATE_OPEN_AFTER_RECEIPT',r['reasons']);self.assertIn('CANDIDATE_CLOSE_AFTER_RECEIPT',r['reasons'])
 def test_dublin_dst_does_not_change_epoch(self):
  z=ZoneInfo('Europe/Dublin')
  for stamp,offset in [('2026-10-25T00:30:00+00:00',3600),('2026-10-25T01:30:00+00:00',0),('2026-10-27T09:35:47+00:00',0)]:
   d=datetime.fromisoformat(stamp);self.assertEqual(d.astimezone(z).utcoffset().total_seconds(),offset);self.assertEqual(d.astimezone(z).timestamp(),d.timestamp())
 def test_non_utc_receipt_rejected(self):
  with self.assertRaises(ValueError):inspect_times([{'time':0}],'H1','2026-10-27T09:00:00+01:00')
 def test_empty_capture_not_admitted(self):
  with self.assertRaises(RuntimeError):require_admitted_capture({})
 def test_snapshot_retains_receipt_but_does_not_promote_unverified_rows(self):
  import sys,types,importlib,os,pandas as pd
  fake=types.ModuleType('MetaTrader5')
  for i,name in enumerate(['W1','D1','H4','H1','M15']):setattr(fake,'TIMEFRAME_'+name,i)
  fake.shutdown=lambda:None
  with patch.dict(sys.modules,{'MetaTrader5':fake}):
   snapshot=importlib.import_module('save_snapshot')
  data=pd.DataFrame({'time':range(1,9),'utc_time':pd.to_datetime(range(1,9),unit='s',utc=True),'open':[1]*8,'high':[2]*8,'low':[0]*8,'close':[1]*8})
  with tempfile.TemporaryDirectory() as d:
   before=os.getcwd();os.chdir(d)
   try:
    cached=Path('data/raw/GOLD_pro/H1.csv');cached.parent.mkdir(parents=True);cached.write_bytes(b'previous-cache-preserved')
    with patch.object(snapshot,'connect'),patch.object(snapshot,'get_rates',return_value=data.copy()),patch.object(snapshot,'mt5',fake):
     with self.assertRaisesRegex(RuntimeError,'TIME_UNVERIFIED'):snapshot.main()
    receipt=next(Path('data/receipts').glob('*/receipt.json'))
    self.assertTrue((receipt.parent/'time_validation.json').exists())
    self.assertEqual(list(Path('data/raw').rglob('*.csv')),[cached])
    self.assertEqual(cached.read_bytes(),b'previous-cache-preserved')
    self.assertTrue(all(o['closure_verified'] is False for o in json.loads(receipt.read_text())['observations']))
   finally:os.chdir(before)

if __name__=='__main__':unittest.main()
