"""Synthetic D3 end-to-end audit, without any transport integration."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import ast
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from offline_evidence_ledger import Ledger, LedgerError, canonical, digest
from offline_scenario_ledger import ScenarioLedger, fingerprint, ROLES
from offline_notification_audit import NotificationAudit


class Clock:
    def __init__(self):
        self.value = dict(at_utc="2026-10-21T10:00:00Z", session_id="synthetic:boot",
                          monotonic=100, uncertainty_seconds=0, health_id="synthetic:health")

    def __call__(self):
        return deepcopy(self.value)

    def later(self):
        self.value.update(at_utc="2026-10-21T10:10:00Z", monotonic=700)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clock = Clock()
        self.cp = dict(classification="synthetic", policy_id="synthetic:clock", max_uncertainty_seconds=0)
        self.d1 = Ledger(self.root / "d1.db", clock=self.clock, policy=self.cp, private_root=self.root)
        self.d2 = ScenarioLedger(self.root / "d2.db", evidence=self.d1, clock=self.clock, clock_policy=self.cp, private_root=self.root)
        self.d3 = self.open()
        fixtures = Path(__file__).parent / "tests/fixtures"
        self.scenario = json.loads((fixtures / "d2/scenario.json").read_text())
        self.policy = json.loads((fixtures / "d3/policy.json").read_text())
        self.ep = dict(classification="synthetic", policy_id="synthetic:eval", wait_on_conflict=True,
                       inputs={r: dict(required=r != "news", max_age_seconds=3600, age_anchor="observed",
                                       source_id="synthetic:"+r, accepted_verification_methods=["unverified"]) for r in ROLES})
        self.refs = {r: None for r in ROLES}
        for role in ("technical", "usd", "yields", "volume"):
            self.evidence(role)
        self.cutoff = self.d1.cutoff(self.clock()["at_utc"])
        self.authority = dict(kind="synthetic_technical_authority", engine_version="synthetic:pa")
        self.version = self.record("initial")
        self.evaluation = self.evaluate("initial", self.version)
        self.recipients = dict(set_id="synthetic:audience", fingerprint=fingerprint(["synthetic:recipient"]))
        self.message = dict(title="Synthetic alert", summary="Offline fixture only", action_labels=["Open chart", "Full analysis"])

    def tearDown(self):
        self.d3.close()
        self.d2.close()
        self.d1.close()
        self.temp.cleanup()

    def open(self, path=None, decisions=None):
        return NotificationAudit(path or self.root / "d3.db", decisions=decisions or self.d2,
                                 clock=self.clock, clock_policy=self.cp, private_root=self.root)

    def evidence(self, role, data=None, predecessor=None):
        payload = canonical(data or {"synthetic": role}).encode()
        revision = "2" if predecessor else "1"
        meta = dict(item_id=role, source_id="synthetic:"+role, source_revision=revision, schema_version="synthetic:v1",
                    content_hash=digest(payload), published=dict(at_utc=self.clock()["at_utc"], authority="synthetic:publisher"),
                    observed=dict(at_utc=self.clock()["at_utc"], authority="synthetic:receipt"), quality="synthetic", validation_status="valid",
                    attestation=dict(verification_method="unverified", claimed_source_identity=role, lineage=[role], uncertainty="unverified"))
        if predecessor:
            meta["predecessor_version_id"] = predecessor
        vid = self.d1.ingest(role+revision, meta, payload=payload)
        self.d1.publish(vid)
        self.refs[role] = dict(source_id=meta["source_id"], item_id=role)
        return vid

    def record(self, key, prior=None, kind=None):
        return self.d2.record(key, kind=kind or ("HYPOTHESIS_REVISED" if prior else "HYPOTHESIS_FORMED"),
                              scenario_id="scenario", scenario=self.scenario, cutoff=self.cutoff, refs=self.refs,
                              authority=self.authority, predecessor_version_id=prior["version_id"] if prior else None)

    def evaluate(self, key, version, news=None):
        return self.d2.evaluate(key, scenario_version_id=version["version_id"], policy=self.ep, engine_version="synthetic:eval", news=news)

    def assess(self, key="eligible", **kwargs):
        args = dict(evaluation_id=self.evaluation["evaluation_id"], hypothesis_id="primary", episode_id="synthetic:episode",
                    event_type="NEW", recipients=self.recipients, policy=self.policy, reason="synthetic observation")
        args.update(kwargs)
        return self.d3.assess(key, **args)

    def generate(self, key="generated", assessment=None):
        return self.d3.generate(key, eligibility_id=(assessment or self.assess())["eligibility_id"], message=self.message)

    def prepare(self, notification=None, key="attempt", **kwargs):
        args = dict(notification_id=(notification or self.generate())["notification_id"], recipients=self.recipients, transport="synthetic:transport")
        args.update(kwargs)
        return self.d3.prepare(key, **args)

    def delivery(self, attempt, event_type, key=None, evidence=None, **kwargs):
        return self.d3.delivery(key or event_type, attempt_id=attempt["attempt_id"], event_type=event_type,
                                evidence=evidence, recipients=self.recipients, **kwargs)

    def started(self):
        attempt = self.prepare()
        self.delivery(attempt, "DELIVERY_ATTEMPT_STARTED")
        return attempt

    def unknown(self):
        attempt = self.started()
        self.delivery(attempt, "DELIVERY_OUTCOME_UNKNOWN", evidence=dict(classification="synthetic", reason="timeout"))
        return attempt

    def test_eligible_primary_no_trade_permission(self):
        a = self.assess()
        self.assertEqual(a["status"], "ELIGIBLE")
        self.assertFalse(a["technical_context"]["permissions"]["entry"])

    def test_nested_bullish_independent_history(self):
        primary = self.generate()
        nested = self.generate("nested", self.assess("nested", hypothesis_id="nested"))
        self.assertEqual((primary["direction"], nested["direction"]), ("bearish", "bullish"))
        self.assertNotEqual(primary["notification_id"], nested["notification_id"])
        self.assertEqual(len(self.d3.projection()["notifications"]), 2)

    def test_alternative_notification(self):
        n = self.generate(assessment=self.assess(hypothesis_id="alternative"))
        self.assertEqual(n["role"], "alternative")

    def test_duplicate_eligibility_suppressed(self):
        n = self.generate()
        a = self.assess("repeat", event_type="MATERIAL_UPDATE", predecessor_notification_id=n["notification_id"])
        self.assertEqual(a["status"], "SUPPRESSED_DUPLICATE")

    def test_material_update_exact_changes(self):
        old = self.generate()
        self.scenario["point_a"]["price"] = 112
        self.scenario["point_b"]["price"] = 101
        self.scenario["FTA"], self.scenario["TA"] = 94, 89
        self.scenario["invalidation"]["price"] = 112
        revised = self.record("revision", self.version)
        ev = self.evaluate("revision", revised)
        a = self.assess("update", evaluation_id=ev["evaluation_id"], event_type="MATERIAL_UPDATE",
                        predecessor_notification_id=old["notification_id"], reason="declared technical revision")
        self.assertEqual(a["status"], "ELIGIBLE")
        self.assertEqual(a["changes"]["FTA"], dict(before=dict(present=True, value=95), after=dict(present=True, value=94)))
        self.assertEqual(set(a["changes"]), {"point_a", "point_b", "FTA", "TA", "invalidation"})
        new = self.generate("updated", a)
        self.assertEqual(new["lifecycle_state"], "DEVELOPING")
        self.assertEqual(new["predecessor_notification_id"], old["notification_id"])

    def test_repeated_evaluation_is_not_material(self):
        n = self.generate()
        ev = self.evaluate("repeated", self.version)
        a = self.assess("repeat", evaluation_id=ev["evaluation_id"], event_type="MATERIAL_UPDATE", predecessor_notification_id=n["notification_id"])
        self.assertEqual(a["status"], "SUPPRESSED_DUPLICATE")

    def test_invalidated_hypothesis_ineligible(self):
        self.scenario["primary"]["state"] = "invalidated"
        v = self.record("invalidated", self.version, "HYPOTHESIS_INVALIDATED")
        ev = self.evaluate("invalidated", v)
        self.assertEqual(self.assess(evaluation_id=ev["evaluation_id"])["status"], "INELIGIBLE")

    def test_missing_mandatory_evidence_unknown(self):
        self.refs["usd"] = None
        v = self.record("missing", self.version)
        ev = self.evaluate("missing", v)
        self.assertEqual(self.assess(evaluation_id=ev["evaluation_id"])["status"], "UNKNOWN")

    def test_conflicting_news_deferred_permissions_unchanged(self):
        interpretation = dict(status="conflicts", explanation="synthetic conflict")
        vid = self.evidence("news", interpretation)
        self.cutoff = self.d1.cutoff(self.clock()["at_utc"])
        v = self.record("news", self.version)
        ev = self.evaluate("news", v, dict(evidence_version_id=vid, interpretation=interpretation))
        a = self.assess(evaluation_id=ev["evaluation_id"])
        self.assertEqual(a["status"], "DEFERRED")
        self.assertEqual(a["technical_context"]["permissions"], self.scenario["permissions"])

    def test_assessment_not_generation(self):
        self.assess()
        self.assertEqual(self.d3.projection()["notifications"], {})

    def test_generated_never_attempted(self):
        n = self.generate()
        self.assertEqual(self.d3.projection()["attempts"], {})
        self.assertEqual(n["payload_hash"], fingerprint(n["content"]))
        self.assertEqual(n["canonical_message"], canonical(n["content"]))

    def test_prepared_not_started(self):
        a = self.prepare()
        self.assertEqual(self.d3.projection()["attempts"][a["attempt_id"]]["state"], "PREPARED")
        self.d3.recover("restart")
        self.assertEqual(self.d3.projection()["attempts"][a["attempt_id"]]["state"], "PREPARED")

    def test_started_recovery_unknown(self):
        a = self.started()
        self.d3.close()
        self.d3 = self.open()
        self.d3.recover("restart")
        self.assertEqual(self.d3.projection()["attempts"][a["attempt_id"]]["state"], "UNKNOWN")

    def test_provider_ack_not_recipient_read(self):
        a = self.started()
        outcome = self.delivery(a, "DELIVERY_ACKNOWLEDGED", evidence=dict(classification="synthetic", reason="acceptance", provider_ack_id="synthetic:ack"))
        self.assertFalse(outcome["recipient_read_proven"])
        self.assertFalse(outcome["recipient_delivery_proven"])
        with self.assertRaises(LedgerError):
            self.prepare(self.d3.projection()["notifications"][a["notification_id"]], key="retry")

    def test_explicit_failure(self):
        a = self.started()
        result = self.delivery(a, "DELIVERY_FAILED", evidence=dict(classification="synthetic", reason="rejected", failure_class="synthetic:rejected"))
        self.assertEqual(result["state"], "FAILED")
        retry = self.prepare(self.d3.projection()["notifications"][a["notification_id"]], key="retry")
        self.assertEqual(retry["ordinal"], 2)

    def test_reconcile_then_retry(self):
        a = self.unknown()
        self.delivery(a, "DELIVERY_RECONCILED", evidence=dict(classification="synthetic", reason="manual review", resolution="still_unknown",
                      evidence_ref="synthetic:review", authorize_retry=True, duplicate_risk_acknowledged=True))
        retry = self.prepare(self.d3.projection()["notifications"][a["notification_id"]], key="retry")
        self.assertEqual(retry["ordinal"], 2)

    def test_unknown_no_retry(self):
        a = self.unknown()
        before = self.d3.events()
        with self.assertRaisesRegex(LedgerError, "retry forbidden"):
            self.prepare(self.d3.projection()["notifications"][a["notification_id"]], key="retry")
        self.assertEqual(self.d3.events(), before)

    def test_risk_acknowledgement_required(self):
        a = self.unknown()
        with self.assertRaisesRegex(LedgerError, "risk"):
            self.delivery(a, "DELIVERY_RECONCILED", evidence=dict(classification="synthetic", reason="review", resolution="still_unknown", evidence_ref="synthetic:r", authorize_retry=True))

    def test_duplicate_attempt_idempotent_after_restart(self):
        n = self.generate()
        a = self.prepare(n)
        before = self.d3.events()
        self.d3.close()
        self.d3 = self.open()
        self.assertEqual(self.prepare(n), a)
        self.assertEqual(self.d3.events(), before)

    def test_attempt_key_conflict(self):
        n = self.generate()
        self.prepare(n)
        before = self.d3.events()
        with self.assertRaisesRegex(LedgerError, "conflict"):
            self.prepare(n, transport="synthetic:other")
        self.assertEqual(self.d3.events(), before)

    def test_recipient_mismatch_atomic(self):
        n = self.generate()
        before = self.d3.projection(), self.d3.events()
        with self.assertRaisesRegex(LedgerError, "recipient"):
            self.prepare(n, recipients=dict(self.recipients, fingerprint="0"*64))
        self.assertEqual((self.d3.projection(), self.d3.events()), before)

    def test_eligibility_recipient_mismatch(self):
        self.assess()
        before = self.d3.events()
        with self.assertRaisesRegex(LedgerError, "recipient"):
            self.assess("redirect", recipients=dict(self.recipients, fingerprint="0"*64))
        self.assertEqual(self.d3.events(), before)

    def test_attempt_cannot_ack_before_start(self):
        a = self.prepare()
        with self.assertRaisesRegex(LedgerError, "transition"):
            self.delivery(a, "DELIVERY_ACKNOWLEDGED", evidence=dict(classification="synthetic", reason="ack", provider_ack_id="synthetic:a"))

    def test_rollback_policy_and_eligibility_atomic(self):
        def crash():
            raise RuntimeError("synthetic crash")
        with self.assertRaises(RuntimeError):
            self.assess(before_commit=crash)
        self.assertEqual(self.d3.events(), [])

    def test_restart_projection_complete(self):
        self.unknown()
        before = self.d3.events(), self.d3.projection()
        self.d3.close()
        self.d3 = self.open()
        self.assertEqual((self.d3.events(), self.d3.projection()), before)

    def test_future_evidence_cannot_change_alert(self):
        n = self.generate()
        before = self.d3.events()
        old = self.evaluation["manifest"]["inputs"]["usd"]["selected"]["version"]["version_id"]
        self.clock.later()
        self.evidence("usd", {"future_price": 999}, old)
        self.assertEqual(self.generate(), n)
        self.assertEqual(self.d3.events(), before)

    def test_future_scenario_cannot_rewrite_alert(self):
        n = self.generate()
        self.scenario["point_a"]["price"] = 999
        self.record("future", self.version)
        self.assertEqual(self.d3.projection()["notifications"][n["notification_id"]], n)

    def test_equal_timestamp_ordering(self):
        self.unknown()
        events = self.d3.events()
        self.assertEqual([e["sequence"] for e in events], list(range(1, len(events)+1)))
        self.assertEqual(len({e["timestamp"] for e in events}), 1)
        self.assertTrue(all(e["causation"] for e in events if e["kind"] != "POLICY_REGISTERED"))

    def test_missing_upstream_fails_closed(self):
        with self.assertRaisesRegex(LedgerError, "missing exact"):
            self.assess(evaluation_id="missing")
        self.assertEqual(self.d3.events(), [])

    def test_corrupt_upstream_fails_closed(self):
        self.d2.db.execute("DROP TRIGGER d2_events_UPDATE")
        self.d2.db.execute("UPDATE d2_events SET hash='tampered' WHERE seq=1")
        with self.assertRaisesRegex(LedgerError, "integrity"):
            self.assess()
        self.assertEqual(self.d3.events(), [])

    def test_corrupt_d1_prefix_fails_closed(self):
        self.d1.db.execute("DROP TRIGGER d1_events_UPDATE")
        self.d1.db.execute("UPDATE d1_events SET hash='tampered' WHERE seq=1")
        with self.assertRaisesRegex(LedgerError, "integrity"):
            self.assess()
        self.assertEqual(self.d3.events(), [])

    def test_later_assessment_still_uses_exact_old_evaluation(self):
        first = self.assess()
        old = self.evaluation["manifest"]["inputs"]["usd"]["selected"]["version"]["version_id"]
        self.clock.later()
        self.evidence("usd", {"later_price": 999}, old)
        self.cutoff = self.d1.cutoff(self.clock()["at_utc"])
        revised = self.record("future", self.version)
        self.evaluate("future", revised)
        later = self.assess("later")
        self.assertEqual(later["evaluation_id"], first["evaluation_id"])
        self.assertEqual(later["manifest_fingerprint"], first["manifest_fingerprint"])
        self.assertEqual(later["cutoff"], first["cutoff"])

    def test_reconciliation_provider_acceptance_cannot_retry(self):
        a = self.unknown()
        self.delivery(a, "DELIVERY_RECONCILED", evidence=dict(classification="synthetic", reason="receipt found", resolution="provider_accepted",
                      evidence_ref="synthetic:receipt", authorize_retry=False))
        with self.assertRaisesRegex(LedgerError, "retry forbidden"):
            self.prepare(self.d3.projection()["notifications"][a["notification_id"]], key="retry")

    def test_generation_rollback(self):
        a = self.assess()
        before = self.d3.events(), self.d3.projection()
        def crash():
            raise RuntimeError("synthetic crash")
        with self.assertRaises(RuntimeError):
            self.d3.generate("failed", eligibility_id=a["eligibility_id"], message=self.message, before_commit=crash)
        self.assertEqual((self.d3.events(), self.d3.projection()), before)

    def test_terminal_episode_cannot_reopen_in_place(self):
        n = self.generate()
        self.clock.later()
        expired = self.generate("expired", self.assess("expired", event_type="EXPIRED", predecessor_notification_id=n["notification_id"]))
        a = self.assess("reopen", event_type="MATERIAL_UPDATE", predecessor_notification_id=expired["notification_id"])
        self.assertEqual(a["status"], "INELIGIBLE")

    def test_no_upstream_writes(self):
        before = self.d1.events(), self.d2.events()
        self.unknown()
        self.assertEqual((self.d1.events(), self.d2.events()), before)

    def test_no_transport_broker_or_trade_imports(self):
        tree = ast.parse((Path(__file__).parent / "offline_notification_audit.py").read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module)
        self.assertEqual(imports, {"contextlib", "datetime", "pathlib", "json", "math", "sqlite3", "offline_evidence_ledger", "offline_scenario_ledger"})
        with self.assertRaisesRegex(LedgerError, "synthetic transport"):
            self.prepare(transport="telegram")

    def test_full_prefix_equivalence(self):
        def replay(path, stop):
            ledger = self.open(path)
            snapshots = []
            try:
                a = n = attempt = None
                for i in range(stop):
                    if i == 0:
                        a = ledger.assess("a", evaluation_id=self.evaluation["evaluation_id"], hypothesis_id="primary", episode_id="synthetic:episode",
                                          event_type="NEW", recipients=self.recipients, policy=self.policy, reason="synthetic")
                    elif i == 1:
                        n = ledger.generate("n", eligibility_id=a["eligibility_id"], message=self.message)
                    elif i == 2:
                        attempt = ledger.prepare("p", notification_id=n["notification_id"], recipients=self.recipients, transport="synthetic:transport")
                    elif i == 3:
                        ledger.delivery("s", attempt_id=attempt["attempt_id"], event_type="DELIVERY_ATTEMPT_STARTED", recipients=self.recipients)
                    else:
                        ledger.recover("r")
                    snapshots.append((ledger.events(), ledger.projection()))
                return snapshots
            finally:
                ledger.close()
        full = replay(self.root / "full.db", 5)
        for stop in range(1, 6):
            self.assertEqual(replay(self.root / f"prefix{stop}.db", stop), full[:stop])

    def test_generation_race_two_assessments(self):
        first, second = self.assess("one"), self.assess("two")
        self.generate("one", first)
        with self.assertRaisesRegex(LedgerError, "already generated"):
            self.generate("two", second)

    def test_expiry_is_not_resolution(self):
        n = self.generate()
        a = self.assess("early-expire", event_type="EXPIRED", predecessor_notification_id=n["notification_id"])
        self.assertEqual(a["status"], "INELIGIBLE")
        self.clock.later()
        a = self.assess("expire", event_type="EXPIRED", predecessor_notification_id=n["notification_id"])
        expired = self.generate("expired", a)
        self.assertEqual(expired["lifecycle_state"], "EXPIRED")

    def test_resolution_requires_frozen_attestation(self):
        n = self.generate()
        with self.assertRaisesRegex(LedgerError, "attestation"):
            self.assess("resolve", event_type="RESOLVED", predecessor_notification_id=n["notification_id"])

    def test_resolution_with_frozen_attestation(self):
        n = self.generate()
        attest = dict(verified=True, relation="resolves", reason="authorized_manual")
        interpretation = dict(status="not_evaluated", resolution=attest)
        vid = self.evidence("news", interpretation)
        self.cutoff = self.d1.cutoff(self.clock()["at_utc"])
        v = self.record("resolved", self.version)
        ev = self.evaluate("resolved", v, dict(evidence_version_id=vid, interpretation=interpretation))
        a = self.assess("resolve", evaluation_id=ev["evaluation_id"], event_type="RESOLVED", predecessor_notification_id=n["notification_id"],
                        resolution=dict(evidence_version_id=vid, attestation=attest, reason="authorized_manual"))
        self.assertEqual(self.generate("resolved", a)["lifecycle_state"], "RESOLVED")

    def test_explicit_permission_policy_never_grants_entry(self):
        self.policy["require_entry_permission"] = True
        self.assertEqual(self.assess()["status"], "INELIGIBLE")

    def test_new_episode_cannot_bypass_active_history(self):
        n = self.generate()
        a = self.assess("new-episode", episode_id="synthetic:second", predecessor_notification_id=n["notification_id"])
        self.assertEqual(a["status"], "INELIGIBLE")

    def test_unchanged_terminal_context_does_not_reopen(self):
        n = self.generate()
        self.clock.later()
        expired = self.generate("expired", self.assess("expired", event_type="EXPIRED", predecessor_notification_id=n["notification_id"]))
        a = self.assess("new-episode", episode_id="synthetic:second", predecessor_notification_id=expired["notification_id"])
        self.assertEqual(a["status"], "SUPPRESSED_DUPLICATE")

    def test_changed_context_new_linked_episode(self):
        n = self.generate()
        self.clock.later()
        expired = self.generate("expired", self.assess("expired", event_type="EXPIRED", predecessor_notification_id=n["notification_id"]))
        self.scenario["FTA"] = 94
        v = self.record("changed", self.version)
        ev = self.evaluate("changed", v)
        a = self.assess("new-episode", evaluation_id=ev["evaluation_id"], episode_id="synthetic:second", predecessor_notification_id=expired["notification_id"])
        self.assertEqual(a["status"], "ELIGIBLE")
        self.generate("new-episode", a)

    def test_linked_episode_cannot_redirect_recipients(self):
        n = self.generate()
        before = self.d3.events()
        with self.assertRaisesRegex(LedgerError, "recipient"):
            self.assess("redirect", episode_id="synthetic:second", predecessor_notification_id=n["notification_id"], recipients=dict(self.recipients, fingerprint="0"*64))
        self.assertEqual(self.d3.events(), before)

    def test_retry_policy_version_immutable(self):
        self.assess()
        policy = deepcopy(self.policy)
        policy["policy_id"] = "synthetic:notifications-v2"
        policy["retry"]["max_attempts"] += 1
        with self.assertRaisesRegex(LedgerError, "retry policy ID"):
            self.assess("different-policy", policy=policy)

    def test_no_attempt_for_old_active_notification_after_expiry(self):
        n = self.generate()
        self.clock.later()
        self.generate("expired", self.assess("expired", event_type="EXPIRED", predecessor_notification_id=n["notification_id"]))
        with self.assertRaisesRegex(LedgerError, "terminal"):
            self.prepare(n)

    def test_preparation_atomic_rollback(self):
        n = self.generate()
        before = self.d3.events(), self.d3.projection()
        def crash():
            raise RuntimeError("synthetic crash")
        with self.assertRaises(RuntimeError):
            self.prepare(n, before_commit=crash)
        self.assertEqual((self.d3.events(), self.d3.projection()), before)

    def test_unknown_recovery_idempotent(self):
        self.started()
        first = self.d3.recover("restart")
        before = self.d3.events()
        self.assertEqual(self.d3.recover("restart"), first)
        self.assertEqual(self.d3.events(), before)

    def test_concurrent_attempt_keys_do_not_double_prepare(self):
        n = self.generate()
        def worker(_):
            d1 = Ledger(self.root / "d1.db", clock=self.clock, policy=self.cp, private_root=self.root)
            d2 = ScenarioLedger(self.root / "d2.db", evidence=d1, clock=self.clock, clock_policy=self.cp, private_root=self.root)
            d3 = self.open(decisions=d2)
            try:
                return d3.prepare("parallel", notification_id=n["notification_id"], recipients=self.recipients, transport="synthetic:transport")
            finally:
                d3.close(); d2.close(); d1.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(worker, range(2)))
        self.assertEqual(a, b)
        self.assertEqual(len(self.d3.projection()["attempts"]), 1)

    def test_missing_technical_optional_fields_remain_missing(self):
        del self.scenario["FTA"]
        v = self.record("missing-target", self.version)
        ev = self.evaluate("missing-target", v)
        n = self.generate(assessment=self.assess(evaluation_id=ev["evaluation_id"]))
        self.assertIsNone(n["content"]["FTA"])
        self.assertFalse(n["semantic"]["technical"]["FTA"]["present"])

    def test_append_only(self):
        self.assess()
        with self.assertRaises(sqlite3.IntegrityError):
            self.d3.db.execute("DELETE FROM d3_events")

    def test_clock_rollback(self):
        a = self.assess()
        self.clock.value["at_utc"] = "2026-10-21T09:00:00Z"
        with self.assertRaisesRegex(LedgerError, "chronology"):
            self.generate(assessment=a)

    def test_process_crash_before_commit(self):
        self.process_crash(False)

    def test_process_crash_after_commit(self):
        self.process_crash(True)

    def process_crash(self, after):
        attempt = self.prepare()
        script = """
import json,os,sys
from pathlib import Path
from offline_evidence_ledger import Ledger
from offline_scenario_ledger import ScenarioLedger
from offline_notification_audit import NotificationAudit
root, cp, clock, aid, recipients, after = json.loads(sys.argv[1]); root=Path(root)
d1=Ledger(root/'d1.db',clock=lambda:clock,policy=cp,private_root=root)
d2=ScenarioLedger(root/'d2.db',evidence=d1,clock=lambda:clock,clock_policy=cp,private_root=root)
d3=NotificationAudit(root/'d3.db',decisions=d2,clock=lambda:clock,clock_policy=cp,private_root=root)
d3.delivery('crash-start',attempt_id=aid,event_type='DELIVERY_ATTEMPT_STARTED',recipients=recipients,
            before_commit=None if after else lambda:os._exit(73))
os._exit(73)
"""
        r = subprocess.run([sys.executable, "-B", "-c", script, json.dumps([str(self.root), self.cp, self.clock(), attempt["attempt_id"], self.recipients, after])],
                           cwd=Path(__file__).parent, capture_output=True, timeout=30)
        self.assertEqual(r.returncode, 73, r.stderr)
        self.d3.close()
        self.d3 = self.open()
        self.d3.recover("restart")
        self.assertEqual(self.d3.projection()["attempts"][attempt["attempt_id"]]["state"], "UNKNOWN" if after else "PREPARED")


if __name__ == "__main__":
    unittest.main()
