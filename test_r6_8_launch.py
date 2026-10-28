import unittest,tempfile,json,sys,time,subprocess,_winapi
from pathlib import Path
from r6_8_launcher import launch,LOCK
from r6_8_job import run_owned,clean_env
from offline_integration import single_instance
from recorded_authority import Rejected
ROOT=Path(__file__).resolve().parent

def alive(pid):
    try:h=_winapi.OpenProcess(0x100000,False,pid)
    except OSError:return False
    try:return _winapi.WaitForSingleObject(h,0)==258
    finally:_winapi.CloseHandle(h)

class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def test_mock_success_shutdown_and_receipts(self):
        r=launch('mock',self.root/'out');self.assertEqual(r['exit_code'],0)
        self.assertEqual(r['remaining_owned_processes'],0)
        worker=json.loads((self.root/'out/worker.json').read_text())
        self.assertEqual(worker['calls'][-1][0],'shutdown')
        self.assertEqual(set(x[0] for x in worker['calls']),{'initialize','shutdown','symbol_info','copy_rates_from_pos'})
        self.assertTrue(all(x[3]==1 for x in worker['calls'] if x[0]=='copy_rates_from_pos'))
        report=json.loads((self.root/'out/pipeline/R6_REPORT.json').read_text(encoding='utf-8'))
        self.assertFalse(report['delivery_permission']);self.assertFalse(report['trade_permission'])
        self.assertEqual(report['replay']['events'][0]['eligibility']['status'],'DEFERRED')
        capture=json.loads((self.root/'out/pipeline/capture.json').read_text())
        for receipt in capture['captures'][0]['series'].values():
            self.assertEqual(receipt['availability'],'UNKNOWN');self.assertTrue(receipt['raw_sha256'])
    def test_connection_failure_no_retry(self):
        r=launch('mock',self.root/'out',mock_case='connect_failure');self.assertEqual(r['exit_code'],10)
        w=json.loads((self.root/'out/worker.json').read_text());self.assertEqual(w['calls'],[['initialize'],['shutdown']])
        self.assertEqual(r['automatic_restarts'],0)
    def test_forbidden_api(self):
        r=launch('mock',self.root/'out',mock_case='forbidden_api');self.assertEqual(r['exit_code'],20)
        w=json.loads((self.root/'out/worker.json').read_text());self.assertEqual(w['calls'],[])
    def test_missing_and_short(self):
        for mode in ('missing','short'):
            r=launch('mock',self.root/mode,mock_case=mode);self.assertEqual(r['exit_code'],20)
            w=json.loads((self.root/mode/'worker.json').read_text());self.assertEqual(w['calls'][-1],['shutdown'])
    def test_crash_releases_lock_without_restart(self):
        r=launch('mock',self.root/'out',mock_case='crash');self.assertEqual(r['exit_code'],70)
        self.assertEqual(r['automatic_restarts'],0)
        with single_instance(LOCK):pass
    def test_duplicate_session(self):
        with single_instance(LOCK):
            with self.assertRaises(RuntimeError):launch('mock',self.root/'out')
        self.assertFalse((self.root/'out').exists())
    def test_native_is_blocked_before_child(self):
        r=launch('native',self.root/'out');self.assertEqual(r['exit_code'],20);self.assertFalse(r['child_started'])
        self.assertNotIn('MetaTrader5',sys.modules)
    def test_no_fallback_and_time_limits(self):
        for backend in ('auto','',None):
            with self.assertRaises(ValueError):launch(backend,self.root/'out')
        for limit in (0,-1,61,float('nan'),True):
            with self.assertRaises(ValueError):launch('mock',self.root/'out',seconds=limit)
    def test_production_output_denied(self):
        with self.assertRaises(Rejected):launch('mock','C:/Users/User/MarketRadar')
    def test_build_and_nonempty_output_denied(self):
        with self.assertRaises(ValueError):launch('mock',ROOT/'output')
        (self.root/'existing').write_text('retain')
        with self.assertRaises(ValueError):launch('mock',self.root)
    def test_clean_environment(self):
        self.assertTrue(set(k.upper() for k in clean_env())<={'SYSTEMROOT','WINDIR','TEMP','TMP','PATH','COMSPEC','PATHEXT'})
    def test_owned_tree_timeout_keeps_unrelated_process(self):
        sentinel=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(120)'],env=clean_env(),creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            r=run_owned([sys.executable,'-I',str(ROOT/'r6_8_process_fixture.py'),'tree',str(self.root)],cwd=ROOT,seconds=3)
            self.assertEqual(r['exit_code'],124);self.assertEqual(r['remaining_owned_processes'],0)
            self.assertGreaterEqual(r['owned_total_processes'],2)
            self.assertTrue((self.root/'descendant.pid').exists())
            self.assertFalse(alive(int((self.root/'descendant.pid').read_text())))
            self.assertIsNone(sentinel.poll());self.assertLess(r['elapsed_seconds'],8)
        finally:sentinel.kill();sentinel.wait()
    def test_orphan_descendant_cleaned_on_success(self):
        r=run_owned([sys.executable,'-I',str(ROOT/'r6_8_process_fixture.py'),'orphan',str(self.root)],cwd=ROOT,seconds=10)
        self.assertEqual(r['exit_code'],0);self.assertEqual(r['remaining_owned_processes'],0)
        self.assertFalse(alive(int((self.root/'descendant.pid').read_text())))
    def test_supervisor_crash_kills_owned_tree(self):
        controller=subprocess.Popen([sys.executable,'-I',str(ROOT/'r6_8_process_fixture.py'),'controller',str(self.root)],env=clean_env(),creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline=time.monotonic()+10
            while not (self.root/'descendant.pid').exists() and time.monotonic()<deadline:time.sleep(.05)
            self.assertTrue((self.root/'descendant.pid').exists())
            pid=int((self.root/'descendant.pid').read_text());self.assertTrue(alive(pid))
            controller.kill();controller.wait()
            deadline=time.monotonic()+5
            while alive(pid) and time.monotonic()<deadline:time.sleep(.05)
            self.assertFalse(alive(pid))
        finally:
            if controller.poll() is None:controller.kill();controller.wait()
