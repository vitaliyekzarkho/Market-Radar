import unittest,tempfile,json,sqlite3,subprocess,sys
from pathlib import Path
from dataclasses import replace
from copy import deepcopy
from contextlib import closing
from r6_adapter import ReadOnlyAdapter,XAUUSD,GER40,EURGBP,ALLOWLIST
from r6_pipeline import acquire_and_replay,validate_clock,csv_bytes
from r6_fixtures import FakeMT5,Clock
from recorded_authority import Rejected,sha
from recorded_context import canonical
from r5_input import normalize_csv
from offline_integration import single_instance

class ReaderTests(unittest.TestCase):
    def test_exact_allowlist_and_no_trade_attributes(self):
        api=FakeMT5();reader=ReadOnlyAdapter(api)
        for name in ('order_send','order_check','login','account_info','symbol_select','positions_get','call'):
            self.assertFalse(hasattr(reader,name))
        with reader:rows=reader.read(XAUUSD,'D1',40)
        self.assertEqual(len(rows),40)
        self.assertEqual({x[0] for x in api.calls},set(ALLOWLIST))
        self.assertEqual(api.calls[-2][3],1)
    def test_no_read_before_connect(self):
        with self.assertRaises(Rejected):ReadOnlyAdapter(FakeMT5()).read(XAUUSD,'D1')
    def test_connection_failure_shutdown(self):
        api=FakeMT5('connect')
        with self.assertRaisesRegex(Rejected,'CONNECTION_FAILED'):
            with ReadOnlyAdapter(api):pass
        self.assertEqual(api.calls,[('initialize',),('shutdown',)])
    def test_empty_and_missing_symbol(self):
        for failure in ('empty','symbol'):
            api=FakeMT5(failure)
            with self.assertRaises(Rejected):
                with ReadOnlyAdapter(api) as r:r.read(XAUUSD,'D1')
            self.assertEqual(api.calls[-1],('shutdown',))
    def test_provider_errors_redacted(self):
        with ReadOnlyAdapter(FakeMT5('exception')) as r:
            with self.assertRaisesRegex(Rejected,'^READ_FAILED$'):r.read(XAUUSD,'D1')
    def test_disabled_templates(self):
        for p in (GER40,EURGBP):
            with self.assertRaisesRegex(Rejected,'PROFILE_DISABLED'):p.validate()
    def test_profile_validation(self):
        for changes in ({'symbol_id':'../bad'},{'timeframes':('M1',)},{'timeframes':('D1','D1')},{'point_size':0},{'timezone':'LOCAL'}):
            with self.assertRaises(Rejected):replace(XAUUSD,**changes).validate()
    def test_scale_mismatch(self):
        with ReadOnlyAdapter(FakeMT5()) as r:
            with self.assertRaises(Rejected):r.read(replace(XAUUSD,digits=3),'D1')
    def test_unconfigured_timeframe(self):
        with ReadOnlyAdapter(FakeMT5()) as r:
            with self.assertRaises(Rejected):r.read(XAUUSD,'M1')
    def test_bad_count(self):
        with ReadOnlyAdapter(FakeMT5()) as r:
            for n in (0,True,2001):
                with self.assertRaises(Rejected):r.read(XAUUSD,'D1',n)
    def test_bad_payload(self):
        for field,value in (('open',float('nan')),('tick_volume',-1),('time',0)):
            api=FakeMT5();original=api.copy_rates_from_pos
            def corrupt(*args):
                rows=original(*args);rows[0][field]=value;return rows
            api.copy_rates_from_pos=corrupt
            with ReadOnlyAdapter(api) as r:
                with self.assertRaises(Rejected):r.read(XAUUSD,'D1')
    def test_reordered_bars(self):
        api=FakeMT5();original=api.copy_rates_from_pos
        api.copy_rates_from_pos=lambda *args:list(reversed(original(*args)))
        with ReadOnlyAdapter(api) as r:
            with self.assertRaises(Rejected):r.read(XAUUSD,'D1')

class ClockTests(unittest.TestCase):
    def test_unknown_uncertainty_accepted_without_verification(self):validate_clock(Clock()())
    def test_high_uncertainty_preserved(self):
        c=Clock()();c['uncertainty_seconds']=86401;validate_clock(c)
        self.assertEqual(c['uncertainty_seconds'],86401)
    def test_bad_uncertainty(self):
        for value in (-1,float('nan'),True):
            c=Clock()();c['uncertainty_seconds']=value
            with self.assertRaises(Rejected):validate_clock(c)
    def test_rollback(self):
        clock=Clock();a=clock();b=clock()
        with self.assertRaisesRegex(Rejected,'CLOCK_REGRESSED'):validate_clock(a,b)
    def test_timestamp_precision(self):
        a=Clock()();a['at_utc']='2026-10-20T12:00:00Z';b=Clock()();validate_clock(b,a)
    def test_future_bar_excluded(self):
        api=FakeMT5()
        with ReadOnlyAdapter(api) as r:rows=r.read(XAUUSD,'D1',40)
        b=csv_bytes(rows);view=normalize_csv(b,symbol='XAUUSD',timeframe='D1',cutoff='2026-10-19T12:00:00Z')
        self.assertEqual(view['excluded_open_or_future'],1)

class SafetySourceTests(unittest.TestCase):
    def test_no_native_import_or_transport_in_new_runtime(self):
        import ast
        root=Path(__file__).resolve().parent
        for name in ('r6_adapter.py','r6_pipeline.py','r6_offline.py'):
            tree=ast.parse((root/name).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):
                    self.assertFalse({x.name for x in node.names}&{'MetaTrader5','telegram_notifier','mt5_reader','run_radar'})
                if isinstance(node,ast.Attribute):self.assertNotIn(node.attr,('order_send','order_check','login','positions_get','orders_get','send_message'))

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def run_capture(self,**kwargs):
        return acquire_and_replay(kwargs.pop('api',FakeMT5()),kwargs.pop('profile',XAUUSD),
            kwargs.pop('output',self.root/'out'),session_id='test_run',count=40,clock=Clock(),**kwargs)
    def test_end_to_end_scenario_and_no_delivery(self):
        api=FakeMT5();report=self.run_capture(api=api)
        self.assertFalse(report['trade_permission']);self.assertFalse(report['delivery_permission'])
        event=report['replay']['events'][0]
        self.assertEqual(event['analysis']['status'],'ANALYZED_RETROSPECTIVELY')
        self.assertIsNotNone(event['recorded_scenario'])
        self.assertEqual(event['eligibility']['status'],'DEFERRED')
        self.assertEqual(event['transport_actions'],0)
        self.assertFalse(event['historical_notification_generated'])
        self.assertTrue(all(s=='UNKNOWN_OR_SUPERSEDED' for s in event['source_selection'].values()))
        self.assertTrue(set(x[0] for x in api.calls)<=set(ALLOWLIST))
    def test_receipts_hashes_and_dependency_chain(self):
        self.run_capture();out=self.root/'out';packet=json.loads((out/'capture.json').read_bytes())
        env=json.loads((out/'input/0_envelope.json').read_bytes())
        self.assertEqual(env['receipts'],[])
        for tf,s in packet['captures'][0]['series'].items():
            self.assertEqual(s['raw_sha256'],sha(canonical(s['raw']).encode()))
            self.assertEqual(s['availability'],'UNKNOWN')
            self.assertIsNone(s['received']['uncertainty_seconds'])
        for o in env['observations']:
            self.assertEqual(o['available_at_utc']['state'],'UNKNOWN')
            if o['kind']=='feature':self.assertTrue(o['derivation']['required_input_ids'][0].endswith(':raw'))
        with closing(sqlite3.connect(out/'capture.db')) as db:
            events=[json.loads(r[0]) for r in db.execute('select body from events')]
        self.assertEqual(sum(e.get('kind')=='CAPTURE_RECORDED' for e in events),1)
    def test_restart_without_acquisition(self):
        first=self.run_capture();api=FakeMT5('connect');second=self.run_capture(api=api)
        self.assertEqual(first,second);self.assertEqual(api.calls,[])
    def test_crash_after_capture_and_recorder(self):
        for phase in ('capture','recorder'):
            out=self.root/phase
            with self.assertRaises(RuntimeError):self.run_capture(output=out,fail_at=phase)
            api=FakeMT5('connect');r=self.run_capture(output=out,api=api)
            self.assertEqual(api.calls,[]);self.assertEqual(len(r['replay']['events']),1)
    def test_two_observations(self):
        r=self.run_capture(snapshots=2)
        self.assertEqual(len(r['replay']['events']),2)
        self.assertNotEqual(*[e['event_id'] for e in r['replay']['events']])
    def test_instrument_isolation(self):
        a=self.run_capture();p=replace(XAUUSD,symbol_id='TEST_METAL',broker_symbol='TEST_METAL')
        b=self.run_capture(profile=p,output=self.root/'other')
        self.assertNotEqual(a['replay']['events'][0]['event_id'],b['replay']['events'][0]['event_id'])
        with self.assertRaises(Rejected):self.run_capture(profile=p)
    def test_tamper_rejected(self):
        with self.assertRaises(RuntimeError):self.run_capture(fail_at='capture')
        p=self.root/'out/capture.json';d=json.loads(p.read_bytes());d['captures'][0]['cutoff']='2027-01-01T00:00:00Z';p.write_text(canonical(d))
        with self.assertRaisesRegex(Rejected,'CAPTURE_INTEGRITY_FAILED'):self.run_capture()
    def test_no_completed_packet_on_error(self):
        with self.assertRaises(Rejected):self.run_capture(api=FakeMT5('empty'))
        self.assertFalse((self.root/'out/capture.json').exists())
    def test_bounds_before_connection(self):
        api=FakeMT5()
        with self.assertRaises(Rejected):self.run_capture(api=api,snapshots=4)
        self.assertEqual(api.calls,[])
    def test_second_instance_rejected(self):
        out=self.root/'out';out.mkdir()
        with single_instance(out/'r6.lock'):
            with self.assertRaisesRegex(RuntimeError,'ALREADY_RUNNING'):self.run_capture()
    def test_production_path_rejected_before_connection(self):
        api=FakeMT5()
        with self.assertRaises(Rejected):self.run_capture(output=Path('C:/Users/User/MarketRadar'),api=api)
        self.assertEqual(api.calls,[])
