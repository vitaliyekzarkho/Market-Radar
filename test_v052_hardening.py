import unittest,tempfile,subprocess,sys,json,hashlib,os
from pathlib import Path
from decision_journal import DecisionJournal
from legacy_instance_lock import LegacyLock

def event(n,day='2026-10-25'):
 return {'audit_key':str(n),'at_utc':day+'T12:00:00Z','body':'x'*700}
class JournalTests(unittest.TestCase):
 def test_rotation_restart_no_duplicates(self):
  with tempfile.TemporaryDirectory() as d:
   events=[event(n) for n in range(6)];a=DecisionJournal(d,1024).sync(events)
   self.assertGreater(a['segments'],1);self.assertEqual(DecisionJournal(d,1024).sync(events)['added'],0)
   self.assertEqual(list(DecisionJournal(d).scan()[0]),[str(n) for n in range(6)])
 def test_date_rotation(self):
  with tempfile.TemporaryDirectory() as d:
   r=DecisionJournal(d).sync([event(1),event(2,'2026-10-26')]);self.assertEqual(r['segments'],2)
 def test_partial_last_record_preserved_and_replayed(self):
  with tempfile.TemporaryDirectory() as d:
   j=DecisionJournal(d);j.sync([event(1)]);p=next(Path(d).glob('*.jsonl'))
   with p.open('ab') as f:f.write(b'{"key":')
   torn=p.read_bytes();r=j.sync([event(1),event(2)])
   self.assertEqual(p.read_bytes(),torn);self.assertEqual(r['records'],2);self.assertEqual(r['segments'],2)
   self.assertEqual(j.sync([event(1),event(2)])['added'],0)
 def test_partial_header_preserved(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'segment-000001.jsonl';p.write_bytes(b'{')
   self.assertEqual(DecisionJournal(d).sync([event(1)])['records'],1);self.assertEqual(p.read_bytes(),b'{')
 def test_tampering_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   j=DecisionJournal(d);j.sync([event(1)]);p=next(Path(d).glob('*.jsonl'));p.write_bytes(p.read_bytes().replace(b'xxxxxxxx',b'yyyyyyyy'))
   with self.assertRaises(ValueError):j.sync([event(1)])
 def test_missing_segment_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   j=DecisionJournal(d,1024);j.sync([event(1),event(2)]);p=Path(d)/'segment-000001.jsonl';p.rename(Path(d)/'kept-first.backup')
   with self.assertRaises(ValueError):j.scan()
 def test_changed_source_id_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   j=DecisionJournal(d);j.sync([event(1)])
   with self.assertRaises(ValueError):j.sync([{**event(1),'body':'different'}])
class ObserverRecoveryTests(unittest.TestCase):
 def test_committed_stage_recovers_projection_after_failure(self):
  from unittest.mock import patch
  from cdr_operational import observe
  import sqlite3
  source=Path(__file__).parent/'verification_inputs/recorded_data/scenario_states/GOLD_pro.json'
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);p=root/'data/scenario_states/GOLD_pro.json';p.parent.mkdir(parents=True);p.write_bytes(source.read_bytes())
   with patch('cdr_operational.DecisionJournal.sync',side_effect=OSError('simulated projection interruption')):
    with self.assertRaises(OSError):observe(root,'scenario','recover-one')
   db=sqlite3.connect(root/'data/cdr/recorded.db');before=db.execute('SELECT COUNT(*) FROM events').fetchone()[0];db.close()
   r=observe(root,'scenario','recover-one');self.assertEqual(r['eligibility']['status'],'DEFERRED')
   db=sqlite3.connect(root/'data/cdr/recorded.db');after=db.execute('SELECT COUNT(*) FROM events').fetchone()[0];db.close()
   self.assertEqual(before,after);self.assertEqual(len(DecisionJournal(root/'data/cdr/journal').scan()[0]),1)
class StartupTests(unittest.TestCase):
 def test_combined_gate_new_vs_new_and_release(self):
  from unittest.mock import patch
  from types import SimpleNamespace
  import operational_startup_gate as g
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'data/runtime').mkdir(parents=True);(root/'data/runtime/radar.lock').write_bytes(b'0');(root/'run_radar.py').write_bytes(b'test fixture')
   with patch.object(g,'Path',return_value=root),patch.object(g,'LEGACY_ENTRY_SHA256',hashlib.sha256(b'test fixture').hexdigest()),patch.object(g.subprocess,'run',return_value=SimpleNamespace(stdout='[]')):
    with g.StartupGate():
     with self.assertRaisesRegex(RuntimeError,'RADAR_ALREADY_RUNNING'):
      with g.StartupGate():pass
    with g.StartupGate():pass
 def test_changed_legacy_contract_blocks_start(self):
  from unittest.mock import patch
  import operational_startup_gate as g
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'run_radar.py').write_bytes(b'changed')
   with patch.object(g,'Path',return_value=root):
    with self.assertRaisesRegex(RuntimeError,'LEGACY_LOCK_CONTRACT_CHANGED'):
     with g.StartupGate():pass
class DryRunTests(unittest.TestCase):
 def test_notifier_dry_run_never_sends_or_writes_marker(self):
  from unittest.mock import patch
  import telegram_notifier as t
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);n=r/'notification.json';n.write_text(json.dumps({'should_send':True,'message':'GOLD | TECHNICAL DRY RUN'}))
   with patch.object(t,'NOTIFICATION_FILE',n),patch.object(t,'STATE_FILE',r/'state.json'),patch.object(t,'DELIVERY_FILE',r/'delivery.json'),patch.object(sys,'argv',['telegram_notifier.py','--dry-run']),patch.object(t,'send_telegram',side_effect=AssertionError('SEND_FORBIDDEN')) as sender,patch.dict(os.environ,{t.TOKEN_ENV:'unit-test-placeholder',t.CHAT_ID_ENV:'unit-test-placeholder'}):
    self.assertEqual(t.main(),0);sender.assert_not_called()
   self.assertFalse((r/'state.json').exists());self.assertEqual(json.loads((r/'delivery.json').read_text())['status'],'dry_run')
class LegacyLockTests(unittest.TestCase):
 def test_readonly_lock_blocks_legacy_and_preserves_bytes(self):
  import msvcrt
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'radar.lock';p.write_bytes(b'0');before=p.read_bytes()
   with LegacyLock(p):
    with p.open('r+b') as f:
     with self.assertRaises(OSError):msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
   self.assertEqual(p.read_bytes(),before)
   with p.open('r+b') as f:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
 def test_legacy_blocks_new(self):
  import msvcrt
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'radar.lock';p.write_bytes(b'0')
   with p.open('r+b') as f:
    msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
    with self.assertRaises(OSError):
     with LegacyLock(p):pass
    msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
 def test_missing_file_fails_closed(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(OSError):
    with LegacyLock(Path(d)/'missing'):pass
 def test_child_crash_releases_native_lock(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'radar.lock';p.write_bytes(b'0')
   code="import sys,os;sys.path.insert(0,sys.argv[1]);from legacy_instance_lock import LegacyLock;lock=LegacyLock(sys.argv[2]);lock.__enter__();print('LOCKED',flush=True);sys.stdin.readline();os._exit(7)"
   child=subprocess.Popen([sys.executable,'-B','-c',code,str(Path(__file__).parent),str(p)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=0x08000000)
   try:
    self.assertEqual(child.stdout.readline().strip(),'LOCKED')
    with self.assertRaises(OSError):
     with LegacyLock(p):pass
    child.communicate('\n',timeout=10);self.assertEqual(child.returncode,7)
    with LegacyLock(p):pass
   finally:
    if child.poll() is None:child.kill();child.wait(timeout=10)
if __name__=='__main__':unittest.main()
