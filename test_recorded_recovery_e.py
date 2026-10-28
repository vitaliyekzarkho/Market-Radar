import tempfile,unittest,json
from pathlib import Path
from copy import deepcopy
from recovery_fixtures import case,raw
from recorded_authority import Rejected
from recorded_ledger import RecordedLedger
from evidence_recorder import EvidenceRecorder

class RecoveryE(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.inputs=self.root/'inputs';self.inputs.mkdir()
        self.e,self.a,self.c=case()
    def setup_recorder(self):
        (self.inputs/'envelope.json').write_bytes(raw(self.e));self.paths={}
        for n,(ref,b) in enumerate(self.a.items()):
            p=self.inputs/f'{n}.bin';p.write_bytes(b);self.paths[ref]=p
        self.l=RecordedLedger(self.root/'recorded.db',context=self.c,dataset_id=self.e['dataset_id']);self.addCleanup(self.l.close)
        return EvidenceRecorder(self.l,input_root=self.inputs)
    def receive(self,r,**extra):
        params=dict(envelope_path=self.inputs/'envelope.json',artifact_paths=self.paths,
                    received_at_utc='2026-10-20T10:00:02Z',clock_source='TEST CLOCK; NOT CAPTURED TIME')
        params.update(extra);return r.receive('one',**params)
    def test_original_payload_and_links(self):
        r=self.setup_recorder();e=self.receive(r)
        self.assertEqual(self.l.payload('test:o1'),self.a['test:file'])
        self.assertEqual(e['request']['authority_class'],'TEST_AUTHORITY')
        self.assertFalse(e['availability_inferred_from_local_write']);self.assertEqual(e['local_clock_authority'],'UNVERIFIED')
    def test_existing_file_missing_receipt_unknown(self):
        del self.a['test:ready'];r=self.setup_recorder();e=self.receive(r)
        self.assertEqual(e['assessment']['observations'][0]['authority']['available_at_utc'],'UNVERIFIED')
        self.assertEqual(self.l.select('test:source','test:item','2026-10-21T00:00:00Z')['status'],'UNKNOWN')
    def test_duplicate_and_restart(self):
        r=self.setup_recorder();first=self.receive(r);n=len(self.l.events())
        other=RecordedLedger(self.l.path,context=self.c,dataset_id=self.e['dataset_id']);self.addCleanup(other.close)
        self.assertEqual(first,self.receive(EvidenceRecorder(other,input_root=self.inputs)));self.assertEqual(len(other.events()),n)
    def test_corrupt_receipt_no_write(self):
        self.a['test:ready']=b'broken';r=self.setup_recorder()
        with self.assertRaises(Rejected):self.receive(r)
        self.assertEqual(self.l.events(),[])
    def test_before_commit_rollback(self):
        r=self.setup_recorder()
        with self.assertRaises(RuntimeError):self.receive(r,fail_before_commit=True)
        self.assertEqual(self.l.events(),[]);self.receive(r)
    def test_after_commit_resume(self):
        r=self.setup_recorder()
        with self.assertRaises(RuntimeError):self.receive(r,fail_after_evidence=True)
        self.assertEqual([e['kind'] for e in self.l.events()],['INGEST'])
        self.receive(r);self.assertEqual([e['kind'] for e in self.l.events()],['INGEST','CAPTURE_RECORDED'])
    def test_changed_source_file_conflict(self):
        r=self.setup_recorder();self.receive(r);self.paths['test:file'].write_bytes(b'changed')
        with self.assertRaises(Rejected):self.receive(r)
    def test_receive_time_conflict(self):
        r=self.setup_recorder()
        with self.assertRaises(Rejected):self.receive(r,received_at_utc='2026-10-20T11:00:00Z')
    def test_path_outside_root(self):
        r=self.setup_recorder();p=self.root/'outside.bin';p.write_bytes(b'not read');self.paths['test:file']=p
        with self.assertRaises(Rejected):self.receive(r)
    def test_two_writers_fail_closed(self):
        import sqlite3
        r=self.setup_recorder();other=RecordedLedger(self.l.path,context=self.c,dataset_id=self.e['dataset_id']);self.addCleanup(other.close)
        with other.transaction():
            with self.assertRaises(sqlite3.OperationalError):self.receive(r)
        self.assertEqual(self.l.events(),[])
