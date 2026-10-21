"""D1 synthetic/offline evidence journal. No runtime or network integrations."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import base64
import hashlib
import json
import math
import sqlite3


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def utc(value):
    if not isinstance(value, str):
        raise ValueError("missing timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp must explicitly be UTC")
    return result.astimezone(timezone.utc)


def stamp(value):
    return utc(value).isoformat(timespec="microseconds")


class LedgerError(ValueError):
    pass


class Ledger:
    """One new private SQLite file; authoritative events only, no migrations.

    clock() returns at_utc, session_id, monotonic, uncertainty_seconds, health_id.
    Policy MUST be explicitly synthetic; no production tolerances are supplied.
    """

    def __init__(self, path, *, clock, policy, private_root):
        self.path = Path(path).resolve()
        self.root = Path(private_root).resolve()
        runtime = Path(r"C:\Users\User\MarketRadar").resolve()
        for location in (self.path, self.root):
            if (location == runtime or location.is_relative_to(runtime)
                    or any((p / ".git").exists() for p in (location, *location.parents))):
                raise LedgerError("private ledger/payload root must be outside runtime/repositories")
        if not self.path.is_relative_to(self.root):
            raise LedgerError("ledger must be inside explicit private root")
        self.clock = clock
        self.policy = json.loads(canonical(policy))
        if (policy.get("classification") != "synthetic"
                or not str(policy.get("policy_id", "")).startswith("synthetic:")
                or not isinstance(policy.get("max_uncertainty_seconds"), (int, float))
                or not math.isfinite(policy["max_uncertainty_seconds"])
                or policy["max_uncertainty_seconds"] < 0):
            raise LedgerError("explicit synthetic clock policy required")
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA synchronous=FULL")
            with self.transaction():
                tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if not tables:
                    self.db.execute("CREATE TABLE d1_meta (contract TEXT NOT NULL, policy TEXT NOT NULL)")
                    self.db.execute("INSERT INTO d1_meta VALUES ('d1_v1',?)", (canonical(policy),))
                    self.db.execute("""CREATE TABLE d1_events (
                        seq INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
                        key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
                        body TEXT NOT NULL, hash TEXT NOT NULL)""")
                    for action in ("UPDATE", "DELETE"):
                        for table in ("d1_events", "d1_meta"):
                            self.db.execute(f"CREATE TRIGGER {table}_{action} BEFORE {action} ON {table} "
                                            "BEGIN SELECT RAISE(ABORT,'append-only'); END")
                row = self.db.execute("SELECT * FROM d1_meta").fetchone()
                if row is None or tuple(row) != ("d1_v1", canonical(policy)):
                    raise LedgerError("schema/policy mismatch; no automatic migration")
                self.events()
        except BaseException:
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

    def events(self):
        previous, result = "0" * 64, []
        for row in self.db.execute("SELECT * FROM d1_events ORDER BY seq"):
            event = json.loads(row["body"])
            if (event["sequence"] != len(result) + 1 or event["sequence"] != row["seq"]
                    or event["predecessor_hash"] != previous
                    or event["event_id"] != row["event_id"] or event["key"] != row["key"]
                    or event["kind"] != row["kind"]
                    or event["event_id"] != digest(event["key"].encode())
                    or event["payload_hash"] != digest(canonical(event["payload"]).encode())
                    or digest(canonical(event).encode()) != row["hash"]):
                raise LedgerError("journal integrity failure")
            previous = row["hash"]
            result.append(event)
        return result

    def _append(self, kind, key, payload, clock=None, causation=None):
        history = self.events()
        previous = self.db.execute("SELECT hash FROM d1_events ORDER BY seq DESC LIMIT 1").fetchone()
        event = dict(sequence=len(history) + 1, schema_version="d1_v1", kind=kind,
                     event_id=digest(key.encode()), key=key, producer="offline_evidence_ledger:d1_v1",
                     timestamp=clock["at_utc"] if clock else None,
                     timestamp_authority="synthetic_clock" if clock else "UNKNOWN",
                     missing_timestamp_reason=None if clock else "no_validated_event_clock",
                     clock=clock, causation=causation,
                     correlation=payload.get("item_id"), payload=payload,
                     payload_hash=digest(canonical(payload).encode()),
                     predecessor_hash=previous[0] if previous else "0" * 64)
        body = canonical(event)
        self.db.execute("INSERT INTO d1_events VALUES (?,?,?,?,?,?)",
                        (event["sequence"], event["event_id"], key, kind, body, digest(body.encode())))
        return event

    def _clock(self):
        c = json.loads(canonical(self.clock()))
        c["at_utc"] = stamp(c["at_utc"])
        for field in ("session_id", "health_id"):
            if not isinstance(c.get(field), str) or not c[field]:
                raise LedgerError("missing clock identity")
        for field in ("monotonic", "uncertainty_seconds"):
            if not isinstance(c.get(field), (int, float)) or not math.isfinite(c[field]) or c[field] < 0:
                raise LedgerError("invalid clock measurement")
        if c["uncertainty_seconds"] > self.policy["max_uncertainty_seconds"]:
            raise LedgerError("unacceptable clock uncertainty")
        clocks = [e["clock"] for e in self.events() if e["clock"]]
        if clocks and utc(c["at_utc"]) < utc(clocks[-1]["at_utc"]):
            raise LedgerError("wall clock rollback")
        same = [p for p in clocks if p["session_id"] == c["session_id"]]
        if same and c["monotonic"] < same[-1]["monotonic"]:
            raise LedgerError("session monotonic rollback")
        return c

    def projection(self, maximum_sequence=None):
        """Rebuild exact semantic state, including all receipts and rejected events."""
        state = dict(versions={}, receipts={}, ready={}, rejected=[], recovery=[])
        for e in self.events():
            if maximum_sequence is not None and e["sequence"] > maximum_sequence:
                break
            p = e["payload"]
            if e["kind"] == "STORED":
                state["versions"][p["version_id"]] = p
            elif e["kind"] == "RECEIVED":
                state["receipts"][p["acquisition_id"]] = p
            elif e["kind"] == "READY":
                state["ready"][p["version_id"]] = dict(p, sequence=e["sequence"])
            elif e["kind"] == "QUARANTINED":
                state["rejected"].append(e)
            elif e["kind"] == "RECOVERY":
                state["recovery"].append(e)
        return state

    def _payload(self, version):
        ref = version["storage"]
        if "inline_b64" in ref:
            data = base64.b64decode(ref["inline_b64"], validate=True)
        else:
            path = (self.root / ref["relative_path"]).resolve()
            if not path.is_relative_to(self.root):
                raise LedgerError("external payload escapes private root")
            runtime = Path(r"C:\Users\User\MarketRadar").resolve()
            if path.is_relative_to(runtime) or any((p / ".git").exists() for p in path.parents):
                raise LedgerError("external payload cannot reference runtime/repositories")
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise LedgerError("external payload unavailable") from exc
        if digest(data) != version["content_hash"]:
            raise LedgerError("payload hash mismatch")
        return data

    def ingest(self, acquisition_id, metadata, *, payload=None, relative_path=None):
        """Persist receipt/version atomically. Never publishes READY implicitly."""
        if not isinstance(acquisition_id, str) or not acquisition_id:
            raise LedgerError("acquisition ID required")
        if (payload is None) == (relative_path is None):
            raise LedgerError("exactly one payload representation required")
        storage = ({"inline_b64": base64.b64encode(payload).decode()} if payload is not None
                   else {"relative_path": str(relative_path)})
        request = dict(metadata=metadata, storage=storage)
        request_hash = digest(canonical(request).encode())
        error, result = None, None
        with self.transaction():
            state = self.projection()
            old = state["receipts"].get(acquisition_id)
            if old:
                if old["request_hash"] != request_hash:
                    raise LedgerError("acquisition key conflict")
                self._payload(state["versions"][old["version_id"]])
                return old["version_id"]
            try:
                clock = self._clock()
                m = json.loads(canonical(metadata))
                for f in ("item_id", "source_id", "schema_version", "source_revision", "content_hash"):
                    if not isinstance(m.get(f), str) or not m[f]:
                        raise LedgerError("missing " + f)
                for f in ("published", "observed"):
                    if not isinstance(m.get(f), dict) or not m[f].get("authority"):
                        raise LedgerError("missing timestamp authority")
                    uncertainty = m[f].get("uncertainty_seconds")
                    if uncertainty is not None and (not isinstance(uncertainty, (int, float))
                                                   or not math.isfinite(uncertainty) or uncertainty < 0):
                        raise LedgerError("invalid source timestamp uncertainty")
                observed = utc(m["observed"]["at_utc"])
                published = m["published"].get("at_utc")
                if published is None:
                    if not m["published"].get("missing_reason"):
                        raise LedgerError("missing publication reason")
                elif utc(published) > observed:
                    raise LedgerError("publication after receipt")
                if observed > utc(clock["at_utc"]):
                    raise LedgerError("future receipt")
                for f in ("quality", "validation_status", "attestation"):
                    if f not in m:
                        raise LedgerError("missing " + f)
                a = m["attestation"]
                for f in ("verification_method", "claimed_source_identity", "lineage", "uncertainty"):
                    if f not in a:
                        raise LedgerError("missing attestation " + f)
                window = m.get("observation_window")
                if window and (utc(window["start_utc"]) > utc(window["end_utc"])
                               or utc(window["end_utc"]) > observed):
                    raise LedgerError("invalid observation window")
                identity = {k: v for k, v in m.items() if k != "observed"}
                version_id = digest(canonical(identity).encode())
                prior = m.get("predecessor_version_id")
                siblings = [v for v in state["versions"].values()
                            if v["metadata"]["item_id"] == m["item_id"]
                            and v["metadata"]["source_id"] == m["source_id"]]
                if version_id not in state["versions"]:
                    if siblings and not prior:
                        raise LedgerError("revision requires predecessor")
                    if prior and (prior not in state["versions"] or
                                  state["versions"][prior] not in siblings):
                        raise LedgerError("invalid predecessor")
                    if prior and any(v["metadata"].get("predecessor_version_id") == prior for v in siblings):
                        raise LedgerError("correction lineage fork; reference latest version")
                version = dict(version_id=version_id, item_id=m["item_id"], metadata=m,
                               content_hash=m["content_hash"], storage=storage,
                               first_acquisition_id=acquisition_id, stored_clock=clock,
                               historical_availability="UNKNOWN")
                self._payload(version)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                error = str(exc)
                key = "reject:" + acquisition_id + ":" + request_hash
                if not self.db.execute("SELECT 1 FROM d1_events WHERE key=?", (key,)).fetchone():
                    self._append("QUARANTINED", key, dict(acquisition_id=acquisition_id,
                                 request_hash=request_hash, reason=error))
            else:
                if version_id not in state["versions"]:
                    stored = self._append("STORED", "stored:" + version_id, version, clock)
                    if prior:
                        self._append("CORRECTED", "corrected:" + version_id,
                                     dict(version_id=version_id, predecessor_version_id=prior,
                                          item_id=m["item_id"]), clock, stored["event_id"])
                receipt = dict(acquisition_id=acquisition_id, request_hash=request_hash,
                               version_id=version_id, item_id=m["item_id"],
                               observed=m["observed"], acquisition_clock=clock)
                self._append("RECEIVED", "receipt:" + acquisition_id, receipt, clock)
                result = version_id
        if error is not None:
            raise LedgerError(error)
        return result

    def publish(self, version_id, *, recovery_id=None, before_commit=None):
        """Second transaction; hook enables offline crash/isolation tests only."""
        with self.transaction():
            state = self.projection()
            if version_id not in state["versions"]:
                raise LedgerError("unknown version")
            version = state["versions"][version_id]
            self._payload(version)
            if version_id in state["ready"]:
                return state["ready"][version_id]
            clock = self._clock()
            if version["metadata"]["validation_status"] != "valid":
                raise LedgerError("version not validated")
            prior = version["metadata"].get("predecessor_version_id")
            if prior and prior not in state["ready"]:
                raise LedgerError("publish predecessor before correction")
            if recovery_id:
                self._append("RECOVERY", "recovery:" + recovery_id,
                             dict(version_id=version_id, action="publish_stored_version_now"), clock)
            p = dict(version_id=version_id, available_at_utc=clock["at_utc"],
                     availability_authority="synthetic_ledger_ready_barrier",
                     clock_health_reference=clock["health_id"])
            e = self._append("READY", "ready:" + version_id, p, clock)
            if before_commit:
                before_commit()
            return dict(p, sequence=e["sequence"])

    def cutoff(self, as_of_utc):
        at = stamp(as_of_utc)
        with self.transaction():
            clock = self._clock()
            if utc(at) > utc(clock["at_utc"]):
                raise LedgerError("cannot capture future cutoff")
            seq = self.db.execute("SELECT COALESCE(MAX(seq),0) FROM d1_events").fetchone()[0]
        return dict(as_of_utc=at, maximum_visible_ledger_sequence=seq)

    def select(self, source_id, item_id, cutoff):
        at = utc(cutoff["as_of_utc"])
        seq = cutoff["maximum_visible_ledger_sequence"]
        if type(seq) is not int or seq < 0:
            raise LedgerError("invalid cutoff sequence")
        maximum = self.db.execute("SELECT COALESCE(MAX(seq),0) FROM d1_events").fetchone()[0]
        if seq > maximum:
            raise LedgerError("cutoff sequence not yet committed")
        state = self.projection(seq)
        candidates, excluded = [], []
        for vid, v in state["versions"].items():
            if (v["metadata"]["source_id"], v["item_id"]) != (source_id, item_id):
                continue
            ready = state["ready"].get(vid)
            if not ready:
                excluded.append(dict(version_id=vid, reason="AVAILABILITY_UNKNOWN_AT_CUTOFF"))
            elif utc(ready["available_at_utc"]) > at:
                excluded.append(dict(version_id=vid, reason="AVAILABLE_AFTER_AS_OF"))
            else:
                candidates.append((ready["sequence"], vid, v, ready))
        if not candidates:
            return dict(status="UNKNOWN", selected=None, excluded=excluded,
                        reason="NO_ELIGIBLE_VERSION")
        _, vid, version, ready = max(candidates)
        # Never fall back to an older version after detecting corruption of the selected one.
        try:
            self._payload(version)
        except (ValueError, OSError) as exc:
            return dict(status="UNKNOWN", selected=None, excluded=excluded,
                        reason="PAYLOAD_UNAVAILABLE_OR_CORRUPT", version_id=vid, detail=str(exc))
        return dict(status="ELIGIBLE", selected=dict(version=version, ready=ready),
                    excluded=excluded, reason=None)
