"""D2 synthetic persistence/temporal contracts, without runtime adapters."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from offline_evidence_ledger import Ledger, LedgerError, canonical, digest
from offline_scenario_ledger import ScenarioLedger, ROLES


class Clock:
    def __init__(self):
        self.value = dict(at_utc="2026-10-21T10:00:00Z", session_id="synthetic:boot",
                          health_id="synthetic:health", monotonic=100, uncertainty_seconds=0)

    def __call__(self):
        return deepcopy(self.value)

    def later(self):
        self.value.update(at_utc="2026-10-21T10:10:00Z", monotonic=700)


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clock = Clock()
        self.cp = dict(classification="synthetic", policy_id="synthetic:clock", max_uncertainty_seconds=0)
        self.d1 = Ledger(self.root / "d1.db", clock=self.clock, policy=self.cp, private_root=self.root)
        self.d2 = self.open()
        self.scenario = json.loads((Path(__file__).parent / "tests/fixtures/d2/scenario.json").read_text())
        self.policy = dict(classification="synthetic", policy_id="synthetic:evaluation-v1", wait_on_conflict=True,
                           inputs={r: dict(required=r != "news", max_age_seconds=60, age_anchor="observed",
                                           source_id="synthetic:" + r,
                                           accepted_verification_methods=["unverified"]) for r in ROLES})
        self.authority = dict(kind="synthetic_technical_authority", engine_version="synthetic:pa-v1")
        self.refs = {r: None for r in ROLES}
        for role in ("technical", "usd", "yields", "volume"):
            self.evidence(role)
        self.cutoff = self.d1.cutoff(self.clock()["at_utc"])

    def tearDown(self):
        self.d2.close()
        self.d1.close()
        self.temp.cleanup()

    def open(self, evidence=None, path=None):
        return ScenarioLedger(path or self.root / "d2.db", evidence=evidence or self.d1,
                              clock=self.clock, clock_policy=self.cp, private_root=self.root)

    def evidence(self, role, value=None, predecessor=None, external=False):
        data = canonical(value or {"synthetic": role}).encode()
        now = self.clock()["at_utc"]
        meta = dict(item_id=role, source_id="synthetic:" + role, schema_version="synthetic:v1",
                    source_revision="2" if predecessor else "1", content_hash=digest(data),
                    published=dict(at_utc=now, authority="synthetic:publisher"),
                    observed=dict(at_utc=now, authority="synthetic:receipt"),
                    quality="synthetic", validation_status="valid",
                    attestation=dict(verification_method="unverified", claimed_source_identity=role,
                                     lineage=[role], uncertainty="synthetic claim only"))
        if predecessor:
            meta["predecessor_version_id"] = predecessor
        args = dict(payload=data)
        if external:
            (self.root / (role + ".json")).write_bytes(data)
            args = dict(relative_path=role + ".json")
        vid = self.d1.ingest(role + meta["source_revision"], meta, **args)
        self.d1.publish(vid)
        self.refs[role] = dict(source_id=meta["source_id"], item_id=role)
        return vid

    def record(self, key="formation", **kwargs):
        args = dict(kind="HYPOTHESIS_FORMED", scenario_id="scenario1", scenario=self.scenario,
                    cutoff=self.cutoff, refs=self.refs, authority=self.authority)
        args.update(kwargs)
        return self.d2.record(key, **args)

    def evaluate(self, version, key="evaluation", **kwargs):
        args = dict(scenario_version_id=version["version_id"], policy=self.policy, engine_version="synthetic:evaluator-v1")
        args.update(kwargs)
        return self.d2.evaluate(key, **args)

    def news(self, status):
        value = dict(status=status, explanation="synthetic interpretation", unknown_field=[1, 2])
        vid = self.evidence("news", value)
        self.cutoff = self.d1.cutoff(self.clock()["at_utc"])
        return dict(evidence_version_id=vid, interpretation=value)

    def test_formation_preserves_complete_scenario(self):
        before = deepcopy(self.scenario)
        version = self.record()
        self.assertEqual(version["scenario"], before)
        self.assertEqual(self.scenario, before)
        self.assertEqual(version["formation_at_utc"], version["created_at_utc"])

    def test_revision_preserves_prior_and_identity(self):
        first = self.record()
        self.scenario["score"] = 71
        revised = self.record("revision", kind="HYPOTHESIS_REVISED", predecessor_version_id=first["version_id"])
        self.assertNotEqual(first["version_id"], revised["version_id"])
        self.assertEqual(self.d2.projection()["versions"][first["version_id"]], first)
        self.assertEqual(revised["scenario"]["primary"]["id"], "primary")

    def test_nested_bullish_does_not_replace_primary(self):
        result = self.evaluate(self.record())
        self.assertEqual(result["technical_context"]["direction"], "bearish")
        self.assertEqual(result["technical_context"]["nested"][0]["direction"], "bullish")

    def test_primary_reversal_requires_technical_authority(self):
        first = self.record()
        self.scenario["direction"] = self.scenario["primary"]["direction"] = "bullish"
        with self.assertRaisesRegex(LedgerError, "authorization"):
            self.record("revision", kind="HYPOTHESIS_REVISED", predecessor_version_id=first["version_id"])
        revised = self.record("revision", kind="HYPOTHESIS_REVISED", predecessor_version_id=first["version_id"],
                              authority=dict(self.authority, direction_change_authorized=True))
        self.assertEqual(revised["scenario"]["direction"], "bullish")

    def test_news_not_technical_authority(self):
        with self.assertRaisesRegex(LedgerError, "technical authority"):
            self.record(authority=dict(kind="news", engine_version="news-v1"))

    def test_primary_invalidation_wait_no_permission_mutation(self):
        first = self.record()
        self.scenario["primary"]["state"] = "invalidated"
        version = self.record("invalid", kind="HYPOTHESIS_INVALIDATED", predecessor_version_id=first["version_id"])
        result = self.evaluate(version)
        self.assertEqual(result["action"], "WAIT")
        self.assertEqual(result["knowledge"], "KNOWN")
        self.assertEqual(result["technical_context"]["permissions"], first["scenario"]["permissions"])

    def test_nested_invalidation_does_not_invalidate_primary(self):
        first = self.record()
        self.scenario["nested"][0]["state"] = "invalidated"
        version = self.record("invalid", kind="HYPOTHESIS_INVALIDATED", hypothesis_id="nested", predecessor_version_id=first["version_id"])
        self.assertEqual(self.evaluate(version)["action"], "RECORDED")

    def test_invalidation_requires_explicit_snapshot(self):
        first = self.record()
        with self.assertRaisesRegex(LedgerError, "invalidate"):
            self.record("invalid", kind="HYPOTHESIS_INVALIDATED", predecessor_version_id=first["version_id"])

    def test_legacy_formation_unknown_through_revision(self):
        first = self.record(kind="SCENARIO_OBSERVED_EXISTING")
        self.assertIsNone(first["formation_at_utc"])
        self.assertEqual(first["formation_authority"], "UNKNOWN")
        revised = self.record("revision", kind="HYPOTHESIS_REVISED", predecessor_version_id=first["version_id"])
        self.assertIsNone(revised["formation_at_utc"])

    def test_evidence_correction_does_not_rewrite_evaluation(self):
        version = self.record()
        before = self.evaluate(version)
        selected = before["manifest"]["inputs"]["usd"]["selected"]["version"]["version_id"]
        self.clock.later()
        self.evidence("usd", {"new": "correction"}, predecessor=selected)
        self.assertEqual(self.evaluate(version), before)
        fresh_evaluation = self.evaluate(version, "second-evaluation")
        self.assertEqual(fresh_evaluation["manifest"], before["manifest"])

    def test_missing_mandatory_unknown_wait(self):
        self.refs["usd"] = None
        result = self.evaluate(self.record())
        self.assertEqual((result["knowledge"], result["action"]), ("UNKNOWN", "WAIT"))
        self.assertEqual(result["assessments"]["usd"]["reason"], "INPUT_NOT_SUPPLIED")

    def test_optional_news_missing_is_neutral(self):
        result = self.evaluate(self.record())
        self.assertEqual((result["knowledge"], result["action"], result["news_status"]), ("KNOWN", "RECORDED", "not_evaluated"))

    def test_stale_usd_yields_volume(self):
        self.clock.later()
        self.cutoff = self.d1.cutoff(self.clock()["at_utc"])
        result = self.evaluate(self.record())
        for role in ("usd", "yields", "volume"):
            self.assertEqual(result["assessments"][role]["reason"], "STALE")
            self.assertEqual(result["assessments"][role]["age_seconds"], 600)

    def test_source_specific_thresholds(self):
        self.clock.later()
        self.cutoff = self.d1.cutoff(self.clock()["at_utc"])
        for role in ROLES:
            self.policy["inputs"][role]["max_age_seconds"] = 900 if role != "usd" else 10
        result = self.evaluate(self.record())
        self.assertEqual(result["assessments"]["usd"]["reason"], "STALE")
        self.assertIsNone(result["assessments"]["yields"]["reason"])

    def test_missing_age_anchor_unknown(self):
        self.policy["inputs"]["volume"]["age_anchor"] = "observation_end"
        result = self.evaluate(self.record())
        self.assertEqual(result["assessments"]["volume"]["reason"], "AGE_ANCHOR_UNKNOWN")

    def test_conflicting_news_preserves_full_technical_payload(self):
        news = self.news("conflicts")
        version = self.record()
        result = self.evaluate(version, news=news)
        self.assertEqual(result["technical_context"], self.scenario)
        self.assertEqual((result["knowledge"], result["action"]), ("KNOWN", "WAIT"))

    def test_supports_cannot_grant_permissions(self):
        news = self.news("supports")
        result = self.evaluate(self.record(), news=news)
        self.assertEqual(result["technical_context"], self.scenario)
        self.assertFalse(result["technical_context"]["permissions"]["entry"])

    def test_insufficient_news_cannot_mutate_permissions(self):
        news = self.news("insufficient_data")
        self.policy["inputs"]["news"]["required"] = True
        result = self.evaluate(self.record(), news=news)
        self.assertEqual(result["knowledge"], "UNKNOWN")
        self.assertIn("INSUFFICIENT_INTERPRETATION", [r["reason"] for r in result["uncertainty"]])
        self.assertEqual(result["technical_context"], self.scenario)

    def test_stale_news_not_evaluated(self):
        news = self.news("supports")
        self.clock.later()
        self.cutoff = self.d1.cutoff(self.clock()["at_utc"])
        self.assertEqual(self.evaluate(self.record(), news=news)["news_status"], "not_evaluated")

    def test_future_news_excluded_at_frozen_cutoff(self):
        cutoff = deepcopy(self.cutoff)
        news = self.news("supports")
        self.cutoff = cutoff
        version = self.record()
        self.assertIsNone(version["manifest"]["inputs"]["news"]["selected"])
        with self.assertRaisesRegex(LedgerError, "selected D1"):
            self.evaluate(version, news=news)

    def test_new_interpretation_cannot_attach_to_old_payload(self):
        news = self.news("supports")
        version = self.record()
        news["interpretation"]["status"] = "conflicts"
        with self.assertRaisesRegex(LedgerError, "frozen evidence"):
            self.evaluate(version, news=news)

    def test_unknown_fields_and_input_mutation_isolation(self):
        version = self.record()
        original = deepcopy(version)
        version["scenario"]["unknown_extension"] = "changed"
        self.scenario["permissions"]["entry"] = True
        self.assertEqual(self.d2.projection()["versions"][original["version_id"]], original)

    def test_same_time_order_and_duplicate_replay(self):
        first = self.record()
        before = self.d2.events()
        self.assertEqual(self.record(), first)
        self.assertEqual(before, self.d2.events())
        result = self.evaluate(first)
        before = self.d2.events()
        self.assertEqual(self.evaluate(first), result)
        self.assertEqual(before, self.d2.events())
        self.assertEqual([e["sequence"] for e in before], list(range(1, len(before) + 1)))

    def test_evaluation_key_conflict(self):
        version = self.record()
        self.evaluate(version)
        before = self.d2.events()
        with self.assertRaisesRegex(LedgerError, "conflict"):
            self.evaluate(version, engine_version="another")
        self.assertEqual(before, self.d2.events())

    def test_scenario_key_conflict(self):
        self.record()
        self.scenario["score"] += 1
        with self.assertRaisesRegex(LedgerError, "conflict"):
            self.record()

    def test_policy_content_conflict(self):
        version = self.record()
        self.evaluate(version)
        self.policy["inputs"]["usd"]["max_age_seconds"] += 1
        with self.assertRaisesRegex(LedgerError, "policy ID"):
            self.evaluate(version, "second")

    def test_production_policy_rejected(self):
        version = self.record()
        self.policy["classification"] = "production"
        with self.assertRaisesRegex(LedgerError, "synthetic"):
            self.evaluate(version)

    def test_missing_policy_role_rejected(self):
        del self.policy["inputs"]["yields"]
        with self.assertRaisesRegex(LedgerError, "synthetic"):
            self.evaluate(self.record())

    def test_scenario_transaction_rollback(self):
        def crash():
            raise RuntimeError("synthetic crash")
        with self.assertRaisesRegex(RuntimeError, "crash"):
            self.record(before_commit=crash)
        self.assertEqual(self.d2.events(), [])

    def test_evaluation_and_policy_atomic_rollback(self):
        version = self.record()
        before = self.d2.events()
        def crash():
            raise RuntimeError("synthetic crash")
        with self.assertRaisesRegex(RuntimeError, "crash"):
            self.evaluate(version, before_commit=crash)
        self.assertEqual(before, self.d2.events())
        self.assertEqual(self.d2.projection()["policies"], {})

    def test_restart_rebuild_exact(self):
        version = self.record()
        self.evaluate(version)
        before = self.d2.projection()
        events = self.d2.events()
        self.d2.close()
        self.d2 = self.open()
        self.assertEqual(self.d2.projection(), before)
        self.assertEqual(self.d2.events(), events)

    def test_full_prefix_equivalence(self):
        version = self.record()
        self.evaluate(version)
        seq = len(self.d2.events())
        prefix = self.d2.projection(seq)
        events = self.d2.events(seq)
        self.clock.later()
        prior = version["manifest"]["inputs"]["usd"]["selected"]["version"]["version_id"]
        self.evidence("usd", {"price_after": 999}, prior)
        self.cutoff = self.d1.cutoff(self.clock()["at_utc"])
        revised = self.record("revision", kind="HYPOTHESIS_REVISED", predecessor_version_id=version["version_id"])
        self.evaluate(revised, "later")
        self.assertEqual(self.d2.projection(seq), prefix)
        self.assertEqual(self.d2.events(seq), events)

    def test_hypothesis_identity_removal_rejected(self):
        first = self.record()
        self.scenario["nested"] = []
        with self.assertRaisesRegex(LedgerError, "identities"):
            self.record("revision", kind="HYPOTHESIS_REVISED", predecessor_version_id=first["version_id"])

    def test_new_nested_hypothesis_has_own_formation(self):
        first = self.record()
        self.clock.later()
        self.scenario["nested"].append(dict(id="new-nested", direction="bullish", state="candidate", parent_id="primary"))
        version = self.record("nested-formation", kind="HYPOTHESIS_FORMED", hypothesis_id="new-nested",
                              predecessor_version_id=first["version_id"])
        self.assertEqual(version["formation_at_utc"], first["formation_at_utc"])
        self.assertNotEqual(version["hypothesis_history"]["new-nested"]["formed_at_utc"], first["formation_at_utc"])
        self.assertIsNone(version["hypothesis_history"]["nested"]["formed_at_utc"])

    def test_legacy_cannot_be_reformed(self):
        version = self.record(kind="SCENARIO_OBSERVED_EXISTING")
        with self.assertRaisesRegex(LedgerError, "newly formed"):
            self.record("false-formation", kind="HYPOTHESIS_FORMED", predecessor_version_id=version["version_id"])

    def test_new_nested_does_not_certify_legacy_primary_formation(self):
        first = self.record(kind="SCENARIO_OBSERVED_EXISTING")
        self.scenario["nested"].append(dict(id="new", direction="bullish", state="candidate", parent_id="primary"))
        version = self.record("new", kind="HYPOTHESIS_FORMED", hypothesis_id="new", predecessor_version_id=first["version_id"])
        self.assertIsNone(version["formation_at_utc"])
        self.assertEqual(version["formation_authority"], "UNKNOWN")

    def test_future_technical_point_rejected(self):
        self.scenario["point_b"]["time"] = "2026-10-22T00:00:00Z"
        with self.assertRaisesRegex(LedgerError, "future technical"):
            self.record()

    def test_source_provenance_policy_unknown(self):
        self.policy["inputs"]["usd"]["accepted_verification_methods"] = ["synthetic_attestation"]
        result = self.evaluate(self.record())
        self.assertEqual(result["assessments"]["usd"]["reason"], "PROVENANCE_NOT_ACCEPTED")
        self.assertEqual(result["knowledge"], "UNKNOWN")

    def test_source_identity_policy_unknown(self):
        self.policy["inputs"]["yields"]["source_id"] = "synthetic:different-source"
        result = self.evaluate(self.record())
        self.assertEqual(result["assessments"]["yields"]["reason"], "SOURCE_POLICY_MISMATCH")

    def test_required_unevaluated_news_unknown(self):
        news = self.news("not_evaluated")
        self.policy["inputs"]["news"]["required"] = True
        result = self.evaluate(self.record(), news=news)
        self.assertEqual((result["knowledge"], result["action"]), ("UNKNOWN", "WAIT"))

    def test_every_prefix_replayed_without_future_operations(self):
        def replay(path, stop):
            ledger = self.open(path=path)
            version = None
            snapshots = []
            try:
                for i in range(stop):
                    if i % 2 == 0:
                        version = ledger.record(str(i), kind="HYPOTHESIS_FORMED" if i == 0 else "HYPOTHESIS_REVISED",
                                                scenario_id="scenario1", scenario=dict(self.scenario, score=70+i),
                                                cutoff=self.cutoff, refs=self.refs, authority=self.authority,
                                                predecessor_version_id=version["version_id"] if version else None)
                    else:
                        ledger.evaluate(str(i), scenario_version_id=version["version_id"], policy=self.policy,
                                        engine_version="synthetic:eval")
                    snapshots.append(dict(events=ledger.events(), projection=ledger.projection()))
                return snapshots
            finally:
                ledger.close()
        full = replay(self.root / "full.db", 6)
        for stop in range(1, 7):
            self.assertEqual(replay(self.root / f"prefix{stop}.db", stop), full[:stop])

    def test_parent_cycle_rejected(self):
        self.scenario["primary"]["parent_id"] = "nested"
        with self.assertRaisesRegex(LedgerError, "parent graph"):
            self.record()

    def test_nonlatest_revision_rejected(self):
        first = self.record()
        self.record("revision", kind="HYPOTHESIS_REVISED", predecessor_version_id=first["version_id"])
        with self.assertRaisesRegex(LedgerError, "latest"):
            self.record("fork", kind="HYPOTHESIS_REVISED", predecessor_version_id=first["version_id"])

    def test_future_cutoff_rejected(self):
        self.cutoff["as_of_utc"] = "2027-01-01T00:00:00Z"
        with self.assertRaisesRegex(LedgerError, "future"):
            self.record()

    def test_uncommitted_sequence_rejected(self):
        self.cutoff["maximum_visible_ledger_sequence"] += 1
        with self.assertRaisesRegex(LedgerError, "uncommitted"):
            self.record()

    def test_clock_rollback_rejected(self):
        version = self.record()
        self.clock.value["at_utc"] = "2026-10-21T09:59:00Z"
        with self.assertRaises(LedgerError):
            self.evaluate(version)

    def test_external_evidence_loss_unknown_no_current_fallback(self):
        self.evidence("news", {"status": "supports"}, external=True)
        self.cutoff = self.d1.cutoff(self.clock()["at_utc"])
        self.policy["inputs"]["news"]["required"] = True
        version = self.record()
        (self.root / "news.json").unlink()
        result = self.evaluate(version)
        self.assertEqual(result["knowledge"], "UNKNOWN")
        self.assertIsNotNone(result["original_manifest"]["inputs"]["news"]["selected"])
        self.assertIsNone(result["manifest"]["inputs"]["news"]["selected"])

    def test_d1_unchanged_by_d2(self):
        before = self.d1.events()
        self.evaluate(self.record())
        self.assertEqual(before, self.d1.events())

    def test_append_only(self):
        self.record()
        for sql in ("DELETE FROM d2_events", "UPDATE d2_events SET hash='bad'"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.d2.db.execute(sql)

    def test_deterministic_replay_complete_outputs(self):
        first = self.record()
        self.evaluate(first)
        other = self.open(path=self.root / "repeat.db")
        try:
            version = other.record("formation", kind="HYPOTHESIS_FORMED", scenario_id="scenario1",
                                   scenario=self.scenario, cutoff=self.cutoff, refs=self.refs, authority=self.authority)
            other.evaluate("evaluation", scenario_version_id=version["version_id"], policy=self.policy,
                           engine_version="synthetic:evaluator-v1")
            self.assertEqual(other.events(), self.d2.events())
            self.assertEqual(other.projection(), self.d2.projection())
        finally:
            other.close()

    def test_concurrent_duplicate_evaluation(self):
        version = self.record()
        def evaluate(_):
            d1 = Ledger(self.root / "d1.db", clock=self.clock, policy=self.cp, private_root=self.root)
            d2 = self.open(evidence=d1)
            try:
                return d2.evaluate("parallel", scenario_version_id=version["version_id"], policy=self.policy, engine_version="synthetic:evaluator-v1")
            finally:
                d2.close()
                d1.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(evaluate, range(2)))
        self.assertEqual(a, b)
        self.assertEqual(len(self.d2.projection()["evaluations"]), 1)

    def test_process_crash_before_commit(self):
        self.crash(False)

    def test_process_crash_after_commit(self):
        self.crash(True)

    def crash(self, after):
        version = self.record()
        script = """
import json, os, sys
from pathlib import Path
from offline_evidence_ledger import Ledger
from offline_scenario_ledger import ScenarioLedger
root, cp, clock, vid, policy, after = json.loads(sys.argv[1])
root = Path(root)
d1 = Ledger(root/'d1.db', private_root=root, policy=cp, clock=lambda: clock)
d2 = ScenarioLedger(root/'d2.db', private_root=root, evidence=d1, clock_policy=cp, clock=lambda: clock)
d2.evaluate('crash-eval', scenario_version_id=vid, policy=policy, engine_version='synthetic:eval',
            before_commit=None if after else lambda: os._exit(73))
os._exit(73)
"""
        result = subprocess.run([sys.executable, "-B", "-c", script,
                                 json.dumps([str(self.root), self.cp, self.clock(), version["version_id"], self.policy, after])],
                                cwd=Path(__file__).parent, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 73, result.stderr)
        self.d2.close()
        self.d2 = self.open()
        self.assertEqual(len(self.d2.projection()["evaluations"]), int(after))
        self.assertEqual(len(self.d2.projection()["policies"]), int(after))


if __name__ == "__main__":
    unittest.main()
