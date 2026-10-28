import tempfile, unittest
from pathlib import Path
from copy import deepcopy
from recovery_fixtures import case,add_observation,raw
from recorded_ledger import RecordedLedger
from recorded_authority import Rejected

class RecoveryB(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'recorded.db'
    def make(self,e,a,c):
        l=RecordedLedger(self.path,context=c,dataset_id=e['dataset_id']);self.addCleanup(l.close);return l
    def test_no_future(self):
        e,a,c=case();l=self.make(e,a,c);l.ingest('one',raw(e),a)
        self.assertEqual(l.select('test:source','test:item','2026-10-20T10:00:03Z')['reason'],'FUTURE')
        self.assertEqual(l.select('test:source','test:item','2026-10-20T10:00:04Z')['status'],'VERIFIED')
    def test_duplicate_restart(self):
        e,a,c=case();l=self.make(e,a,c);l.ingest('one',raw(e),a);l.ingest('one',raw(e),a)
        self.assertEqual(len(l.events()),1)
        other=RecordedLedger(self.path,context=c,dataset_id=e['dataset_id']);self.addCleanup(other.close)
        other.ingest('one',raw(e),a);self.assertEqual(other.events(),l.events())
    def test_crash_rollback(self):
        e,a,c=case();l=self.make(e,a,c)
        with self.assertRaises(RuntimeError):l.ingest('one',raw(e),a,fail_before_commit=True)
        self.assertEqual(l.events(),[]);self.assertEqual(l.observations(),{})
        l.ingest('one',raw(e),a);self.assertEqual(len(l.events()),1)
    def test_both_correction_orders(self):
        e,a,c=case();c=add_observation(e,a,c,'test:o2','2026-10-20T10:00:10Z',predecessor='test:o1')
        l=self.make(e,a,c);l.ingest('one',raw(e),a);e['observations'].reverse();l.ingest('one',raw(e),a)
        self.assertEqual(l.select('test:source','test:item','2026-10-20T10:00:04Z')['selected']['observation_id'],'test:o1')
        self.assertEqual(l.select('test:source','test:item','2026-10-20T10:00:10Z')['selected']['observation_id'],'test:o2')
    def test_unknown_replacement_blocks_old(self):
        e,a,c=case();c=add_observation(e,a,c,'test:o2','2026-10-20T10:00:10Z',predecessor='test:o1')
        e['observations'][1]['available_at_utc']=deepcopy(e['observations'][0]['scenario_established_at_utc'])
        l=self.make(e,a,c);l.ingest('one',raw(e),a)
        self.assertEqual(l.select('test:source','test:item','2026-10-20T10:00:20Z')['reason'],'HEAD_UNKNOWN')
    def test_conflict_no_mutation(self):
        e,a,c=case();l=self.make(e,a,c);l.ingest('one',raw(e),a);before=l.events()
        e['observations'][0]['logical_item_id']='test:changed'
        with self.assertRaises(Rejected): l.ingest('two',raw(e),a)
        self.assertEqual(before,l.events())
    def test_orphan_correction(self):
        e,a,c=case();e['observations'][0]['supersedes']='missing';l=self.make(e,a,c)
        with self.assertRaises(Rejected):l.ingest('one',raw(e),a)
        self.assertEqual(l.events(),[])
    def test_corrupted_blob(self):
        e,a,c=case();l=self.make(e,a,c);l.ingest('one',raw(e),a)
        l.db.execute("UPDATE blobs SET data=X'00'")
        with self.assertRaises(Rejected): l.select('test:source','test:item',e['historical_cutoff_utc'])
    def test_owner_mismatch(self):
        e,a,c=case();l=self.make(e,a,c)
        with self.assertRaises(Rejected):RecordedLedger(self.path,context=c,dataset_id='test:other')
