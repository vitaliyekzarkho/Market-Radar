import unittest,tempfile,copy,json,sqlite3
from pathlib import Path
from unittest.mock import patch
from mt5_clock_authority import ClockLedger,epoch,utc,PERIODS
from time_ingestion import persist_capture,load_lineage,journal_deferred_cycle
from decision_journal import DecisionJournal

def fixture(at='2026-10-27T14:00:00Z',offset=3600,tf='M15'):
    b=epoch(at);start=b-2;end=b+2
    def external(t):
        out=[]
        for source in ('time.cloudflare.com','time.google.com'):
            out.append(dict(source=source,method='NTP_UDP_UNAUTHENTICATED',status='OK',start_utc=utc(t),end_utc=utc(t+.01),elapsed_seconds=.01,local_clock_step_seconds=0,stratum=2,conservative_offset_interval_seconds=[-.1,.1]))
        for source in ('https://www.cloudflare.com/cdn-cgi/trace','https://www.google.com/generate_204'):
            out.append(dict(source=source,method='HTTPS_DATE_TLS',status='OK',start_utc=utc(t),end_utc=utc(t+.01),elapsed_seconds=.01,local_clock_step_seconds=0,age_header=None,offset_interval_seconds=[-.2,.2]))
        return out
    old=dict(time=int(b+offset-PERIODS[tf]),open=1,high=2,low=0,close=1,tick_volume=5,spread=1,real_volume=0)
    new=dict(old,time=int(b+offset));samples=[]
    for delta in (-1,.5,1):
        t=b+delta
        samples.append(dict(measurements=[dict(kind='tick',start_utc=utc(t),end_utc=utc(t),value=dict(time=int(t+offset),time_msc=int((t+offset)*1000))),dict(kind='bars',timeframe=tf,start_utc=utc(t),end_utc=utc(t+.001),rows=[copy.deepcopy(old)] if delta<0 else [copy.deepcopy(old),copy.deepcopy(new)])]))
    return dict(symbol='GOLD.pro',source='OANDA_MT5_LOCAL',initialize_success=True,initialize_start_utc=utc(start),shutdown_end_utc=utc(end),samples=samples,external_before=external(start-1),external_after=external(end+1))

class TimeAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.l=ClockLedger(self.root/'clock.sqlite')
    def tearDown(self):self.l.close();self.tmp.cleanup()
    def test_current_measured_offset(self):
        r=self.l.ingest('one',fixture());self.assertEqual(r['authority']['offset_seconds'],3600);self.assertEqual(r['status'],'TIME_VERIFIED')
    def test_no_global_hour_rule(self):
        for n,offset in enumerate((0,7200,-3600)):
            r=self.l.ingest(str(n),fixture(f'2026-10-2{7+n}T14:00:00Z',offset));self.assertEqual(r['authority']['offset_seconds'],offset)
    def test_expiry_blocks_new_inference_not_old_fact(self):
        self.l.ingest('one',fixture());r=self.l.lineage('one','2026-10-27T14:02:00Z')
        self.assertEqual(r['authority_state'],'EXPIRED');self.assertEqual(r['rows'][0]['closure_status'],'OBSERVED_CLOSED');self.assertFalse(r['rows'][0]['historical_confirmation_allowed'])
    def test_offset_change_new_segment(self):
        a=self.l.ingest('a',fixture());b=self.l.ingest('b',fixture('2026-10-27T14:01:00Z',7200))
        self.assertNotEqual(a['authority']['segment'],b['authority']['segment']);self.assertIn('PREVIOUS_AUTHORITY_EXPIRED_CHANGED_OR_UNVERIFIED',b['reasons'])
    def test_dst_no_retroactive_shift(self):
        a=self.l.ingest('a',fixture('2026-10-25T00:59:00Z',7200));b=self.l.ingest('b',fixture('2026-10-25T01:00:00Z',3600))
        self.assertEqual(self.l.records()[0][2],a);self.assertNotEqual(a['authority']['segment'],b['authority']['segment'])
    def test_no_fresh_ticks(self):
        c=fixture()
        for s in c['samples']:s['measurements'][0]['value']=copy.deepcopy(c['samples'][0]['measurements'][0]['value'])
        r=self.l.ingest('a',c);self.assertEqual(r['status'],'TIME_UNVERIFIED');self.assertIn('FRESH_TICKS_REQUIRED',r['reasons'])
    def test_m15_h1_closure(self):
        for n,tf in enumerate(('M15','H1')):
            r=self.l.ingest(tf,fixture(f'2026-10-2{7+n}T14:00:00Z',tf=tf));self.assertEqual(r['closures'][0]['closure_status'],'OBSERVED_CLOSED')
    def test_h4_d1_w1_can_accumulate_actual_rollover(self):
        for n,tf in enumerate(('H4','D1','W1')):
            r=self.l.ingest(tf,fixture(f'2026-10-2{7+n}T14:00:00Z',tf=tf));self.assertEqual(r['closures'][0]['timeframe'],tf);self.assertIsNone(r['closures'][0]['verified_open_utc'])
    def test_position_one_not_authority(self):
        c=fixture(tf='H4')
        for s in c['samples']:s['measurements'][1]['rows']=[dict(time=1),dict(time=2)]
        r=self.l.ingest('a',c);self.assertEqual(r['closures'],[]);self.assertTrue(all(x['time_status']=='TIME_UNVERIFIED' for x in self.l.lineage('a','2026-10-27T14:00:03Z')['rows']))
    def test_restart_idempotent(self):
        c=fixture();r=self.l.ingest('a',c);self.l.close();self.l=ClockLedger(self.root/'clock.sqlite');self.assertEqual(self.l.ingest('a',c),r);self.assertEqual(len(self.l.records()),1)
    def test_conflicting_repeat_rejected(self):
        c=fixture();self.l.ingest('a',c);c['source']='OTHER'
        with self.assertRaisesRegex(ValueError,'CONFLICT'):self.l.ingest('a',c)
        self.assertEqual(len(self.l.records()),1)
    def test_order_rejected(self):
        self.l.ingest('b',fixture('2026-10-28T14:00:00Z'))
        with self.assertRaisesRegex(ValueError,'OUT_OF_ORDER'):self.l.ingest('a',fixture())
    def test_corrupt_ledger_rejected(self):
        self.l.ingest('a',fixture());self.l.db.execute("UPDATE captures SET result='{}'");self.l.db.commit()
        with self.assertRaisesRegex(ValueError,'INTEGRITY'):self.l.records()
    def test_future_and_earlier_cutoff(self):
        self.l.ingest('a',fixture());rows=self.l.lineage('a','2026-10-27T13:59:59Z')['rows'];self.assertTrue(all(r['available_at_utc'] is None for r in rows))
        rows=self.l.lineage('a','2026-10-27T14:00:03Z')['rows'];self.assertEqual(rows[-1]['closure_status'],'CURRENT_OR_FUTURE');self.assertIsNone(rows[-1]['verified_utc_timestamp'])
    def test_missing_clock(self):
        c=fixture();c['external_before']=[];self.assertEqual(self.l.ingest('a',c)['status'],'TIME_UNVERIFIED')
    def test_clock_step(self):
        c=fixture();c['external_before'][0]['local_clock_step_seconds']=1;self.assertIn('CLOCK_LATENCY_OR_STEP',self.l.ingest('a',c)['reasons'])
    def test_unknown_source(self):
        c=fixture();c['source']='OTHER';self.assertIn('SOURCE_IDENTITY_REQUIRED',self.l.ingest('a',c)['reasons'])
    def test_revised_bar_not_certified(self):
        c=fixture();self.l.ingest('a',c);later=fixture('2026-10-27T14:01:00Z')
        for s in later['samples']:s['measurements'][1]['rows']=[dict(c['samples'][-1]['measurements'][1]['rows'][0],close=9),dict(time=1793113260)]
        self.l.ingest('b',later);r=self.l.lineage('b','2026-10-27T14:01:03Z');self.assertEqual(r['rows'][0]['time_status'],'TIME_UNVERIFIED')
    def test_time_journal_chain_and_restart(self):
        c=fixture();persist_capture(self.root,'a',c,'2026-10-27T14:00:03Z');r=journal_deferred_cycle(self.root,'cycle');journal_deferred_cycle(self.root,'cycle')
        records=DecisionJournal(self.root/'data/cdr/journal').scan()[0];self.assertEqual(len(records),4)
        self.assertEqual(r['status'],'DEFERRED')
        for e in records.values():
            self.assertIsNone(e['technical']);self.assertFalse(e['should_notify']);self.assertFalse(e['trade_authorized']);self.assertEqual(e['assessment']['status'],'DEFERRED');self.assertIn('time_lineage',e)
    def test_receipt_tamper(self):
        persist_capture(self.root,'a',fixture(),'2026-10-27T14:00:03Z');(self.root/'data/receipts/a/clock_capture.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):load_lineage(self.root)
    def test_run_pipeline_deferred_never_calls_analytics_or_transport(self):
        import run_radar
        persist_capture(self.root,'a',fixture(),'2026-10-27T14:00:03Z')
        with patch.object(run_radar,'run_step',return_value=(True,'TIME_AUTHORITY_DEFERRED: retained')) as step:
            self.assertTrue(run_radar.run_pipeline(self.root,False));self.assertEqual(step.call_count,1)
        self.assertEqual(json.loads((self.root/run_radar.HEARTBEAT_FILE).read_text())['status'],'DEFERRED')
    def test_journal_failure_not_success(self):
        import run_radar
        with patch.object(run_radar,'run_step',return_value=(True,'TIME_AUTHORITY_DEFERRED: retained')):
            self.assertFalse(run_radar.run_pipeline(self.root,False))
    def test_real_recorded_capture(self):
        c=json.loads((Path(__file__).parent/'verification_inputs/time_authority/native_clock_capture.json').read_text());c.update(symbol='GOLD.pro',source='OANDA_MT5_LOCAL')
        r=self.l.ingest('recorded',c);self.assertEqual(r['authority']['offset_seconds'],3600);self.assertEqual({v['timeframe'] for v in r['closures']},{'M15','H1'})
        lineage=self.l.lineage('recorded',c['shutdown_end_utc']);self.assertEqual(len(lineage['rows']),25)
        self.assertTrue(all(x['time_status']=='TIME_UNVERIFIED' for x in lineage['rows'] if x['timeframe'] in ('H4','D1','W1')))
    def test_actual_rollover_across_restart(self):
        c=json.loads((Path(__file__).parent/'verification_inputs/time_authority/native_clock_capture.json').read_text());c.update(symbol='GOLD.pro',source='OANDA_MT5_LOCAL')
        cut=next(i for i,s in enumerate(c['samples']) if next(m for m in s['measurements'] if m.get('timeframe')=='M15')['rows'][-1]['time']==1793113200)
        a=copy.deepcopy(c);a['samples']=a['samples'][:cut];a['shutdown_end_utc']=a['samples'][-1]['measurements'][-1]['end_utc']
        b=copy.deepcopy(c);b['samples']=b['samples'][cut:];b['initialize_start_utc']=b['samples'][0]['measurements'][0]['start_utc']
        self.assertEqual(self.l.ingest('before',a)['closures'],[]);self.l.close();self.l=ClockLedger(self.root/'clock.sqlite')
        r=self.l.ingest('after',b);self.assertEqual({v['timeframe'] for v in r['closures']},{'M15','H1'})
    def test_expired_gap_cannot_prove_rollover(self):
        a=fixture();a['samples']=a['samples'][:1]
        # Valid tick observations, but bar does not advance in the first batch.
        a=fixture()
        for s in a['samples']:s['measurements'][1]['rows']=[copy.deepcopy(a['samples'][0]['measurements'][1]['rows'][0])]
        self.l.ingest('a',a)
        b=fixture('2026-10-27T14:02:00Z')
        for s in b['samples']:s['measurements'][1]['rows']=[dict(time=1793112300),dict(time=1793113200)]
        self.assertEqual(self.l.ingest('b',b)['closures'],[])
    def test_snapshot_explicit_deferred_mode_no_raw_cache_promotion(self):
        import clock_capture_adapter,mt5_clock_probe
        import sys,types,importlib
        fake=types.ModuleType('MetaTrader5')
        for i,name in enumerate(PERIODS):setattr(fake,'TIMEFRAME_'+name,i)
        fake.shutdown=lambda:None
        with patch.dict(sys.modules,{'MetaTrader5':fake}):snapshot=importlib.import_module('save_snapshot')
        import os
        before=os.getcwd()
        try:
            os.chdir(self.root)
            with patch.object(snapshot,'connect'),patch.object(snapshot,'mt5',fake),patch('clock_capture_adapter.collect',return_value=fixture()):snapshot.main(allow_deferred=True)
        finally:os.chdir(before)
        self.assertTrue((self.root/'data/time_authority/latest.json').exists());self.assertFalse((self.root/'data/raw').exists())
    def test_persisted_view_forgery_rejected(self):
        persist_capture(self.root,'a',fixture(),'2026-10-27T14:00:03Z');p=self.root/'data/time_authority/latest.json';v=json.loads(p.read_text());v['lineage']['notification_eligible']=True;p.write_text(json.dumps(v))
        with self.assertRaisesRegex(ValueError,'VIEW_MISMATCH'):load_lineage(self.root)

if __name__=='__main__':unittest.main()
