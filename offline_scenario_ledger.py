"""D2 offline immutable technical snapshots and D1-linked evaluations.

No technical engine, notification, transport or runtime adapter is invoked.
"""
from contextlib import contextmanager
from pathlib import Path
import json
import math
import sqlite3

from offline_evidence_ledger import canonical, digest, stamp, utc, LedgerError


ROLES = ("technical", "news", "usd", "yields", "volume")
KINDS = {"HYPOTHESIS_FORMED", "HYPOTHESIS_REVISED", "HYPOTHESIS_INVALIDATED",
         "SCENARIO_OBSERVED_EXISTING"}


def copy(value):
    return json.loads(canonical(value))


def fingerprint(value):
    return digest(canonical(value).encode())


class ScenarioLedger:
    def __init__(self, path, *, evidence, clock, clock_policy, private_root):
        self.path = Path(path).resolve()
        root = Path(private_root).resolve()
        runtime = Path(r"C:\Users\User\MarketRadar").resolve()
        if (not self.path.is_relative_to(root) or self.path.is_relative_to(runtime)
                or any((p / ".git").exists() for p in self.path.parents)):
            raise LedgerError("D2 database must be private and outside runtime/repositories")
        if self.path == evidence.path:
            raise LedgerError("D2 must not migrate the D1 database")
        self.evidence, self.clock = evidence, clock
        self.clock_policy = copy(clock_policy)
        if (clock_policy.get("classification") != "synthetic"
                or not str(clock_policy.get("policy_id", "")).startswith("synthetic:")
                or type(clock_policy.get("max_uncertainty_seconds")) not in (int, float)
                or not math.isfinite(clock_policy["max_uncertainty_seconds"])
                or clock_policy["max_uncertainty_seconds"] < 0):
            raise LedgerError("explicit synthetic clock policy required")
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA synchronous=FULL")
            with self.transaction():
                if not self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                    self.db.execute("CREATE TABLE d2_meta (contract TEXT, clock_policy TEXT)")
                    self.db.execute("INSERT INTO d2_meta VALUES ('d2_v1',?)", (canonical(clock_policy),))
                    self.db.execute("CREATE TABLE d2_events (seq INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, body TEXT NOT NULL, hash TEXT NOT NULL)")
                    for table in ("d2_events", "d2_meta"):
                        for action in ("UPDATE", "DELETE"):
                            self.db.execute(f"CREATE TRIGGER {table}_{action} BEFORE {action} ON {table} "
                                            "BEGIN SELECT RAISE(ABORT,'append-only'); END")
                row = self.db.execute("SELECT * FROM d2_meta").fetchone()
                if row is None or tuple(row) != ("d2_v1", canonical(clock_policy)):
                    raise LedgerError("D2 schema/clock policy mismatch")
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

    def events(self, maximum_sequence=None):
        previous, result = "0" * 64, []
        for row in self.db.execute("SELECT * FROM d2_events ORDER BY seq"):
            if maximum_sequence is not None and row["seq"] > maximum_sequence:
                break
            e = json.loads(row["body"])
            if (row["seq"] != len(result) + 1 or e["sequence"] != row["seq"]
                    or e["key"] != row["key"] or e["event_id"] != fingerprint(e["key"])
                    or e["predecessor_hash"] != previous or fingerprint(e) != row["hash"]
                    or fingerprint(e["payload"]) != e["payload_hash"]):
                raise LedgerError("D2 journal integrity failure")
            result.append(e)
            previous = row["hash"]
        return result

    def projection(self, maximum_sequence=None):
        result = dict(versions={}, latest={}, evaluations={}, policies={})
        for e in self.events(maximum_sequence):
            p = e["payload"]
            if e["kind"] in KINDS:
                result["versions"][p["version_id"]] = p
                result["latest"][p["scenario_id"]] = p["version_id"]
            elif e["kind"] == "EVALUATION_COMPLETED":
                result["evaluations"][p["evaluation_id"]] = p
            elif e["kind"] == "POLICY_REGISTERED":
                result["policies"][p["policy_id"]] = p
        return result

    def _append(self, key, kind, request_hash, payload, clock):
        events = self.events()
        e = dict(sequence=len(events) + 1, key=key, event_id=fingerprint(key), kind=kind,
                 schema_version="d2_v1", producer="offline_scenario_ledger:d2_v1",
                 timestamp=clock["at_utc"], clock=clock, request_hash=request_hash,
                 correlation=payload.get("scenario_id"),
                 causation=payload.get("predecessor_version_id") or payload.get("scenario_version_id"),
                 payload=payload, payload_hash=fingerprint(payload),
                 predecessor_hash=fingerprint(events[-1]) if events else "0" * 64)
        self.db.execute("INSERT INTO d2_events VALUES (?,?,?,?)",
                        (e["sequence"], key, canonical(e), fingerprint(e)))

    def _replay(self, key, request_hash):
        for e in self.events():
            if e["key"] == key:
                if e["request_hash"] != request_hash:
                    raise LedgerError("same-key/different-payload conflict")
                return e["payload"]
        return None

    def _clock(self, cutoff):
        c = copy(self.clock())
        c["at_utc"] = stamp(c["at_utc"])
        for field in ("session_id", "health_id"):
            if not isinstance(c.get(field), str) or not c[field]:
                raise LedgerError("missing clock identity")
        for field in ("monotonic", "uncertainty_seconds"):
            if type(c.get(field)) not in (int, float) or not math.isfinite(c[field]) or c[field] < 0:
                raise LedgerError("invalid clock measurement")
        if c["uncertainty_seconds"] > self.clock_policy["max_uncertainty_seconds"]:
            raise LedgerError("clock uncertainty")
        if utc(c["at_utc"]) < utc(cutoff["as_of_utc"]):
            raise LedgerError("future cutoff")
        events = self.events()
        if events and utc(c["at_utc"]) < utc(events[-1]["timestamp"]):
            raise LedgerError("clock rollback")
        same = [e["clock"] for e in events if e["clock"]["session_id"] == c["session_id"]]
        if same and c["monotonic"] < same[-1]["monotonic"]:
            raise LedgerError("monotonic rollback")
        return c

    def _manifest(self, cutoff, refs):
        # Use a D1 read transaction, not its writer API. The prefix is frozen even
        # if a concurrent writer appends evidence between D2 operations.
        stamp(cutoff["as_of_utc"])
        seq = cutoff["maximum_visible_ledger_sequence"]
        if type(seq) is not int or seq < 0 or set(refs) != set(ROLES):
            raise LedgerError("explicit cutoff and all five input roles required")
        self.evidence.db.execute("BEGIN")
        try:
            history = self.evidence.events()
            if seq > len(history):
                raise LedgerError("uncommitted D1 cutoff")
            selected = {}
            for role in ROLES:
                ref = refs[role]
                if ref is None:
                    selected[role] = dict(status="UNKNOWN", selected=None, reason="INPUT_NOT_SUPPLIED", excluded=[])
                else:
                    selected[role] = self.evidence.select(ref["source_id"], ref["item_id"], cutoff)
            return dict(cutoff=copy(cutoff), d1_prefix_hash=fingerprint(history[:seq]),
                        refs=copy(refs), inputs=selected)
        finally:
            self.evidence.db.execute("ROLLBACK")

    @staticmethod
    def _hypotheses(scenario):
        for f in ("instrument", "timeframe", "direction", "permissions", "primary", "nested", "alternatives"):
            if f not in scenario:
                raise LedgerError("missing scenario field: " + f)
        hypotheses = [scenario["primary"], *scenario["nested"], *scenario["alternatives"]]
        ids = [h["id"] for h in hypotheses]
        if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
            raise LedgerError("unique stable hypothesis IDs required")
        if scenario["primary"]["direction"] != scenario["direction"]:
            raise LedgerError("primary direction mismatch")
        by_id = {h["id"]: h for h in hypotheses}
        for h in hypotheses:
            seen, current = set(), h
            while current.get("parent_id") is not None:
                if current["id"] in seen or current["parent_id"] not in by_id:
                    raise LedgerError("invalid hypothesis parent graph")
                seen.add(current["id"])
                current = by_id[current["parent_id"]]
        return by_id

    def record(self, key, *, kind, scenario_id, scenario, cutoff, refs, authority,
               predecessor_version_id=None, hypothesis_id=None, before_commit=None):
        request = copy(dict(kind=kind, scenario_id=scenario_id, scenario=scenario,
                            cutoff=cutoff, refs=refs, authority=authority,
                            predecessor_version_id=predecessor_version_id, hypothesis_id=hypothesis_id))
        rh = fingerprint(request)
        if not isinstance(key, str) or not key or not isinstance(scenario_id, str) or not scenario_id:
            raise LedgerError("stable operation/scenario IDs required")
        with self.transaction():
            old = self._replay("scenario:" + key, rh)
            if old is not None:
                return old
            if kind not in KINDS or authority.get("kind") != "synthetic_technical_authority" or not authority.get("engine_version"):
                raise LedgerError("explicit synthetic technical authority required")
            hypotheses = self._hypotheses(scenario)
            target = hypothesis_id or scenario["primary"]["id"]
            if target not in hypotheses:
                raise LedgerError("unknown target hypothesis")
            state = self.projection()
            latest = state["latest"].get(scenario_id)
            prior = state["versions"].get(predecessor_version_id)
            if latest is None:
                if predecessor_version_id is not None or kind not in {"HYPOTHESIS_FORMED", "SCENARIO_OBSERVED_EXISTING"}:
                    raise LedgerError("first observation must be formed or observed-existing")
            else:
                if latest != predecessor_version_id or kind not in {"HYPOTHESIS_FORMED", "HYPOTHESIS_REVISED", "HYPOTHESIS_INVALIDATED"}:
                    raise LedgerError("revision must extend latest scenario version")
                if utc(cutoff["as_of_utc"]) < utc(prior["cutoff"]["as_of_utc"]) or cutoff["maximum_visible_ledger_sequence"] < prior["cutoff"]["maximum_visible_ledger_sequence"]:
                    raise LedgerError("scenario cutoff rollback")
                if (scenario["direction"] != prior["scenario"]["direction"]
                        or scenario["primary"]["id"] != prior["scenario"]["primary"]["id"]):
                    if authority.get("direction_change_authorized") is not True:
                        raise LedgerError("primary change requires explicit technical authorization")
                previous_hypotheses = self._hypotheses(prior["scenario"])
                if kind == "HYPOTHESIS_FORMED" and target in previous_hypotheses:
                    raise LedgerError("existing hypothesis cannot be newly formed")
                if kind != "HYPOTHESIS_FORMED" and target not in previous_hypotheses:
                    raise LedgerError("new hypothesis requires formation event")
                if not set(previous_hypotheses).issubset(hypotheses):
                    raise LedgerError("retain existing hypothesis identities, including terminal ones")
                if any(hypotheses[i].get("parent_id") != h.get("parent_id") for i, h in previous_hypotheses.items()):
                    if authority.get("topology_change_authorized") is not True:
                        raise LedgerError("parent change requires explicit technical authorization")
            if kind == "HYPOTHESIS_INVALIDATED" and hypotheses[target].get("state") != "invalidated":
                raise LedgerError("technical snapshot must explicitly invalidate target")
            clock = self._clock(cutoff)
            for point in (scenario.get("point_a"), scenario.get("point_b")):
                if isinstance(point, dict) and point.get("time") is not None:
                    if utc(point["time"]) > utc(cutoff["as_of_utc"]):
                        raise LedgerError("future technical point timestamp")
            manifest = self._manifest(cutoff, refs)
            if prior:
                old_seq = prior["cutoff"]["maximum_visible_ledger_sequence"]
                history = self.evidence.events()
                if fingerprint(history[:old_seq]) != prior["manifest"]["d1_prefix_hash"]:
                    raise LedgerError("D1 ledger prefix changed")
            hypothesis_history = copy(prior["hypothesis_history"]) if prior else {}
            for hid in hypotheses:
                if hid not in hypothesis_history:
                    formed = kind == "HYPOTHESIS_FORMED" and hid == target
                    hypothesis_history[hid] = dict(first_observed_at_utc=clock["at_utc"],
                                                   formed_at_utc=clock["at_utc"] if formed else None,
                                                   formation_authority="synthetic_declared" if formed else "UNKNOWN")
            payload = dict(request, hypothesis_id=target, manifest=manifest,
                           hypothesis_history=hypothesis_history,
                           created_at_utc=clock["at_utc"],
                           formation_at_utc=(prior["formation_at_utc"] if prior else
                                             clock["at_utc"] if kind == "HYPOTHESIS_FORMED" else None),
                           formation_authority=(prior["formation_authority"] if prior else
                                                "synthetic_declared" if kind == "HYPOTHESIS_FORMED" else "UNKNOWN"),
                           scenario_hash=fingerprint(scenario))
            payload["version_id"] = fingerprint(payload)
            self._append("scenario:" + key, kind, rh, payload, clock)
            if before_commit:
                before_commit()
            return copy(payload)

    @staticmethod
    def _policy(policy):
        if (policy.get("classification") != "synthetic"
                or not str(policy.get("policy_id", "")).startswith("synthetic:")
                or set(policy.get("inputs", {})) != set(ROLES)):
            raise LedgerError("explicit versioned synthetic input policy required")
        for rule in policy["inputs"].values():
            age = rule.get("max_age_seconds")
            if (type(rule.get("required")) is not bool or type(age) not in (int, float)
                    or not math.isfinite(age) or age < 0
                    or rule.get("age_anchor") not in {"observed", "published", "observation_end", "available"}):
                raise LedgerError("invalid explicit source freshness rule")
            methods = rule.get("accepted_verification_methods")
            if (not isinstance(rule.get("source_id"), str) or not rule["source_id"]
                    or not isinstance(methods, list) or not methods
                    or any(not isinstance(m, str) or not m for m in methods)):
                raise LedgerError("explicit source identity and provenance policy required")
        if type(policy.get("wait_on_conflict")) is not bool:
            raise LedgerError("explicit conflict handling required")

    def evaluate(self, key, *, scenario_version_id, policy, engine_version, news=None, before_commit=None):
        request = copy(dict(scenario_version_id=scenario_version_id, policy=policy,
                            engine_version=engine_version, news=news))
        if not isinstance(key, str) or not key or not isinstance(engine_version, str) or not engine_version:
            raise LedgerError("evaluation key and engine version required")
        rh = fingerprint(request)
        with self.transaction():
            old = self._replay("evaluation:" + key, rh)
            if old is not None:
                return old
            self._policy(policy)
            state = self.projection()
            prior_policy = state["policies"].get(policy["policy_id"])
            if prior_policy is not None and prior_policy != policy:
                raise LedgerError("policy ID content conflict")
            version = state["versions"][scenario_version_id]
            cutoff = version["cutoff"]
            clock = self._clock(cutoff)
            manifest = self._manifest(cutoff, version["refs"])
            if manifest["d1_prefix_hash"] != version["manifest"]["d1_prefix_hash"]:
                raise LedgerError("D1 ledger prefix changed")
            # A formerly readable external payload may be lost. Preserve the original
            # manifest and record present integrity failure, never substitute inputs.
            reasons, assessments, mandatory_unknown = [], {}, False
            for role in ROLES:
                selection = manifest["inputs"][role]
                rule = policy["inputs"][role]
                reason, age = selection["reason"], None
                if selection["status"] == "ELIGIBLE":
                    selected = selection["selected"]
                    m = selected["version"]["metadata"]
                    anchor = rule["age_anchor"]
                    when = (selected["ready"]["available_at_utc"] if anchor == "available" else
                            (m.get("observation_window") or {}).get("end_utc") if anchor == "observation_end" else
                            (m.get(anchor) or {}).get("at_utc"))
                    if when is None:
                        reason = "AGE_ANCHOR_UNKNOWN"
                    else:
                        age = (utc(cutoff["as_of_utc"]) - utc(when)).total_seconds()
                        reason = "FUTURE_AGE_ANCHOR" if age < 0 else "STALE" if age > rule["max_age_seconds"] else None
                    if m["source_id"] != rule["source_id"]:
                        reason = "SOURCE_POLICY_MISMATCH"
                    elif m["attestation"]["verification_method"] not in rule["accepted_verification_methods"]:
                        reason = "PROVENANCE_NOT_ACCEPTED"
                if reason:
                    reasons.append(dict(role=role, reason=reason, required=rule["required"]))
                    mandatory_unknown |= rule["required"]
                assessments[role] = dict(knowledge="UNKNOWN" if reason else "KNOWN", reason=reason,
                                         age_seconds=age, required=rule["required"])
            news_status = "not_evaluated"
            if news is not None:
                selected = manifest["inputs"]["news"]["selected"]
                if selected is None or news.get("evidence_version_id") != selected["version"]["version_id"]:
                    raise LedgerError("news must reference the selected D1 version")
                interpretation = news.get("interpretation", {})
                if interpretation.get("status") not in {"supports", "conflicts", "insufficient_data", "not_evaluated"}:
                    raise LedgerError("invalid news interpretation status")
                # Interpretation itself must be in the selected D1 payload, not
                # a freshly supplied assertion attached to historically old evidence.
                data = self.evidence._payload(selected["version"])
                if json.loads(data) != interpretation:
                    raise LedgerError("news interpretation differs from frozen evidence payload")
                news_status = interpretation["status"] if assessments["news"]["knowledge"] == "KNOWN" else "not_evaluated"
            if news_status in {"insufficient_data", "not_evaluated"} and policy["inputs"]["news"]["required"]:
                mandatory_unknown = True
                reasons.append(dict(role="news", reason="INSUFFICIENT_INTERPRETATION", required=True))
            if news is None and policy["inputs"]["news"]["required"]:
                mandatory_unknown = True
                reasons.append(dict(role="news", reason="INTERPRETATION_NOT_SUPPLIED", required=True))
            invalidated = version["scenario"]["primary"].get("state") == "invalidated"
            wait = mandatory_unknown or invalidated or (news_status == "conflicts" and policy["wait_on_conflict"])
            if invalidated:
                reasons.append(dict(role="technical", reason="PRIMARY_INVALIDATED", required=True))
            if news_status == "conflicts":
                reasons.append(dict(role="news", reason="CONFLICTING_INTERPRETATION", required=policy["inputs"]["news"]["required"]))
            payload = dict(request, scenario_id=version["scenario_id"], cutoff=copy(cutoff),
                           original_manifest=version["manifest"], manifest=manifest,
                           technical_context=copy(version["scenario"]), news_status=news_status,
                           assessments=assessments, knowledge="UNKNOWN" if mandatory_unknown else "KNOWN",
                           action="WAIT" if wait else "RECORDED", reasons=reasons,
                           uncertainty=[r for r in reasons if assessments[r["role"]]["knowledge"] == "UNKNOWN"
                                        or r["reason"] in {"INSUFFICIENT_INTERPRETATION", "INTERPRETATION_NOT_SUPPLIED"}],
                           evaluated_at_utc=clock["at_utc"], policy_hash=fingerprint(policy))
            payload["evaluation_id"] = fingerprint(payload)
            if prior_policy is None:
                self._append("policy:" + policy["policy_id"], "POLICY_REGISTERED", fingerprint(policy), copy(policy), clock)
            self._append("evaluation:" + key, "EVALUATION_COMPLETED", rh, payload, clock)
            if before_commit:
                before_commit()
            return copy(payload)
