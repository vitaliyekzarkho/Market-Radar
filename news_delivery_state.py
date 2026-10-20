"""Offline delivery ledger only. No transport implementation or network imports."""
from datetime import timedelta
import json
from news_notification_policy import identifier, stamp, utc


class Delivery:
    def __init__(self, store):
        self.store = store

    def _row(self, key):
        row = self.store.db.execute("SELECT * FROM outbox WHERE ns=? AND id=?", (self.store.ns, key)).fetchone()
        if row is None:
            raise ValueError("unknown intent")
        return row, json.loads(row["payload"])

    def _set(self, key, status, now):
        self.store.db.execute("UPDATE outbox SET status=?,last_at=? WHERE ns=? AND id=?",
                              (status, stamp(now), self.store.ns, key))

    def begin(self, key, *, now):
        """Record an attempt; caller may only simulate outcomes in this block."""
        s, moment = self.store, utc(now)
        with s.transaction():
            s.clock(moment)
            row, payload = self._row(key)
            if row["status"] not in {"queued", "failed"}:
                return None
            if moment >= utc(payload["expires_at_utc"]):
                self._set(key, "expired", moment)
                s.log("NOTIFICATION_EXPIRED", moment, {"intent": key})
                return None
            policy = payload["delivery_policy"]
            if row["status"] == "failed" and not row["retryable"]:
                return None
            if row["attempts"] >= policy["retry_max_attempts"] or moment < utc(row["next_at"]):
                return None
            number = row["attempts"] + 1
            s.db.execute("INSERT INTO attempts VALUES (?,?,?,?,NULL,'attempted',NULL,NULL)",
                         (s.ns, key, number, stamp(moment)))
            s.db.execute("UPDATE outbox SET status='attempted',attempts=?,last_at=?,retryable=0 WHERE ns=? AND id=?",
                         (number, stamp(moment), s.ns, key))
            s.log("ATTEMPTED", moment, {"intent": key, "attempt": number})
            return number

    def acknowledge(self, key, number, *, outcome, now, receipt_ref=None):
        """Use a closed outcome vocabulary; never persist an exception string."""
        if outcome not in {"delivered", "transient_failure", "permanent_failure", "timeout", "unknown"}:
            raise ValueError("unsupported safe outcome code")
        if outcome == "delivered":
            identifier(receipt_ref)
        elif receipt_ref is not None:
            raise ValueError("receipt only belongs to delivery")
        s, moment = self.store, utc(now)
        with s.transaction():
            s.clock(moment)
            row, payload = self._row(key)
            if row["attempts"] != number or row["status"] not in {"attempted", "unknown"}:
                raise ValueError("stale or duplicate acknowledgment")
            attempt = s.db.execute("SELECT * FROM attempts WHERE ns=? AND intent=? AND number=?", (s.ns, key, number)).fetchone()
            if moment < utc(attempt["started"]):
                raise ValueError("acknowledgment before attempt")
            if row["status"] == "unknown" and outcome != "delivered":
                raise ValueError("unknown needs explicit resolution or late receipt")
            status = "delivered" if outcome == "delivered" else "failed" if outcome.endswith("_failure") else "unknown"
            retryable = outcome == "transient_failure"
            policy = payload["delivery_policy"]
            ready = moment + timedelta(seconds=policy["retry_backoff_seconds"] * (2 ** (number - 1)))
            s.db.execute("UPDATE attempts SET acknowledged=?,outcome=?,code=?,receipt=? WHERE ns=? AND intent=? AND number=?",
                         (stamp(moment), status, outcome, receipt_ref, s.ns, key, number))
            s.db.execute("UPDATE outbox SET status=?,last_at=?,retryable=?,next_at=? WHERE ns=? AND id=?",
                         (status, stamp(moment), int(retryable), stamp(ready), s.ns, key))
            s.log("DELIVERY_OUTCOME", moment, {"intent": key, "attempt": number, "outcome": status, "code": outcome})

    def recover(self, *, now):
        """Expired attempt leases become unknown, never automatically failed."""
        s, moment = self.store, utc(now)
        changed = []
        with s.transaction():
            s.clock(moment)
            for row in s.db.execute("SELECT * FROM outbox WHERE ns=? AND status='attempted'", (s.ns,)).fetchall():
                policy = json.loads(row["payload"])["delivery_policy"]
                if moment >= utc(row["last_at"]) + timedelta(seconds=policy["attempt_timeout_seconds"]):
                    self._set(row["id"], "unknown", moment)
                    s.db.execute("UPDATE attempts SET outcome='unknown',code='recovered_inflight' WHERE ns=? AND intent=? AND number=?",
                                 (s.ns, row["id"], row["attempts"]))
                    s.log("RECOVERED_UNKNOWN", moment, {"intent": row["id"], "attempt": row["attempts"]})
                    changed.append(row["id"])
        return changed

    def resolve_unknown(self, key, *, decision_ref, action, acknowledge_duplicate_risk, now):
        identifier(decision_ref)
        if action not in {"retry", "cancel"} or type(acknowledge_duplicate_risk) is not bool:
            raise ValueError("invalid explicit decision")
        if action == "retry" and not acknowledge_duplicate_risk:
            raise ValueError("duplicate delivery risk must be acknowledged")
        s, moment = self.store, utc(now)
        with s.transaction():
            s.clock(moment)
            row, payload = self._row(key)
            if row["status"] != "unknown":
                raise ValueError("only unknown can be resolved")
            if action == "retry" and (moment >= utc(payload["expires_at_utc"]) or row["attempts"] >= payload["delivery_policy"]["retry_max_attempts"]):
                raise ValueError("retry budget or TTL exhausted")
            self._set(key, "queued" if action == "retry" else "cancelled", moment)
            s.db.execute("UPDATE outbox SET next_at=? WHERE ns=? AND id=?", (stamp(moment), s.ns, key))
            s.log("UNKNOWN_DECISION", moment, {"intent": key, "decision_ref": decision_ref, "action": action,
                                             "acknowledge_duplicate_risk": acknowledge_duplicate_risk})

    def dispose(self, key, *, disposition, decision_ref, now):
        identifier(decision_ref)
        if disposition not in {"cancelled", "superseded"}:
            raise ValueError("invalid disposition")
        s, moment = self.store, utc(now)
        with s.transaction():
            s.clock(moment)
            row, _ = self._row(key)
            if row["status"] not in {"queued", "failed"}:
                raise ValueError("inflight, unknown or delivered outcomes cannot be discarded")
            self._set(key, disposition, moment)
            s.log("DISPOSITION", moment, {"intent": key, "disposition": disposition, "decision_ref": decision_ref})
