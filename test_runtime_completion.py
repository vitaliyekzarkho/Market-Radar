import copy
import io
import json
import os
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import numpy as np
import run_radar
from time_ingestion import persist_capture, capture_source_rows, refreshed_delivery_window
from test_historical_admission_pipeline import capture
from test_time_authority_integration import fixture

SOURCE=Path(__file__).resolve().parent
class Fixed(datetime):
    @classmethod
    def now(cls,tz=None):return datetime(2026,10,27,14,0,5,tzinfo=timezone.utc)

def all_frames():
    c=capture()
    for tf in ('W1','D1','H4','H1'):
        other=capture(tf)
        for i,s in enumerate(c['samples']):s['measurements'].append(other['samples'][i]['measurements'][1])
    return c

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.cwd=Path.cwd();os.chdir(self.root)
    def tearDown(self):os.chdir(self.cwd);self.tmp.cleanup()
    def write(self,name,value):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value),encoding='utf-8')
    def execute(self,name):
        with patch.object(sys,'argv',[name]),redirect_stdout(io.StringIO()):
            try:runpy.run_path(str(SOURCE/name),run_name='__main__')
            except SystemExit as exc:
                if exc.code not in (None,0):raise

    def test_runner_capture_to_journal_to_notification_without_native_transport(self):
        calls=[]
        # Acquisition and external providers are fixture boundaries. The runner,
        # admission, existing scenario/reasoning, policy, builder and journal run.
        local={'scenario_state.py','trade_idea_engine.py','event_monitor.py','alert_policy.py',
               'notification_builder.py','volume_context_engine.py','rates_context_engine.py',
               'oil_context_engine.py','live_reaction_engine.py','fomc_market_reaction_engine.py'}
        def step(root,name,verbose,report_failure=True):
            calls.append(name)
            if name=='save_snapshot.py':
                persist_capture(root,'fixture',all_frames(),'2026-10-27T14:00:05Z')
                return True,'HISTORICAL_ADMISSION_CAPTURE: fixture'
            if name in local:self.execute(name)
            return True,'OFFLINE_PROVIDER_FIXTURE' if name not in local else ''
        with patch.dict(os.environ,{'MARKET_RADAR_ADMISSION':'1'}),patch.object(run_radar,'datetime',Fixed),patch.object(run_radar,'run_step',side_effect=step),redirect_stdout(io.StringIO()):
            self.assertTrue(run_radar.run_pipeline(self.root,False,no_telegram=True))
        self.assertNotIn('telegram_notifier.py',calls)
        self.assertNotIn('session_context.py',calls)
        s=json.loads((self.root/'data/scenario_states/GOLD_pro.json').read_text('utf-8'))
        self.assertEqual(s['time_basis'],'SOURCE_ORDER_ONLY')
        n=json.loads((self.root/'data/notifications/GOLD_pro.json').read_text('utf-8'))
        self.assertFalse(n['should_send'])  # first observation is baseline, not an alert
        j=json.loads((self.root/'data/cdr/DECISION_JOURNAL.json').read_text('utf-8'))
        self.assertEqual([e['stage'] for e in j],['capture','scenario','policy','notification'])
        self.assertTrue(all(not e['trade_authorized'] for e in j))
        # A restart with identical content does not reset existing scenario/policy.
        with patch.dict(os.environ,{'MARKET_RADAR_ADMISSION':'1'}),patch.object(run_radar,'datetime',Fixed),patch.object(run_radar,'run_step',side_effect=step),redirect_stdout(io.StringIO()):
            self.assertTrue(run_radar.run_pipeline(self.root,False,no_telegram=True))
        n=json.loads((self.root/'data/notifications/GOLD_pro.json').read_text('utf-8'))
        self.assertFalse(n['should_send'])

    def test_incomplete_capture_never_reaches_context_or_policy(self):
        def step(root,name,*args,**kwargs):
            self.assertEqual(name,'save_snapshot.py')
            persist_capture(root,'incomplete',capture(),'2026-10-27T14:00:05Z')
            return True,'HISTORICAL_ADMISSION_CAPTURE:'
        with patch.object(run_radar,'datetime',Fixed),patch.object(run_radar,'run_step',side_effect=step):
            self.assertFalse(run_radar.run_pipeline(self.root,False,no_telegram=True))
        self.assertFalse((self.root/'data/notifications/GOLD_pro.json').exists())

    def test_recorded_ohlcv_valid_scenario_reaches_notification_boundary(self):
        from test_alert_policy import scenario, state
        # Recorded capture is unchanged; only acquisition and remote providers
        # are substituted. The complete existing analytical/decision path runs.
        captured=json.loads((SOURCE/'recorded_replay_final/data/receipts/saved_native_20261027/clock_capture.json').read_text('utf-8'))
        class RecordedNow(datetime):
            @classmethod
            def now(cls,tz=None):return datetime.fromisoformat('2026-10-27T14:30:37.996063+00:00')
        self.write('data/runtime/GOLD_pro_alert_policy_state.json',state(scenario(direction='bullish')))
        import alert_policy
        self.write(str(alert_policy.STATE_FILE),state(scenario(direction='bullish')))
        local={'scenario_state.py','trade_idea_engine.py','event_monitor.py','alert_policy.py',
               'notification_builder.py','volume_context_engine.py','rates_context_engine.py',
               'oil_context_engine.py','live_reaction_engine.py','fomc_market_reaction_engine.py'}
        def step(root,name,verbose,report_failure=True):
            if name=='save_snapshot.py':
                persist_capture(root,'recorded',captured,RecordedNow.now().isoformat())
                return True,'HISTORICAL_ADMISSION_CAPTURE: recorded offline'
            if name in local:self.execute(name)
            return True,''
        with patch.dict(os.environ,{'MARKET_RADAR_ADMISSION':'1'}),patch.object(run_radar,'datetime',RecordedNow),patch.object(run_radar,'run_step',side_effect=step),redirect_stdout(io.StringIO()):
            self.assertTrue(run_radar.run_pipeline(self.root,False,no_telegram=True))
        s=json.loads((self.root/'data/scenario_states/GOLD_pro.json').read_text('utf-8'))
        self.assertEqual(s['primary_scenario']['direction'],'bearish')
        self.assertEqual(s['primary_scenario']['score'],47)
        n=json.loads((self.root/'data/notifications/GOLD_pro.json').read_text('utf-8'))
        self.assertTrue(n['should_send'])
        self.assertTrue(n['message'])
        import telegram_notifier as t
        self.assertTrue(t.validate_notification(n)[0])
        # A matching legacy snapshot can preserve continuity only after fresh
        # admitted calculations independently reproduce its structural identity.
        legacy=copy.deepcopy(s);legacy.pop('time_basis')
        self.write('data/scenario_states/GOLD_pro.json',legacy)
        with patch.dict(os.environ,{'MARKET_RADAR_ADMISSION':'1'}):self.execute('scenario_state.py')
        migrated=json.loads((self.root/'data/scenario_states/GOLD_pro.json').read_text('utf-8'))
        self.assertEqual(migrated['migration']['historical_availability'],'UNKNOWN')
        self.assertEqual(migrated['primary_scenario']['direction'],s['primary_scenario']['direction'])
        # Restore the exact notification-associated snapshot for transport tests.
        self.write('data/scenario_states/GOLD_pro.json',s)
        with patch.dict(os.environ,{'MARKET_RADAR_ADMISSION':'1',t.TOKEN_ENV:'TEST_NOT_A_CREDENTIAL',t.CHAT_ID_ENV:'TEST_RECIPIENT'}),patch('time_ingestion.refreshed_delivery_window',return_value=(True,'TEST_CLOCK')),patch.object(sys,'argv',['telegram_notifier.py']),patch.object(t,'send_telegram',return_value={'message_id':1}) as send,redirect_stdout(io.StringIO()):
            self.assertEqual(t.main(),0)
            self.assertEqual(t.main(),0)
            self.assertEqual(send.call_count,1)

    def test_legacy_state_is_rejected_without_overwrite(self):
        self.write('data/states/GOLD_pro.json',{'time_basis':'SOURCE_ORDER_ONLY'})
        self.write('data/ranges/GOLD_pro.json',{})
        self.write('data/scenario_states/GOLD_pro.json',{'primary_scenario':{'direction':'bearish'}})
        original=(self.root/'data/scenario_states/GOLD_pro.json').read_bytes()
        with patch.dict(os.environ,{'MARKET_RADAR_ADMISSION':'1'}):
            with self.assertRaisesRegex(ValueError,'MIGRATION'):self.execute('scenario_state.py')
        self.assertEqual(original,(self.root/'data/scenario_states/GOLD_pro.json').read_bytes())

    def test_temporal_consumers_defer_without_native_import_or_cache(self):
        import rates_context_engine,oil_context_engine,live_reaction_engine
        with patch.dict(os.environ,{'MARKET_RADAR_ADMISSION':'1'}):
            for mod in (rates_context_engine,oil_context_engine):
                with patch.object(mod,'cached_context',side_effect=AssertionError('legacy cache')):
                    self.assertEqual(mod.collect_context(Fixed.now())['status'],'DEFERRED')
            self.assertEqual(live_reaction_engine.build_payload({},Fixed.now())['status'],'DEFERRED')

    def test_current_system_clock_refresh_never_extends_broker_mapping(self):
        from time_ingestion import refresh_delivery_clock
        samples=fixture()['external_after']
        with patch('datetime.datetime',Fixed):
            refresh_delivery_clock(self.root,lambda:samples)
        self.assertTrue(refreshed_delivery_window(self.root,'2026-10-27T14:00:06Z')[0])
        self.assertFalse(refreshed_delivery_window(self.root,'2026-10-27T14:02:06Z')[0])
        self.assertFalse((self.root/'data/time_authority/observations.sqlite').exists())

    def test_changed_clock_receipt_rejected(self):
        from time_ingestion import refresh_delivery_clock
        with patch('datetime.datetime',Fixed):refresh_delivery_clock(self.root,lambda:fixture()['external_after'])
        folder=self.root/'data/time_authority/delivery'
        key=json.loads((folder/'latest.json').read_text())['sha256']
        p=folder/(key+'.json');p.write_text('{}')
        self.assertFalse(refreshed_delivery_window(self.root,'2026-10-27T14:00:06Z')[0])

    def test_auxiliary_receipt_preserves_raw_and_excludes_current(self):
        from types import SimpleNamespace
        rows=np.array([(100,10.,12.,9.,11.,100),(200,11.,13.,10.,12.,110),(300,12.,14.,11.,13.,120)],
                      dtype=[('time','i8'),('open','f8'),('high','f8'),('low','f8'),('close','f8'),('tick_volume','i8')])
        class MT5:
            n=0
            def symbol_info_tick(self,s):
                self.n+=1;return SimpleNamespace(time=301,time_msc=301000+self.n)
            def copy_rates_from_pos(self,*args):return rows
        authority={'expires_at_utc':'2026-10-27T14:01:00Z','hi':0}
        with patch('datetime.datetime',Fixed),patch('time.sleep'),patch('time_ingestion.verified_utc_view',return_value={'current_clock_bounds':authority,'capture_id':'clock','capture_sha256':'a'*64}):
            admitted,receipt=capture_source_rows(MT5(),'EURUSD.pro',1,'H1',3,self.root)
        self.assertEqual([r['time'] for r in admitted],[100,200])
        self.assertNotIn('utc_time',admitted[0])
        self.assertEqual(receipt['symbol'],'EURUSD.pro')
        body=json.loads((self.root/receipt['receipt']).read_text())
        from mt5_clock_authority import digest
        self.assertEqual(digest(body),receipt['receipt_sha256'])
        self.assertEqual(body['historical_availability'],'UNKNOWN')

    def test_failed_snapshot_never_reuses_legacy_cache_after_admission(self):
        self.write('data/admission/latest.json',{'status':'READY'})
        with patch.object(run_radar,'run_step',return_value=(False,'MARKET_CLOSED_CONFIRMED')) as step,patch.object(run_radar,'complete_cache_available',return_value=True):
            self.assertFalse(run_radar.run_pipeline(self.root,False,no_telegram=True))
        self.assertEqual(step.call_count,1)

    def test_auxiliary_correction_or_frozen_ticks_cannot_confirm(self):
        from types import SimpleNamespace
        rows=np.array([(100,10.,12.,9.,11.,100),(200,11.,13.,10.,12.,110),(300,12.,14.,11.,13.,120)],
                      dtype=[('time','i8'),('open','f8'),('high','f8'),('low','f8'),('close','f8'),('tick_volume','i8')])
        class MT5:
            n=0
            def __init__(self,frozen=False):self.frozen=frozen
            def symbol_info_tick(self,s):
                self.n+=1;return SimpleNamespace(time=301,time_msc=301000+(0 if self.frozen else self.n))
            def copy_rates_from_pos(self,*args):
                r=rows.copy()
                if self.n==3:r[0]['close']=10.5
                return r
        authority={'expires_at_utc':'2026-10-27T14:01:00Z','hi':0}
        with patch('datetime.datetime',Fixed),patch('time.sleep'),patch('time_ingestion.verified_utc_view',return_value={'current_clock_bounds':authority,'capture_id':'clock','capture_sha256':'a'*64}):
            with self.assertRaisesRegex(ValueError,'STABLE_CLOSED'):capture_source_rows(MT5(),'EURUSD.pro',1,'H1',3,self.root)
            with self.assertRaisesRegex(ValueError,'FRESH_TICKS'):capture_source_rows(MT5(True),'EURUSD.pro',1,'H1',3,self.root)
        self.assertFalse((self.root/'data/receipts/auxiliary').exists())

    def test_notification_boundary_retains_unknown_attempt_and_deduplicates(self):
        import telegram_notifier as t
        from mt5_clock_authority import digest
        s=json.loads((SOURCE/'verification_inputs/recorded_data/scenario_states/GOLD_pro.json').read_text('utf-8-sig'))
        self.write('data/scenario_states/GOLD_pro.json',s)
        self.write('data/admission/latest.json',{'status':'READY','capture_id':'test','transport_authorized':True})
        self.write('data/notifications/GOLD_pro.json',{'should_send':True,'message':'SYNTHETIC TEST ONLY',
                   'scenario_sha256':digest(s),'capture_id':'test'})
        env={'MARKET_RADAR_ADMISSION':'1',t.TOKEN_ENV:'TEST_NOT_A_CREDENTIAL',t.CHAT_ID_ENV:'TEST_RECIPIENT'}
        with patch.dict(os.environ,env),patch('time_ingestion.refreshed_delivery_window',return_value=(True,'TEST_CLOCK')),patch.object(sys,'argv',['telegram_notifier.py']),patch.object(t,'send_telegram',side_effect=TimeoutError) as send,redirect_stdout(io.StringIO()):
            self.assertEqual(t.main(),1)
            self.assertEqual(t.main(),0)
            self.assertEqual(send.call_count,1)
        self.assertIn('pending_digest',json.loads((self.root/t.STATE_FILE).read_text()))

    def test_notification_lineage_mismatch_cannot_reach_transport(self):
        import telegram_notifier as t
        self.write('data/scenario_states/GOLD_pro.json',{'primary_scenario':None,'publication_state':'silent'})
        self.write('data/admission/latest.json',{'status':'READY','capture_id':'test','transport_authorized':True})
        self.write('data/notifications/GOLD_pro.json',{'should_send':True,'message':'SYNTHETIC','scenario_sha256':'wrong','capture_id':'test'})
        with patch.dict(os.environ,{'MARKET_RADAR_ADMISSION':'1'}),patch.object(sys,'argv',['telegram_notifier.py']),patch.object(t,'send_telegram') as send:
            self.assertEqual(t.main(),1)
            send.assert_not_called()

if __name__=='__main__':unittest.main()
