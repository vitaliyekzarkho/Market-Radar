"""Synthetic D1 boundary tests. All files live in temporary private directories."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from offline_evidence_ledger import Ledger, LedgerError, digest


FIXTURE = Path(__file__).parent / "tests/fixtures/d1/evidence.json"


class Clock:
    def __init__(self):
        self.value = dict(at_utc="2026-10-21T10:00:00Z", session_id="synthetic:boot1",
                          monotonic=100, uncertainty_seconds=0, health_id="synthetic:healthy")

    def __call__(self):
        return deepcopy(self.value)

    def later(self):
        self.value.update(at_utc="2026-10-21T10:01:00Z", monotonic=160)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "ledger.sqlite"
        self.clock = Clock()
        self.policy = dict(classification="synthetic", policy_id="synthetic:zero-uncertainty",
                           max_uncertainty_seconds=0)
        self.meta = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.data = b"synthetic evidence v1"
        self.meta["content_hash"] = digest(self.data)
        self.ledger = self.open()

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def open(self):
        return Ledger(self.path, clock=self.clock, policy=self.policy, private_root=self.root)

    def ingest(self, key="acq1", meta=None, payload=None):
        return self.ledger.ingest(key, meta or self.meta, payload=self.data if payload is None else payload)

    def select(self, cutoff=None):
        return self.ledger.select("synthetic:publisher", "item1", cutoff or self.ledger.cutoff(self.clock()["at_utc"]))

    def correction(self, prior):
        meta = deepcopy(self.meta)
        meta.update(source_revision="2", predecessor_version_id=prior, content_hash=digest(b"corrected"))
        return self.ingest("acq2", meta, b"corrected")

    def test_store_then_ready(self):
        vid = self.ingest()
        self.assertEqual(self.select()["excluded"][0]["reason"], "AVAILABILITY_UNKNOWN_AT_CUTOFF")
        ready = self.ledger.publish(vid)
        selected = self.select()["selected"]
        self.assertEqual(selected["version"]["version_id"], vid)
        self.assertEqual(selected["ready"], ready)

    def test_identical_acquisition_no_changes(self):
        vid = self.ingest()
        before = self.ledger.events()
        self.clock.later()
        self.assertEqual(self.ingest(), vid)
        self.assertEqual(self.ledger.events(), before)

    def test_reobservation_same_version_new_receipt(self):
        vid = self.ingest()
        self.clock.later()
        m = deepcopy(self.meta)
        m["observed"]["at_utc"] = self.clock()["at_utc"]
        self.assertEqual(self.ingest("again", m), vid)
        self.assertEqual(len(self.ledger.projection()["versions"]), 1)
        self.assertEqual(len(self.ledger.projection()["receipts"]), 2)

    def test_acquisition_conflict_no_partial_events(self):
        self.ingest()
        before = self.ledger.events()
        with self.assertRaisesRegex(LedgerError, "conflict"):
            self.ingest(payload=b"different")
        self.assertEqual(before, self.ledger.events())

    def test_correction_preserves_prior(self):
        first = self.ingest()
        self.ledger.publish(first)
        second = self.correction(first)
        self.ledger.publish(second)
        self.assertEqual(self.select()["selected"]["version"]["version_id"], second)
        self.assertIn(first, self.ledger.projection()["versions"])
        self.assertIn("CORRECTED", [e["kind"] for e in self.ledger.events()])

    def test_prefix_stable_after_later_correction(self):
        first = self.ingest()
        self.ledger.publish(first)
        cutoff = self.ledger.cutoff(self.clock()["at_utc"])
        before = self.select(cutoff)
        self.clock.later()
        self.ledger.publish(self.correction(first))
        self.assertEqual(before, self.select(cutoff))

    def test_equal_timestamp_sequence_boundary(self):
        vid = self.ingest()
        cutoff = self.ledger.cutoff(self.clock()["at_utc"])
        before = self.select(cutoff)
        self.ledger.publish(vid)
        self.assertEqual(before, self.select(cutoff))
        self.assertEqual(self.select()["status"], "ELIGIBLE")

    def test_both_cutoff_constraints(self):
        vid = self.ingest()
        self.clock.later()
        self.ledger.publish(vid)
        result = self.select(self.ledger.cutoff("2026-10-21T10:00:00Z"))
        self.assertEqual(result["excluded"][0]["reason"], "AVAILABLE_AFTER_AS_OF")

    def test_future_evidence_quarantined(self):
        self.meta["observed"]["at_utc"] = "2026-10-22T00:00:00Z"
        with self.assertRaisesRegex(LedgerError, "future"):
            self.ingest()
        self.assertEqual(self.ledger.projection()["versions"], {})
        self.assertEqual(len(self.ledger.projection()["rejected"]), 1)

    def test_invalid_timestamp_inputs(self):
        for index, value in enumerate((None, "bad", "2026-10-21T09:00:00", "2026-10-21T10:00:00+01:00")):
            with self.subTest(value=value):
                self.meta["observed"]["at_utc"] = value
                with self.assertRaises(LedgerError):
                    self.ingest(str(index))

    def test_missing_publication_explicit_unknown(self):
        self.meta["published"] = dict(at_utc=None, authority="UNKNOWN", missing_reason="legacy missing")
        vid = self.ingest()
        self.ledger.publish(vid)
        self.assertIsNone(self.select()["selected"]["version"]["metadata"]["published"]["at_utc"])

    def test_publication_after_receipt(self):
        self.meta["published"]["at_utc"] = "2026-10-21T10:00:01Z"
        with self.assertRaisesRegex(LedgerError, "publication"):
            self.ingest()

    def test_invalid_observation_window(self):
        self.meta["observation_window"] = dict(start_utc="2026-10-21T10:00:00Z", end_utc="2026-10-22T00:00:00Z")
        with self.assertRaisesRegex(LedgerError, "window"):
            self.ingest()

    def test_clock_rollback_no_ready(self):
        vid = self.ingest()
        self.clock.value["at_utc"] = "2026-10-21T09:59:59Z"
        with self.assertRaisesRegex(LedgerError, "rollback"):
            self.ledger.publish(vid)
        self.assertEqual(self.ledger.projection()["ready"], {})

    def test_clock_uncertainty(self):
        vid = self.ingest()
        self.clock.value["uncertainty_seconds"] = 1
        with self.assertRaisesRegex(LedgerError, "uncertainty"):
            self.ledger.publish(vid)

    def test_session_monotonic_rollback(self):
        vid = self.ingest()
        self.clock.value["monotonic"] = 99
        with self.assertRaisesRegex(LedgerError, "monotonic"):
            self.ledger.publish(vid)

    def test_new_boot_no_global_monotonic_claim(self):
        vid = self.ingest()
        self.clock.value.update(session_id="synthetic:boot2", monotonic=0)
        self.ledger.publish(vid)
        self.assertEqual(self.select()["status"], "ELIGIBLE")

    def test_restart_stored_only_and_recovery_now(self):
        vid = self.ingest()
        cutoff = self.ledger.cutoff(self.clock()["at_utc"])
        before = self.select(cutoff)
        self.ledger.close()
        self.clock.later()
        self.ledger = self.open()
        self.assertEqual(self.select()["status"], "UNKNOWN")
        ready = self.ledger.publish(vid, recovery_id="synthetic:recovery1")
        self.assertEqual(ready["available_at_utc"], "2026-10-21T10:01:00.000000+00:00")
        self.assertEqual(before, self.select(cutoff))
        self.assertEqual(len(self.ledger.projection()["recovery"]), 1)

    def test_ready_rollback_isolation(self):
        vid = self.ingest()
        other = self.open()
        before = self.ledger.events()
        def crash():
            self.assertEqual(other.projection()["ready"], {})
            raise RuntimeError("synthetic crash")
        try:
            with self.assertRaisesRegex(RuntimeError, "crash"):
                self.ledger.publish(vid, before_commit=crash)
            self.assertEqual(before, self.ledger.events())
        finally:
            other.close()

    def test_restart_ready_and_rebuild_complete_state(self):
        vid = self.ingest()
        self.ledger.publish(vid)
        before = self.ledger.projection()
        events = self.ledger.events()
        self.ledger.close()
        self.ledger = self.open()
        self.assertEqual(events, self.ledger.events())
        self.assertEqual(before, self.ledger.projection())
        self.assertEqual(self.select()["status"], "ELIGIBLE")

    def test_concurrent_duplicate_ready(self):
        vid = self.ingest()
        def publish(_):
            ledger = self.open()
            try:
                return ledger.publish(vid)
            finally:
                ledger.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(publish, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(sum(e["kind"] == "READY" for e in self.ledger.events()), 1)

    def external(self):
        path = self.root / "payload.bin"
        path.write_bytes(self.data)
        vid = self.ledger.ingest("external", self.meta, relative_path="payload.bin")
        self.ledger.publish(vid)
        return path, vid

    def test_tampered_payload_fail_closed(self):
        path, _ = self.external()
        path.write_bytes(b"tampered")
        self.assertEqual(self.select()["reason"], "PAYLOAD_UNAVAILABLE_OR_CORRUPT")

    def test_missing_external_payload(self):
        path, _ = self.external()
        path.unlink()
        self.assertEqual(self.select()["reason"], "PAYLOAD_UNAVAILABLE_OR_CORRUPT")

    def test_hash_mismatch_at_ingestion(self):
        with self.assertRaisesRegex(LedgerError, "hash mismatch"):
            self.ingest(payload=b"wrong")

    def test_independence_remains_claim(self):
        vid = self.ingest()
        self.ledger.publish(vid)
        attestation = self.select()["selected"]["version"]["metadata"]["attestation"]
        self.assertEqual(attestation, self.meta["attestation"])
        self.assertEqual(attestation["verification_method"], "unverified_upstream_claim")

    def test_late_legacy_import_not_historical_ready(self):
        self.meta["published"]["at_utc"] = "2020-01-01T00:00:00Z"
        self.meta["legacy_import"] = True
        vid = self.ingest()
        self.ledger.publish(vid)
        result = self.select(self.ledger.cutoff("2020-01-02T00:00:00Z"))
        self.assertEqual(result["status"], "UNKNOWN")

    def test_future_market_outcome_does_not_change_prefix(self):
        vid = self.ingest()
        self.ledger.publish(vid)
        cutoff = self.ledger.cutoff(self.clock()["at_utc"])
        before = self.select(cutoff)
        self.clock.later()
        meta = deepcopy(self.meta)
        meta["item_id"] = "later-market-price"
        self.ledger.publish(self.ingest("price", meta))
        self.assertEqual(before, self.select(cutoff))

    def test_append_only_enforcement(self):
        self.ingest()
        for sql in ("DELETE FROM d1_events", "UPDATE d1_events SET kind='x'", "DELETE FROM d1_meta"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.ledger.db.execute(sql)

    def test_invalid_predecessor(self):
        self.meta["predecessor_version_id"] = "missing"
        with self.assertRaisesRegex(LedgerError, "predecessor"):
            self.ingest()

    def test_missing_revision_lineage(self):
        self.ingest()
        self.meta["source_revision"] = "2"
        with self.assertRaisesRegex(LedgerError, "predecessor"):
            self.ingest("revision")

    def test_correction_cannot_publish_before_predecessor(self):
        first = self.ingest()
        second = self.correction(first)
        with self.assertRaisesRegex(LedgerError, "predecessor"):
            self.ledger.publish(second)

    def test_unvalidated_no_ready(self):
        self.meta["validation_status"] = "unverified"
        vid = self.ingest()
        with self.assertRaisesRegex(LedgerError, "validated"):
            self.ledger.publish(vid)

    def test_private_path_containment(self):
        with self.assertRaisesRegex(LedgerError, "escapes"):
            self.ledger.ingest("escape", self.meta, relative_path="../escape.bin")

    def test_no_migration_of_other_db(self):
        path = self.root / "other.sqlite"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE production (value TEXT)")
        db.close()
        with self.assertRaises(sqlite3.DatabaseError):
            Ledger(path, clock=self.clock, policy=self.policy, private_root=self.root)
        db = sqlite3.connect(path)
        self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("production",)])
        db.close()

    def test_policy_must_be_synthetic(self):
        policy = dict(self.policy, classification="production")
        with self.assertRaisesRegex(LedgerError, "synthetic"):
            Ledger(self.root / "invalid.sqlite", clock=self.clock, policy=policy, private_root=self.root)

    def test_deterministic_complete_journal(self):
        first = self.ingest()
        self.ledger.publish(first)
        expected = self.ledger.events()
        other = Ledger(self.root / "repeat.sqlite", clock=self.clock, policy=self.policy, private_root=self.root)
        try:
            vid = other.ingest("acq1", self.meta, payload=self.data)
            other.publish(vid)
            self.assertEqual(other.events(), expected)
            self.assertEqual(other.projection(), self.ledger.projection())
        finally:
            other.close()

    def test_uncommitted_ready_not_in_other_cutoff(self):
        vid = self.ingest()
        other = self.open()
        before = other.cutoff(self.clock()["at_utc"])
        def check():
            self.assertEqual(other.select("synthetic:publisher", "item1", before)["status"], "UNKNOWN")
        try:
            self.ledger.publish(vid, before_commit=check)
            self.assertEqual(other.select("synthetic:publisher", "item1", before)["status"], "UNKNOWN")
        finally:
            other.close()

    def test_future_cutoff_rejected(self):
        with self.assertRaisesRegex(LedgerError, "future cutoff"):
            self.ledger.cutoff("2027-01-01T00:00:00Z")

    def test_uncommitted_sequence_rejected(self):
        with self.assertRaisesRegex(LedgerError, "not yet committed"):
            self.select(dict(as_of_utc=self.clock()["at_utc"], maximum_visible_ledger_sequence=999))

    def test_lineage_fork_rejected(self):
        first = self.ingest()
        self.correction(first)
        meta = dict(self.meta, source_revision="fork", predecessor_version_id=first)
        with self.assertRaisesRegex(LedgerError, "fork"):
            self.ingest("fork", meta)

    def test_corrupted_journal_detected(self):
        self.ingest()
        self.ledger.db.execute("DROP TRIGGER d1_events_UPDATE")
        self.ledger.db.execute("UPDATE d1_events SET hash='bad' WHERE seq=1")
        with self.assertRaisesRegex(LedgerError, "integrity"):
            self.select()

    def test_payload_corruption_no_older_fallback(self):
        first = self.ingest()
        self.ledger.publish(first)
        meta = dict(self.meta, source_revision="2", predecessor_version_id=first,
                    content_hash=digest(b"revision"))
        path = self.root / "revision.bin"
        path.write_bytes(b"revision")
        vid = self.ledger.ingest("revision", meta, relative_path="revision.bin")
        self.ledger.publish(vid)
        path.unlink()
        result = self.select()
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["version_id"], vid)

    def test_process_crash_before_ready_commit(self):
        self.process_crash(False)

    def test_process_crash_after_ready_commit(self):
        self.process_crash(True)

    def process_crash(self, after_commit):
        vid = self.ingest()
        script = """
import json, os, sys
from offline_evidence_ledger import Ledger
path, root, policy, clock, vid, after = json.loads(sys.argv[1])
ledger = Ledger(path, private_root=root, policy=policy, clock=lambda: clock)
if after:
    ledger.publish(vid)
    os._exit(73)
ledger.publish(vid, before_commit=lambda: os._exit(73))
"""
        result = subprocess.run([sys.executable, "-B", "-c", script,
                                 json.dumps([str(self.path), str(self.root), self.policy,
                                             self.clock(), vid, after_commit])],
                                cwd=Path(__file__).parent, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 73, result.stderr)
        self.ledger.close()
        self.ledger = self.open()
        self.assertEqual(self.select()["status"], "ELIGIBLE" if after_commit else "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
