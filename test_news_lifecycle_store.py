from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import patch
from news_lifecycle_store import Store
from news_lifecycle_engine import Lifecycle
from news_delivery_state import Delivery
from test_news_lifecycle_engine import Fixture, event, policy, tm, ROOT


class StoreTests(Fixture):
    def test_restart_preserves_queue_and_input_idempotency(self):
        self.process()
        with_store = Store(self.path, namespace="synthetic", mode="offline")
        try:
            result = Lifecycle(with_store, policy()).process(event(), ["recipient_a"], now=tm(1))
            self.assertTrue(result["duplicate"])
            self.assertEqual(len(with_store.outbox()), 1)
        finally:
            with_store.close()

    def test_transaction_rolls_back_partial_outbox_and_episode(self):
        original = self.s.log
        def fail(kind, now, payload):
            original(kind, now, payload)
            if kind == "NEW":
                raise RuntimeError("synthetic crash")
        with patch.object(self.s, "log", side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.process()
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM episodes").fetchone()[0], 0)
        self.assertFalse(self.s.outbox())
        self.assertEqual(len(self.process()["notification_ids"]), 1)

    def test_concurrent_processors_create_one_transition(self):
        def run():
            s = Store(self.path, namespace="synthetic", mode="offline")
            try:
                return Lifecycle(s, policy()).process(event(), ["recipient_a"], now=tm())
            finally:
                s.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertEqual(sum(not r["duplicate"] for r in results), 1)
        self.assertEqual(len(self.s.outbox()), 1)

    def test_replay_namespace_has_strict_cutoff_and_separate_state(self):
        self.process()
        replay = Store(self.path, namespace="synthetic", mode="replay")
        try:
            engine = Lifecycle(replay, policy())
            with self.assertRaises(ValueError):
                engine.process(event(), ["recipient_a"], now=tm(-1))
            self.assertFalse(replay.outbox())
            engine.process(event(), ["recipient_a"], now=tm())
            self.assertEqual(len(replay.outbox()), 1)
            self.assertNotEqual(replay.outbox()[0]["id"], self.s.outbox()[0]["id"])
        finally:
            replay.close()

    def test_database_corruption_does_not_reset_history(self):
        path = Path(self.tmp.name) / "corrupt.sqlite"
        content = b"synthetic corrupted database"
        path.write_bytes(content)
        with self.assertRaises(sqlite3.DatabaseError):
            Store(path, namespace="synthetic", mode="offline")
        self.assertEqual(path.read_bytes(), content)

    def test_immutable_evidence_and_input_versions(self):
        self.process()
        with self.assertRaises(ValueError):
            self.process(event(assessment="conflicts"))
        changed = event(2, 10)
        changed["evidence"][0]["ref"] = "fact_1"
        with self.assertRaises(ValueError):
            self.process(changed, 10)
        self.assertEqual(len(self.s.outbox()), 1)

    def test_clock_rollback_is_rejected(self):
        self.process()
        with self.assertRaises(ValueError):
            self.engine.expire(now=tm(-1))

    def test_database_and_backup_cannot_be_in_repository(self):
        with self.assertRaises(ValueError):
            Store(ROOT / "forbidden.sqlite", namespace="synthetic", mode="offline")
        with self.assertRaises(ValueError):
            self.s.backup(ROOT / "forbidden_backup.sqlite")

    def test_backup_retains_history(self):
        self.process()
        backup = Path(self.tmp.name) / "backup.sqlite"
        self.s.backup(backup)
        restored = Store(backup, namespace="synthetic", mode="offline")
        try:
            self.assertEqual(restored.outbox(), self.s.outbox())
        finally:
            restored.close()

    def test_numeric_recipient_and_private_fields_rejected(self):
        for recipients in [["12345"], ["chat_12345"]]:
            with self.assertRaises(ValueError):
                self.process(recipients=recipients)
        with self.assertRaises(ValueError):
            self.process(event(bot_token="synthetic_secret"))
        self.assertFalse(self.s.outbox())

    def test_actual_process_crash_before_commit_rolls_back(self):
        # Only a synthetic SQLite transaction; no project service is executed.
        code = "import sqlite3,os,sys; c=sqlite3.connect(sys.argv[1]); c.execute('BEGIN IMMEDIATE'); c.execute(\"INSERT INTO stories VALUES ('offline:synthetic','crash_story','active')\"); os._exit(17)"
        result = subprocess.run([sys.executable, "-B", "-c", code, str(self.path)], capture_output=True)
        self.assertEqual(result.returncode, 17)
        self.assertIsNone(self.s.db.execute("SELECT id FROM stories WHERE id='crash_story'").fetchone())

    def test_crash_after_attempt_before_acknowledgment_recovered(self):
        key = self.process()["notification_ids"][0]
        code = "import sys,os; from news_lifecycle_store import Store; from news_delivery_state import Delivery; s=Store(sys.argv[1],namespace='synthetic',mode='offline'); Delivery(s).begin(sys.argv[2],now=sys.argv[3]); os._exit(19)"
        result = subprocess.run([sys.executable, "-B", "-c", code, str(self.path), key, tm(1)], cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 19, result.stderr.decode())
        self.assertEqual(self.delivery.recover(now=tm(21)), [key])
        self.assertIsNone(self.delivery.begin(key, now=tm(22)))
