import unittest,tempfile,json,copy
from pathlib import Path
from cdr_news_bridge import connect_news
from cdr_operational import observe,mapped
from news_lifecycle_store import Store
from news_lifecycle_engine import Lifecycle
from news_notification_policy import Policy
T='2026-10-24T15:00:00Z'
class CompletionTests(unittest.TestCase):
 def test_cross_installation_detection(self):
  from operational_startup_gate import competing_radars
  rows=[{'ProcessId':1,'ParentProcessId':0,'Name':'python.exe','CommandLine':'python C:/old/run_radar.py'},
        {'ProcessId':2,'ParentProcessId':1,'Name':'python.exe','CommandLine':'python C:/new/run_radar.py'},
        {'ProcessId':3,'ParentProcessId':0,'Name':'python.exe','CommandLine':'python C:/other/run_radar.py'}]
  self.assertEqual(competing_radars(rows,2),[3])
  self.assertEqual(competing_radars(rows[:2],2),[])
 def test_absent_news_not_invented(self):
  r=connect_news({}, {}, {},cutoff=T)
  self.assertIsNone(r['event_id']);self.assertIsNone(r['times']['interpretation_available_at_utc']);self.assertFalse(r['causality_confirmed'])
 def test_event_mismatch_not_admitted(self):
  r=connect_news({}, {'event':{'event_id':'a'},'reaction_report':{'event_id':'b'}}, {},cutoff=T)
  self.assertIsNone(r['validation']);self.assertFalse(r['trading_permission'])
 def test_permissions_always_denied_without_mutation(self):
  s={'primary_scenario':{'permissions':{'entry':True}}};r=mapped(s)
  self.assertFalse(r['permissions']['entry']);self.assertTrue(s['primary_scenario']['permissions']['entry'])
 def test_local_lifecycle_restart_and_contradiction(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'cdr/news.db';p.parent.mkdir()
   policy=Policy('audit_v1',86400,3600,900,300,3600,1,60,30)
   s=Store(p,namespace='audit',mode='operational_audit');l=Lifecycle(s,policy)
   item={'event_id':'a','directional_comparison':'directionally_supported'}
   first=l.observe_local(item,now=T);s.close()
   s=Store(p,namespace='audit',mode='operational_audit');l=Lifecycle(s,policy)
   self.assertTrue(l.observe_local(item,now=T)['duplicate'])
   item['directional_comparison']='directionally_contradicted';r=l.observe_local(item,now=T)
   self.assertEqual(r['previous_state'],first['state']);self.assertIsNone(r['historical_available_at_utc']);self.assertEqual(r['notification_ids'],[])
   self.assertEqual(s.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],0);s.close()
 def test_refutation_requires_production_confirmation(self):
  source=Path(__file__).parent/'verification_inputs/recorded_data/scenario_states/GOLD_pro.json'
  for confirmed in (False,True):
   scenario=json.loads(source.read_text('utf-8-sig'))
   with self.subTest(confirmed=confirmed),tempfile.TemporaryDirectory() as d:
    root=Path(d);p=root/'data/scenario_states/GOLD_pro.json';p.parent.mkdir(parents=True);p.write_text(json.dumps(scenario))
    observe(root,'scenario','one')
    scenario['primary_scenario']['direction']='bullish' if scenario['primary_scenario']['direction']=='bearish' else 'bearish'
    scenario['market_state']['hypothesis_transition']={'switch_confirmed':confirmed}
    p.write_text(json.dumps(scenario));b=observe(root,'scenario','two')
    kinds=[x['kind'] for x in b['assessment']['observed_changes']]
    self.assertIn('PRODUCTION_REFUTATION_OBSERVED' if confirmed else 'DIRECTION_CHANGE_UNCONFIRMED',kinds)
    self.assertEqual(b['eligibility']['status'],'DEFERRED')
 def test_dublin_dst_window(self):
  from zoneinfo import ZoneInfo
  from datetime import datetime,timezone
  from run_radar import inside_active_window
  zone=ZoneInfo('Europe/Dublin')
  summer=datetime(2026,10,24,7,tzinfo=timezone.utc).astimezone(zone)
  winter=datetime(2026,10,25,8,tzinfo=timezone.utc).astimezone(zone)
  self.assertEqual(summer.hour,8);self.assertEqual(winter.hour,8)
  self.assertTrue(inside_active_window(summer));self.assertTrue(inside_active_window(winter))
 def test_target_progression_journal(self):
  source=Path(__file__).parent/'verification_inputs/recorded_data/scenario_states/GOLD_pro.json'
  scenario=json.loads(source.read_text('utf-8-sig'))
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);p=root/'data/scenario_states/GOLD_pro.json';p.parent.mkdir(parents=True);p.write_text(json.dumps(scenario))
   a=observe(root,'scenario','one');scenario['market_state']['primary']['point_b']['price']=3900;p.write_text(json.dumps(scenario))
   b=observe(root,'scenario','two')
   self.assertEqual(a['technical']['primary']['id'],b['technical']['primary']['id'])
   self.assertTrue(any(x['kind']=='TARGET_CHANGE_OBSERVED' for x in b['assessment']['observed_changes']))
   self.assertEqual(b['eligibility']['status'],'DEFERRED')
   self.assertTrue((root/'data/cdr/DECISION_JOURNAL.md').exists())
if __name__=='__main__':unittest.main()
