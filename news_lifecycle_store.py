"""Transactional local storage. All tables are namespaced; no network APIs."""
from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3
from news_notification_policy import canonical, identifier, stamp, utc


SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO meta VALUES ('schema','1');
CREATE TABLE clocks (ns TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE policies (ns TEXT, version TEXT, payload TEXT NOT NULL, PRIMARY KEY(ns,version));
CREATE TABLE stories (ns TEXT, id TEXT, status TEXT NOT NULL, PRIMARY KEY(ns,id));
CREATE TABLE aliases (ns TEXT, alias TEXT, story TEXT, PRIMARY KEY(ns,alias,story));
CREATE TABLE members (ns TEXT, member TEXT, story TEXT, PRIMARY KEY(ns,member,story));
CREATE TABLE episodes (ns TEXT, id TEXT, story TEXT, payload TEXT NOT NULL, PRIMARY KEY(ns,id));
CREATE TABLE inputs (ns TEXT, id TEXT, hash TEXT, payload TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(ns,id));
CREATE TABLE evidence (ns TEXT, ref TEXT, payload TEXT NOT NULL, PRIMARY KEY(ns,ref));
CREATE TABLE journal (seq INTEGER PRIMARY KEY, ns TEXT, kind TEXT, at TEXT, payload TEXT NOT NULL);
CREATE TABLE migrations (ns TEXT, id TEXT, parent TEXT, child TEXT, at TEXT, PRIMARY KEY(ns,id,parent,child));
CREATE TABLE outbox (ns TEXT, id TEXT, story TEXT, episode TEXT, recipient TEXT,
 status TEXT, attempts INTEGER NOT NULL DEFAULT 0, next_at TEXT, last_at TEXT,
 retryable INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL, PRIMARY KEY(ns,id));
CREATE TABLE attempts (ns TEXT, intent TEXT, number INTEGER, started TEXT, acknowledged TEXT,
 outcome TEXT, code TEXT, receipt TEXT, PRIMARY KEY(ns,intent,number));
"""


class Store:
    def __init__(self, path, *, namespace, mode):
        identifier(namespace)
        if mode not in {"offline", "replay"}:
            raise ValueError("only offline or replay stores are supported")
        path = Path(path).resolve()
        root = Path(__file__).resolve().parent
        if path.is_relative_to(root) or any((p / ".git").exists() for p in path.parents):
            raise ValueError("database must be outside the repository")
        self.ns = mode + ":" + namespace
        self.mode = mode
        existed = path.exists()
        self.db = sqlite3.connect(str(path), timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA synchronous=FULL")
            if existed:
                if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("database integrity failure")
                if self.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0] != "1":
                    raise sqlite3.DatabaseError("unsupported schema")
            else:
                self.db.executescript("BEGIN IMMEDIATE;" + SCHEMA + "COMMIT;")
        except Exception:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def clock(self, now):
        row = self.db.execute("SELECT value FROM clocks WHERE ns=?", (self.ns,)).fetchone()
        if row and now < utc(row[0]):
            raise ValueError("decision clock cannot move backwards")
        self.db.execute("INSERT OR REPLACE INTO clocks VALUES (?,?)", (self.ns, stamp(now)))

    def log(self, kind, now, payload):
        self.db.execute("INSERT INTO journal(ns,kind,at,payload) VALUES (?,?,?,?)",
                        (self.ns, kind, stamp(now), canonical(payload)))

    def episode(self, story):
        row = self.db.execute("SELECT payload FROM episodes WHERE ns=? AND story=? ORDER BY rowid DESC LIMIT 1",
                              (self.ns, story)).fetchone()
        return json.loads(row[0]) if row else None

    def save_episode(self, episode):
        self.db.execute("INSERT INTO episodes VALUES (?,?,?,?) ON CONFLICT(ns,id) DO UPDATE SET payload=excluded.payload",
                        (self.ns, episode["episode_id"], episode["canonical_story_id"], canonical(episode)))

    def ancestors(self, story):
        found, pending = {story}, [story]
        while pending:
            child = pending.pop()
            for row in self.db.execute("SELECT parent FROM migrations WHERE ns=? AND child=?", (self.ns, child)):
                if row[0] not in found:
                    found.add(row[0]); pending.append(row[0])
        return found

    def communicated(self, story, recipient, hypothesis, *, evidence_observed_at=None):
        if not hypothesis:
            return False
        ancestors = self.ancestors(story)
        for row in self.db.execute("SELECT story,payload FROM outbox WHERE ns=? AND recipient=? AND status='delivered'", (self.ns, recipient)):
            payload = json.loads(row["payload"])
            if row["story"] in ancestors and payload.get("hypothesis_id") == hypothesis and payload["notification_kind"] != "MARKET_HYPOTHESIS_CONTRADICTED":
                if evidence_observed_at is None or utc(payload["available_at_utc"]) < utc(evidence_observed_at):
                    return True
        return False

    def outbox(self):
        return [dict(r) | {"payload": json.loads(r["payload"])} for r in
                self.db.execute("SELECT * FROM outbox WHERE ns=? ORDER BY rowid", (self.ns,))]

    def backup(self, path):
        # Backups obey the same outside-repository constraint as primary stores.
        target = Path(path).resolve()
        if target.exists() or target.is_relative_to(Path(__file__).resolve().parent) or any((p / ".git").exists() for p in target.parents):
            raise ValueError("new external backup path required")
        destination = sqlite3.connect(str(target))
        try:
            self.db.backup(destination)
        finally:
            destination.close()
