"""D3 synthetic/offline audit only. There is deliberately no transport callable."""
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
import json
import math
import sqlite3

from offline_evidence_ledger import LedgerError, canonical, stamp, utc
from offline_scenario_ledger import copy, fingerprint


EVENT_TYPES = {"NEW", "MATERIAL_UPDATE", "RESOLVED", "EXPIRED"}
TERMINAL = {"RESOLVED", "EXPIRED"}


class NotificationAudit:
    def __init__(self, path, *, decisions, clock, clock_policy, private_root):
        self.path, root = Path(path).resolve(), Path(private_root).resolve()
        runtime = Path(r"C:\Users\User\MarketRadar").resolve()
        if (not self.path.is_relative_to(root) or self.path.is_relative_to(runtime)
                or self.path in {decisions.path, decisions.evidence.path}
                or any((p / ".git").exists() for p in self.path.parents)):
            raise LedgerError("separate private offline D3 database required")
        self.decisions, self.clock, self.clock_policy = decisions, clock, copy(clock_policy)
        self._synthetic(clock_policy)
        self._number(clock_policy["max_uncertainty_seconds"])
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA synchronous=FULL")
            with self.transaction():
                if not self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                    self.db.execute("CREATE TABLE d3_meta (schema TEXT, clock_policy TEXT)")
                    self.db.execute("INSERT INTO d3_meta VALUES ('d3_v1',?)", (canonical(clock_policy),))
                    self.db.execute("CREATE TABLE d3_events (seq INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, body TEXT NOT NULL, hash TEXT NOT NULL)")
                    for table in ("d3_meta", "d3_events"):
                        for action in ("UPDATE", "DELETE"):
                            self.db.execute(f"CREATE TRIGGER {table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'append-only'); END")
                row = self.db.execute("SELECT * FROM d3_meta").fetchone()
                if row is None or tuple(row) != ("d3_v1", canonical(clock_policy)):
                    raise LedgerError("D3 schema/policy mismatch; no migrations")
                self.events()
        except BaseException:
            self.db.close()
            raise

    @staticmethod
    def _synthetic(policy):
        if policy.get("classification") != "synthetic" or not str(policy.get("policy_id", "")).startswith("synthetic:"):
            raise LedgerError("explicit versioned synthetic policy required")

    @staticmethod
    def _number(value):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise LedgerError("invalid explicit policy/clock number")

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
        result, previous = [], "0" * 64
        for row in self.db.execute("SELECT * FROM d3_events ORDER BY seq"):
            if maximum_sequence is not None and row["seq"] > maximum_sequence:
                break
            e = json.loads(row["body"])
            if (e["sequence"] != len(result) + 1 or row["seq"] != e["sequence"]
                    or e["key"] != row["key"] or e["event_id"] != fingerprint(e["key"])
                    or e["predecessor_hash"] != previous or fingerprint(e) != row["hash"]
                    or e["payload_hash"] != fingerprint(e["payload"])):
                raise LedgerError("D3 journal integrity failure")
            result.append(e)
            previous = row["hash"]
        return result

    def projection(self, maximum_sequence=None):
        p = dict(eligibility={}, notifications={}, attempts={}, policies={})
        for e in self.events(maximum_sequence):
            v, kind = e["payload"], e["kind"]
            if kind == "POLICY_REGISTERED":
                p["policies"][v["policy"]["policy_id"]] = v["policy"]
            elif kind == "ALERT_ELIGIBILITY_ASSESSED":
                p["eligibility"][v["eligibility_id"]] = v
            elif kind == "NOTIFICATION_GENERATED":
                p["notifications"][v["notification_id"]] = v
            elif kind == "DELIVERY_ATTEMPT_PREPARED":
                p["attempts"][v["attempt_id"]] = dict(v, state="PREPARED", history=[e["event_id"]])
            elif kind.startswith("DELIVERY_"):
                a = p["attempts"][v["attempt_id"]]
                a.update(state=v["state"], last_event=v, history=a["history"] + [e["event_id"]])
        return p

    def _old(self, key, request):
        for e in self.events():
            if e["key"] == key:
                if e["request_hash"] != fingerprint(request):
                    raise LedgerError("same-key/different-payload conflict")
                return e["payload"]
        return None

    def _now(self, *predecessor_times):
        c = copy(self.clock())
        c["at_utc"] = stamp(c["at_utc"])
        for field in ("session_id", "health_id"):
            if not isinstance(c.get(field), str) or not c[field]:
                raise LedgerError("missing synthetic clock identity")
        self._number(c["monotonic"])
        self._number(c["uncertainty_seconds"])
        if c["uncertainty_seconds"] > self.clock_policy["max_uncertainty_seconds"]:
            raise LedgerError("clock uncertainty")
        events = self.events()
        times = list(predecessor_times) + ([events[-1]["timestamp"]] if events else [])
        if any(utc(t) > utc(c["at_utc"]) for t in times):
            raise LedgerError("clock/causal chronology rollback")
        same = [e["clock"] for e in events if e["clock"]["session_id"] == c["session_id"]]
        if same and c["monotonic"] < same[-1]["monotonic"]:
            raise LedgerError("session clock rollback")
        return c

    def _append(self, key, kind, request, payload, clock, causes, before_commit=None):
        events = self.events()
        payload = copy(dict(payload, event_id=fingerprint(key), recorded_at_utc=clock["at_utc"], sequence=len(events) + 1))
        event = dict(sequence=len(events) + 1, event_id=fingerprint(key), key=key, kind=kind,
                     schema_version="d3_v1", producer="offline_notification_audit:d3_v1",
                     timestamp=clock["at_utc"], clock=clock, causation=causes,
                     correlation=payload.get("notification_id") or payload.get("evaluation_id"),
                     request_hash=fingerprint(request), payload=payload, payload_hash=fingerprint(payload),
                     predecessor_hash=fingerprint(events[-1]) if events else "0" * 64)
        self.db.execute("INSERT INTO d3_events VALUES (?,?,?,?)",
                        (event["sequence"], key, canonical(event), fingerprint(event)))
        if before_commit:
            before_commit()
        return payload

    def _upstream(self, evaluation_id):
        # D2 is read under a snapshot; D1 is append-only and checked at the exact prefix.
        d2 = self.decisions
        d2.db.execute("BEGIN")
        try:
            history = d2.events()
            state = d2.projection()
            if evaluation_id not in state["evaluations"]:
                raise LedgerError("missing exact D2 evaluation")
            evaluation = state["evaluations"][evaluation_id]
            version = state["versions"].get(evaluation["scenario_version_id"])
            if version is None or version["scenario"] != evaluation["technical_context"]:
                raise LedgerError("missing/corrupt scenario reference")
            event = next(e for e in history if e["kind"] == "EVALUATION_COMPLETED" and e["payload"]["evaluation_id"] == evaluation_id)
            binding = dict(evaluation_event_id=event["event_id"], evaluation_hash=fingerprint(evaluation),
                           scenario_hash=fingerprint(version), d2_event_hash=fingerprint(event))
        finally:
            d2.db.execute("ROLLBACK")
        d1 = d2.evidence
        d1.db.execute("BEGIN")
        try:
            seq = evaluation["cutoff"]["maximum_visible_ledger_sequence"]
            history = d1.events()
            if seq > len(history) or fingerprint(history[:seq]) != evaluation["manifest"]["d1_prefix_hash"]:
                raise LedgerError("missing/corrupt D1 prefix")
            for manifest in (evaluation["original_manifest"], evaluation["manifest"]):
                for role, selection in manifest["inputs"].items():
                    if selection["selected"] is not None:
                        selected = selection["selected"]
                        meta = selected["version"]["metadata"]
                        actual = d1.select(meta["source_id"], meta["item_id"], evaluation["cutoff"])
                        if actual["selected"] != selected:
                            raise LedgerError("missing/corrupt frozen D1 evidence: " + role)
        finally:
            d1.db.execute("ROLLBACK")
        return copy(evaluation), copy(version), binding

    @staticmethod
    def _recipient(value):
        if (set(value) != {"set_id", "fingerprint"} or not value["set_id"].startswith("synthetic:")
                or len(value["fingerprint"]) != 64 or any(c not in "0123456789abcdef" for c in value["fingerprint"])):
            raise LedgerError("opaque synthetic recipient-set identity/fingerprint required")

    @staticmethod
    def _policy(policy):
        NotificationAudit._synthetic(policy)
        if (set(policy["allowed_roles"]) - {"primary", "nested", "alternative"}
                or set(policy["allowed_events"]) - EVENT_TYPES
                or not isinstance(policy["allowed_states"], list)
                or type(policy["require_entry_permission"]) is not bool):
            raise LedgerError("invalid explicit notification policy")
        NotificationAudit._number(policy["episode_max_age_seconds"])
        retry = policy["retry"]
        NotificationAudit._synthetic(retry)
        if type(retry["max_attempts"]) is not int or retry["max_attempts"] < 1:
            raise LedgerError("invalid retry limit")
        for f in ("allow_failed_retry", "allow_reconciled_unknown_retry"):
            if type(retry[f]) is not bool:
                raise LedgerError("explicit retry policy required")

    @staticmethod
    def _hypothesis(scenario, hid):
        for role, entries in (("primary", [scenario["primary"]]), ("nested", scenario["nested"]), ("alternative", scenario["alternatives"])):
            for h in entries:
                if h["id"] == hid:
                    return role, h
        raise LedgerError("missing exact hypothesis ID")

    @staticmethod
    def _semantic(scenario, role, h, evaluation):
        # Evaluation IDs, price ticks, source count, wording and policy versions
        # are deliberately not evidence of a material change.
        fields = ("point_a", "point_b", "FTA", "TA", "invalidation", "liquidity_objectives", "htf", "market_structure")
        return dict(role=role, hypothesis=copy(h), technical={k: dict(present=k in scenario, value=scenario.get(k)) for k in fields},
                    news_status=evaluation["news_status"])

    def assess(self, key, *, evaluation_id, hypothesis_id, episode_id, event_type,
               recipients, policy, reason, predecessor_notification_id=None,
               resolution=None, before_commit=None):
        request = copy(dict(evaluation_id=evaluation_id, hypothesis_id=hypothesis_id,
                            episode_id=episode_id, event_type=event_type, recipients=recipients,
                            policy=policy, reason=reason, predecessor_notification_id=predecessor_notification_id,
                            resolution=resolution))
        with self.transaction():
            old = self._old("eligibility:" + key, request)
            if old is not None:
                return old
            self._recipient(recipients)
            self._policy(policy)
            if event_type not in EVENT_TYPES or not episode_id.startswith("synthetic:") or not isinstance(reason, str) or not reason.strip():
                raise LedgerError("explicit lifecycle event, episode and reason required")
            state = self.projection()
            known = state["policies"].get(policy["policy_id"])
            if known is not None and known != policy:
                raise LedgerError("policy ID content conflict")
            for registered in state["policies"].values():
                if registered["retry"]["policy_id"] == policy["retry"]["policy_id"] and registered["retry"] != policy["retry"]:
                    raise LedgerError("retry policy ID content conflict")
            evaluation, version, binding = self._upstream(evaluation_id)
            clock = self._now(evaluation["evaluated_at_utc"], version["created_at_utc"])
            scenario = version["scenario"]
            role, h = self._hypothesis(scenario, hypothesis_id)
            stream = dict(scenario_id=version["scenario_id"], hypothesis_id=hypothesis_id, episode_id=episode_id)
            for a in state["eligibility"].values():
                if a["stream"] == stream and a["recipients"] != recipients:
                    raise LedgerError("recipient-set mismatch")
            previous = [n for n in state["notifications"].values() if n["stream"] == stream]
            latest = previous[-1] if previous else None
            lineage = [n for n in state["notifications"].values()
                       if n["stream"]["scenario_id"] == stream["scenario_id"] and n["hypothesis_id"] == hypothesis_id]
            parent = latest or (lineage[-1] if lineage else None)
            if parent and parent["recipients"] != recipients:
                raise LedgerError("recipient-set mismatch across linked episodes")
            if predecessor_notification_id != (parent["notification_id"] if parent else None):
                raise LedgerError("exact latest predecessor required")
            semantic = self._semantic(scenario, role, h, evaluation)
            changed = {} if latest is None else {f: dict(before=latest["semantic"].get(f), after=value)
                                               for f, value in semantic.items() if value != latest["semantic"].get(f)}
            if latest:
                # Flatten technical fields so Point A/B, FTA/TA have direct before/after records.
                changed.pop("technical", None)
                changed.update({f: dict(before=latest["semantic"]["technical"][f], after=v)
                                for f, v in semantic["technical"].items() if v != latest["semantic"]["technical"][f]})
            status, reasons = "ELIGIBLE", [reason]
            if role not in policy["allowed_roles"] or event_type not in policy["allowed_events"]:
                status, reasons = "INELIGIBLE", ["POLICY_EXCLUDES_ROLE_OR_EVENT"]
            elif latest is None and parent and parent["lifecycle_state"] not in TERMINAL:
                status, reasons = "INELIGIBLE", ["PREVIOUS_EPISODE_STILL_ACTIVE"]
            elif latest is None and parent and parent["semantic"] == semantic:
                status, reasons = "SUPPRESSED_DUPLICATE", ["UNCHANGED_TERMINAL_CONTEXT"]
            elif latest and latest["lifecycle_state"] in TERMINAL:
                status, reasons = "INELIGIBLE", ["TERMINAL_EPISODE"]
            elif latest and not changed and event_type not in TERMINAL:
                status, reasons = "SUPPRESSED_DUPLICATE", ["UNCHANGED_SEMANTIC_CONTEXT"]
            elif (latest is None and event_type != "NEW") or (latest is not None and event_type == "NEW"):
                status, reasons = "INELIGIBLE", ["INVALID_EPISODE_TRANSITION"]
            elif event_type == "RESOLVED":
                if not resolution or resolution.get("reason") not in {"verified_correction", "documented_cancellation", "authorized_manual"}:
                    raise LedgerError("explicit resolution attestation required")
                candidates = [v["selected"] for v in evaluation["manifest"]["inputs"].values() if v["selected"]]
                selected = next((s for s in candidates if s["version"]["version_id"] == resolution.get("evidence_version_id")), None)
                if selected is None:
                    raise LedgerError("resolution must reference frozen evidence")
                attestation = json.loads(self.decisions.evidence._payload(selected["version"])).get("resolution")
                if attestation != resolution.get("attestation") or not attestation or attestation.get("verified") is not True or attestation.get("relation") != "resolves" or attestation.get("reason") != resolution["reason"]:
                    raise LedgerError("resolution attestation mismatch")
            elif event_type == "EXPIRED":
                deadline = utc(previous[0]["recorded_at_utc"]) + timedelta(seconds=previous[0]["policy"]["episode_max_age_seconds"])
                if utc(clock["at_utc"]) < deadline:
                    status, reasons = "INELIGIBLE", ["EXPIRY_DEADLINE_NOT_REACHED"]
            elif h.get("state") not in policy["allowed_states"] or h.get("state") == "invalidated":
                status, reasons = "INELIGIBLE", ["HYPOTHESIS_NOT_ALERTABLE"]
            elif evaluation["knowledge"] == "UNKNOWN":
                status, reasons = "UNKNOWN", ["D2_KNOWLEDGE_UNKNOWN"]
            elif evaluation["action"] == "WAIT":
                status, reasons = "DEFERRED", ["D2_ACTION_WAIT"]
            elif policy["require_entry_permission"] and scenario["permissions"].get("entry") is not True:
                status, reasons = "INELIGIBLE", ["EXPLICIT_TECHNICAL_PERMISSION_ABSENT"]
            lifecycle_state = "DEVELOPING" if event_type == "MATERIAL_UPDATE" else event_type
            semantic_key = fingerprint(dict(stream=stream, recipients=recipients, semantic=semantic, lifecycle_state=lifecycle_state))
            payload = dict(request, eligibility_id=fingerprint(dict(key=key, request=request)), status=status, reasons=reasons,
                           stream=stream, semantic=semantic, semantic_key=semantic_key, changes=changed,
                           lifecycle_state=lifecycle_state, role=role, direction=h["direction"],
                           hypothesis_state=h.get("state"), scenario_version_id=version["version_id"],
                           cutoff=evaluation["cutoff"], manifest_fingerprint=fingerprint(evaluation["manifest"]),
                           upstream=binding, technical_context=scenario, uncertainty=evaluation["uncertainty"],
                           evaluated_at_utc=evaluation["evaluated_at_utc"], scenario_created_at_utc=version["created_at_utc"])
            if known is None:
                self._append("policy:" + policy["policy_id"], "POLICY_REGISTERED", policy, dict(policy=policy), clock, [])
            return self._append("eligibility:" + key, "ALERT_ELIGIBILITY_ASSESSED", request, payload, clock,
                                [binding["evaluation_event_id"]] + ([parent["event_id"]] if parent else []), before_commit)

    def generate(self, key, *, eligibility_id, message, before_commit=None):
        request = copy(dict(eligibility_id=eligibility_id, message=message))
        with self.transaction():
            old = self._old("generate:" + key, request)
            if old is not None:
                return old
            state = self.projection()
            a = state["eligibility"].get(eligibility_id)
            if a is None or a["status"] != "ELIGIBLE":
                raise LedgerError("eligible assessment required")
            _, _, binding = self._upstream(a["evaluation_id"])
            if binding != a["upstream"]:
                raise LedgerError("upstream binding changed")
            previous = [n for n in state["notifications"].values() if n["stream"] == a["stream"]]
            lineage = [n for n in state["notifications"].values()
                       if n["stream"]["scenario_id"] == a["stream"]["scenario_id"] and n["hypothesis_id"] == a["hypothesis_id"]]
            parent = previous[-1] if previous else lineage[-1] if lineage else None
            if any(n["semantic_key"] == a["semantic_key"] for n in previous):
                raise LedgerError("semantic notification already generated")
            if (parent["notification_id"] if parent else None) != a["predecessor_notification_id"]:
                raise LedgerError("eligibility predecessor superseded")
            if set(message) - {"title", "summary", "action_labels"} or not message.get("title") or not message.get("summary"):
                raise LedgerError("only supplied title/summary/action labels accepted")
            clock = self._now(a["recorded_at_utc"])
            scenario = a["technical_context"]
            content = dict(original_message=message, instrument=scenario["instrument"], timeframe=scenario["timeframe"],
                           current_reference_price=scenario.get("current_reference_price"), role=a["role"], direction=a["direction"],
                           point_a=scenario.get("point_a"), point_b=scenario.get("point_b"), FTA=scenario.get("FTA"), TA=scenario.get("TA"),
                           invalidation=scenario.get("invalidation"), observation_reason=a["reason"], uncertainty=a["uncertainty"],
                           technical_permissions=scenario["permissions"], cutoff=a["cutoff"],
                           scenario_version_id=a["scenario_version_id"], evaluation_id=a["evaluation_id"],
                           manifest_fingerprint=a["manifest_fingerprint"], hypothesis_id=a["hypothesis_id"])
            payload = dict(a, notification_id=fingerprint(dict(semantic_key=a["semantic_key"], eligibility_id=eligibility_id)),
                           eligibility_id=eligibility_id, canonical_message=canonical(content), content=content,
                           payload_hash=fingerprint(content), generation_clock=clock)
            return self._append("generate:" + key, "NOTIFICATION_GENERATED", request, payload, clock, [a["event_id"]], before_commit)

    def prepare(self, key, *, notification_id, recipients, transport, before_commit=None):
        request = copy(dict(notification_id=notification_id, recipients=recipients, transport=transport))
        with self.transaction():
            old = self._old("prepare:" + key, request)
            if old is not None:
                return old
            state = self.projection()
            n = state["notifications"].get(notification_id)
            if n is None or recipients != n["recipients"]:
                raise LedgerError("missing notification or recipient-set mismatch")
            stream_notifications = [v for v in state["notifications"].values() if v["stream"] == n["stream"]]
            if n["lifecycle_state"] not in TERMINAL and stream_notifications[-1]["lifecycle_state"] in TERMINAL:
                raise LedgerError("active notification belongs to a terminal episode")
            if not isinstance(transport, str) or not transport.startswith("synthetic:"):
                raise LedgerError("synthetic transport name only; no transport is called")
            _, _, binding = self._upstream(n["evaluation_id"])
            if binding != n["upstream"]:
                raise LedgerError("upstream binding changed")
            attempts = [a for a in state["attempts"].values() if a["notification_id"] == notification_id]
            retry = n["policy"]["retry"]
            if len(attempts) >= retry["max_attempts"]:
                raise LedgerError("synthetic attempt limit")
            if attempts:
                last = attempts[-1]
                allowed = last["state"] == "FAILED" and retry["allow_failed_retry"]
                if last["state"] == "RECONCILED":
                    allowed = (last["last_event"]["retry_authorized"]
                               and last["last_event"]["evidence"].get("resolution") == "confirmed_not_accepted"
                               and retry["allow_reconciled_unknown_retry"])
                if not allowed:
                    raise LedgerError("retry forbidden without eligible failure/reconciliation")
            clock = self._now(n["recorded_at_utc"], *( [attempts[-1]["last_event"]["recorded_at_utc"]] if attempts and "last_event" in attempts[-1] else []))
            payload = dict(request, attempt_id=fingerprint(dict(notification_id=notification_id, ordinal=len(attempts)+1)),
                           delivery_id=fingerprint(dict(notification_id=notification_id, recipients=recipients)),
                           ordinal=len(attempts)+1, idempotency_key=key, payload_hash=n["payload_hash"],
                           eligibility_id=n["eligibility_id"], retry_policy=retry)
            causes = [n["event_id"]] + ([attempts[-1]["history"][-1]] if attempts else [])
            return self._append("prepare:" + key, "DELIVERY_ATTEMPT_PREPARED", request, payload, clock, causes, before_commit)

    def delivery(self, key, *, attempt_id, event_type, evidence=None, recipients, before_commit=None):
        """Record a declared synthetic observation. Never dispatches or probes."""
        request = copy(dict(attempt_id=attempt_id, event_type=event_type, evidence=evidence, recipients=recipients))
        with self.transaction():
            old = self._old("delivery:" + key, request)
            if old is not None:
                return old
            a = self.projection()["attempts"].get(attempt_id)
            if a is None or a["recipients"] != recipients:
                raise LedgerError("missing attempt or recipient-set mismatch")
            transitions = {"DELIVERY_ATTEMPT_STARTED": ("PREPARED", "STARTED"),
                           "DELIVERY_ACKNOWLEDGED": ("STARTED", "ACKNOWLEDGED"),
                           "DELIVERY_FAILED": ("STARTED", "FAILED"),
                           "DELIVERY_OUTCOME_UNKNOWN": ("STARTED", "UNKNOWN"),
                           "DELIVERY_RECONCILED": ("UNKNOWN", "RECONCILED")}
            if event_type not in transitions or a["state"] != transitions[event_type][0]:
                raise LedgerError("invalid delivery transition")
            retry_authorized = False
            if event_type != "DELIVERY_ATTEMPT_STARTED":
                if not isinstance(evidence, dict) or evidence.get("classification") != "synthetic" or not evidence.get("reason"):
                    raise LedgerError("synthetic outcome evidence required")
                if event_type == "DELIVERY_ACKNOWLEDGED" and not evidence.get("provider_ack_id"):
                    raise LedgerError("provider acceptance ID required")
                if event_type == "DELIVERY_FAILED" and not evidence.get("failure_class"):
                    raise LedgerError("failure classification required")
                if event_type == "DELIVERY_RECONCILED":
                    if evidence.get("resolution") not in {"provider_accepted", "confirmed_not_accepted", "still_unknown"} or not evidence.get("evidence_ref"):
                        raise LedgerError("explicit reconciliation evidence required")
                    if type(evidence.get("authorize_retry")) is not bool:
                        raise LedgerError("explicit retry decision required")
                    retry_authorized = evidence["authorize_retry"]
                    if retry_authorized and evidence["resolution"] != "confirmed_not_accepted":
                        raise LedgerError("retry requires confirmed_not_accepted resolution")
                    if retry_authorized and evidence.get("duplicate_risk_acknowledged") is not True:
                        raise LedgerError("retry requires duplicate-risk acknowledgement")
            clock = self._now(a.get("last_event", a)["recorded_at_utc"])
            payload = dict(request, notification_id=a["notification_id"], state=transitions[event_type][1],
                           recipient_read_proven=False, recipient_delivery_proven=False,
                           retry_authorized=retry_authorized, payload_hash=a["payload_hash"],
                           eligibility_id=a["eligibility_id"])
            return self._append("delivery:" + key, event_type, request, payload, clock, [a["history"][-1]], before_commit)

    def recover(self, key, *, before_commit=None):
        """Explicit recovery audit: committed STARTED attempts become UNKNOWN.

        PREPARED is deliberately not assumed to have started. No automatic retry.
        """
        request = dict(recovery_key=key)
        with self.transaction():
            old = self._old("recovery:" + key, request)
            if old is not None:
                return old
            clock = self._now()
            changed = []
            for a in self.projection()["attempts"].values():
                if a["state"] == "STARTED":
                    p = self._append("recovered:" + key + ":" + a["attempt_id"], "DELIVERY_OUTCOME_UNKNOWN", request,
                                     dict(attempt_id=a["attempt_id"], notification_id=a["notification_id"], state="UNKNOWN",
                                          evidence=dict(classification="synthetic", reason="restart_without_recorded_outcome"),
                                          recipients=a["recipients"], payload_hash=a["payload_hash"], eligibility_id=a["eligibility_id"],
                                          recipient_delivery_proven=False, recipient_read_proven=False, retry_authorized=False),
                                     clock, [a["history"][-1]])
                    changed.append(p["event_id"])
            return self._append("recovery:" + key, "RECOVERY_RECORDED", request, dict(unknown_events=changed), clock, changed, before_commit)
