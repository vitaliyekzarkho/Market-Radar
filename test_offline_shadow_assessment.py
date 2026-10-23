"""D5 general and focused XAU cases; prices are synthetic artifact-inspired examples."""
import ast
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import offline_shadow_assessment as d5
from offline_shadow_boundary import canonical, fingerprint, sha
from offline_evidence_ledger import Ledger
from offline_scenario_ledger import ScenarioLedger, ROLES
from offline_notification_audit import NotificationAudit

FIXTURE = Path(__file__).parent / "tests/fixtures/d5/base.json"


def fresh():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def seal(case):
    for step in case["steps"]:
        b = step["snapshot"]
        b["payload"]["d3"]["evaluation"]["technical_context_hash"] = fingerprint(b["payload"]["d2"]["scenario"])
        b["payload"]["d3"]["evaluation"]["manifest_fingerprint"] = fingerprint(b["payload"]["d2"]["evidence_ref"])
        b["envelope"]["content_hash"] = sha(canonical(b["payload"]).encode())
        b["envelope"]["raw_payload_ref"] = "sha256:" + b["envelope"]["content_hash"]
        step["snapshot_hash"] = fingerprint(b)
    return canonical(case).encode()


def second(case):
    first = case["steps"][0]
    nxt = deepcopy(first)
    nxt.update(event_id="synthetic:event-2", sequence=2)
    b = nxt["snapshot"]
    nxt["cutoff"]["as_of_utc"] = "2026-10-21T10:00:20Z"
    b["cutoff"] = deepcopy(nxt["cutoff"])
    b["clock"].update(at_utc="2026-10-21T10:00:21Z", monotonic=110)
    b["envelope"].update(snapshot_id="synthetic:snapshot-2", observation_start_utc="2026-10-21T10:00:18Z",
        observation_end_utc="2026-10-21T10:00:18Z", published_at_utc="2026-10-21T10:00:18Z", claimed_available_at_utc="2026-10-21T10:00:19Z")
    d = b["payload"]["d2"]
    old = first["snapshot"]["payload"]["d2"]
    d.update(scenario_version_id="synthetic:scenario-v2", event_type="HYPOTHESIS_REVISED", created_at_utc="2026-10-21T10:00:16Z",
             predecessor=dict(version_id=old["scenario_version_id"], scenario_id=old["scenario_id"], scenario=deepcopy(old["scenario"]), cutoff=deepcopy(old["cutoff"])))
    d["cutoff"]["as_of_utc"] = "2026-10-21T10:00:15Z"
    d["evidence_ref"]["cutoff"] = deepcopy(d["cutoff"])
    ev = b["payload"]["d3"]["evaluation"]
    ev.update(evaluation_id="synthetic:evaluation-2", scenario_version_id=d["scenario_version_id"], evaluated_at_utc="2026-10-21T10:00:17Z", cutoff=deepcopy(d["cutoff"]))
    nxt["transition"] = dict(event_id=nxt["event_id"], predecessor_event_id=first["event_id"], at_utc="2026-10-21T10:00:15Z",
                             sequence=2, evidence_ids=[nxt["evidence"][0]["id"]], causal_reason="synthetic technical revision",
                             classification="SCENARIO_REVISION")
    case["steps"].append(nxt)
    return nxt


def target_change(case):
    nxt = second(case)
    s = nxt["snapshot"]["payload"]["d2"]["scenario"]
    original = deepcopy(s["point_b"])
    s["point_b"] = dict(price=4019.92, time="2026-10-21T10:00:14Z", definition="synthetic liquidity cluster")
    nxt["targets"]["current_point_b"] = deepcopy(s["point_b"])
    proof = deepcopy(nxt["evidence"][0]); proof.update(id="synthetic:target-proof", item_id="target", sequence=3)
    proof["payload"] = dict(target=original, target_state="reached")
    proof["content_hash"] = fingerprint(proof["payload"])
    nxt["evidence"].append(proof)
    nxt["targets"]["history"] = [dict(target=original, state="reached", evidence_id=proof["id"], at_utc="2026-10-21T10:00:14Z")]
    nxt["snapshot"]["payload"]["d2"]["authority"]["target_change_authorized"] = True
    return nxt


class CaseAssertions:
    def setUp(self):
        self.case = fresh()

    def run_case(self, raw=None, **kwargs):
        r = d5.replay(raw if raw is not None else seal(self.case), **kwargs).report
        self.assertTrue(r["timeline"], r)
        return r

    def codes(self, result=None):
        r = result or self.run_case()
        return {d["reason_code"] for d in r["discrepancies"]}

    def has(self, code):
        self.assertIn(code, self.codes())

    def clean_transition(self):
        r = self.run_case()
        self.assertFalse([d for d in r["discrepancies"] if d["classification"] != "EVIDENCE_GAP"], r)
        return r


class GeneralReplayTests(CaseAssertions, unittest.TestCase):
    def test_stable_primary(self):
        self.assertEqual(self.run_case()["timeline"][0]["direction"], "bearish")

    def test_nested_rebound(self):
        r = self.run_case()["timeline"][0]
        self.assertTrue(r["nested_refs"])
        self.assertEqual(r["direction"], "bearish")

    def test_rebound_then_bearish_continuation(self):
        n = second(self.case); n["snapshot"]["payload"]["d2"]["scenario"]["nested"][0]["state"] = "invalidated"
        self.assertEqual(self.clean_transition()["timeline"][-1]["direction"], "bearish")

    def test_valid_primary_reversal(self):
        n = second(self.case); d = n["snapshot"]["payload"]["d2"]
        d["scenario"]["direction"] = d["scenario"]["primary"]["direction"] = "bullish"
        d["authority"]["direction_change_authorized"] = True
        self.clean_transition()

    def test_unauthorized_reversal(self):
        n = second(self.case); d = n["snapshot"]["payload"]["d2"]
        d["scenario"]["direction"] = d["scenario"]["primary"]["direction"] = "bullish"
        self.has("UNAUTHORIZED_DIRECTION_REVERSAL")

    def test_silent_point_a(self):
        n = second(self.case); n["snapshot"]["payload"]["d2"]["scenario"]["point_a"]["price"] = 4200
        n["transition"] = None
        self.has("CHANGE_WITHOUT_CAUSAL_AVAILABLE_EVIDENCE")

    def test_silent_point_b(self):
        n = target_change(self.case); n["snapshot"]["payload"]["d2"]["scenario_version_id"] = "synthetic:scenario-v1"
        self.has("MATERIAL_CHANGE_WITHOUT_NEW_VERSION_AND_PREDECESSOR")

    def test_new_id_without_formation(self):
        n = second(self.case); n["snapshot"]["payload"]["d2"]["scenario"]["nested"].append(dict(id="new", direction="bullish", parent_id="primary"))
        self.has("NEW_HYPOTHESIS_WITHOUT_TARGETED_FORMATION")

    def test_new_id_valid_formation(self):
        n = second(self.case); d = n["snapshot"]["payload"]["d2"]
        d["scenario"]["nested"].append(dict(id="new", direction="bullish", parent_id="primary"))
        d.update(event_type="HYPOTHESIS_FORMED", hypothesis_id="new", formation_at_utc=d["created_at_utc"])
        self.clean_transition()

    def test_delayed_macro(self):
        self.case["steps"][0]["evidence"][0]["available_at_utc"] = "2026-10-21T11:00:00Z"
        self.assertEqual(self.run_case()["timeline"][0]["evidence"][0]["reason"], "NOT_YET_AVAILABLE")

    def test_usd_yields_disagreement(self):
        e = deepcopy(self.case["steps"][0]["evidence"][0]); e.update(id="yields", item_id="yields", relation="conflicts")
        self.case["steps"][0]["evidence"].append(e)
        r = self.run_case()["timeline"][0]
        self.assertTrue(r["supporting"] and r["contradicting"])

    def test_stale_volume(self):
        self.case["steps"][0]["evidence"][0]["max_age_seconds"] = 0
        self.assertEqual(self.run_case()["timeline"][0]["evidence"][0]["reason"], "STALE_EVIDENCE")

    def test_missing_availability(self):
        self.case["steps"][0]["evidence"][0]["available_at_utc"] = None
        self.assertTrue(self.run_case()["timeline"][0]["missing"])

    def test_correction_after_cutoff(self):
        n = second(self.case); e = n["evidence"][0]
        e.update(id="correction", revision="2", predecessor_id="synthetic:technical-proof", available_at_utc="2026-10-21T11:00:00Z")
        r = self.run_case()["timeline"]
        self.assertEqual(r[0]["evidence_prefix_fingerprint"], r[1]["evidence_prefix_fingerprint"])

    def test_equal_timestamp_sequence(self):
        e = self.case["steps"][0]["evidence"][0]; e["available_at_utc"] = self.case["steps"][0]["cutoff"]["as_of_utc"]
        e["sequence"] = 11
        self.assertTrue(self.run_case()["timeline"][0]["missing"])

    def test_target_reached_not_reversal(self):
        target_change(self.case)
        self.assertEqual(self.clean_transition()["timeline"][-1]["direction"], "bearish")

    def test_fta_without_revision(self):
        n = second(self.case); d = n["snapshot"]["payload"]["d2"]
        d["scenario"]["FTA"] = 4058.97; d["scenario_version_id"] = "synthetic:scenario-v1"
        self.has("MATERIAL_CHANGE_WITHOUT_NEW_VERSION_AND_PREDECESSOR")

    def test_permission_without_authority(self):
        n = second(self.case); n["snapshot"]["payload"]["d2"]["scenario"]["permissions"]["entry"] = True
        self.has("TECHNICAL_PERMISSION_CHANGED_WITHOUT_AUTHORITY")

    def test_duplicate_candidate_not_authorization(self):
        second(self.case); self.has("DUPLICATE_CANDIDATE_REQUIRES_D3")

    def test_material_candidate_no_ledger(self):
        target_change(self.case)
        self.assertEqual(self.run_case()["timeline"][-1]["notification"]["status"], "UNKNOWN")

    def test_resolution_without_authority(self):
        self.case["steps"][0]["snapshot"]["payload"]["d3"]["event_type"] = "RESOLVED"
        self.has("AUTHORITATIVE_RECORD_MISSING")

    def test_expiry_without_authority(self):
        self.case["steps"][0]["snapshot"]["payload"]["d3"]["event_type"] = "EXPIRED"
        self.assertEqual(self.run_case()["timeline"][0]["notification"]["status"], "UNKNOWN")

    def test_unknown_notification(self):
        self.assertFalse(self.run_case()["timeline"][0]["notification"]["delivery_authorized"])

    def test_corrupt_snapshot(self):
        seal(self.case); self.case["steps"][0]["snapshot_hash"] = "0" * 64
        self.assertIn("SNAPSHOT_HASH_MISMATCH", self.codes(self.run_case(canonical(self.case).encode())))

    def test_d4_schema_incompatible(self):
        self.case["steps"][0]["snapshot"]["envelope"]["schema_version"] = "unknown"
        self.has("D4_NOT_SHADOW_READY")

    def test_no_d1_membership_fabricated(self):
        r = self.run_case()["timeline"][0]
        self.assertEqual(r["candidate_status"], "SHADOW_READY")
        self.assertEqual(r["authority_scope"], "UNAVAILABLE")

    def test_d2_lifecycle_mismatch(self):
        n = second(self.case); n["snapshot"]["payload"]["d2"]["scenario"]["nested"] = []
        self.has("HYPOTHESIS_ID_REMOVED")

    def test_d3_policy_claim_mismatch(self):
        self.case["steps"][0]["claimed_notification_status"] = "ELIGIBLE"
        self.has("NOTIFICATION_CLAIM_UNSUPPORTED")

    def test_interrupted_replay_recompute(self):
        target_change(self.case); raw = seal(self.case)
        first = d5.replay(raw, stop_after=1).report["timeline"]
        full = d5.replay(raw).report["timeline"]
        self.assertEqual(first, full[:1])

    def test_fresh_process_replay(self):
        raw = seal(self.case)
        code = "import sys;from offline_shadow_assessment import replay;print(replay(sys.stdin.buffer.read()).report_json)"
        result = subprocess.check_output([sys.executable, "-B", "-c", code], input=raw, cwd=Path(__file__).parent)
        self.assertEqual(json.loads(result), d5.replay(raw).report)

    def test_independent_prefix(self):
        target_change(self.case); full = self.run_case()["timeline"]
        self.case["steps"] = self.case["steps"][:1]
        self.assertEqual(self.run_case()["timeline"], full[:1])

    def test_immutable_report_after_mutation(self):
        r = d5.replay(seal(self.case)); saved = r.report_json
        self.case["steps"][0]["snapshot"]["payload"]["d2"]["scenario"]["direction"] = "bullish"
        returned = r.report; returned["timeline"].clear()
        self.assertEqual(saved, r.report_json)
        self.assertTrue(r.report["timeline"])


class XauFocusedTests(CaseAssertions, unittest.TestCase):
    def test_01_complete_target_lineage(self):
        target_change(self.case); self.clean_transition()

    def test_02_missing_predecessor(self):
        n = target_change(self.case); n["snapshot"]["payload"]["d2"]["predecessor"] = None
        self.has("MATERIAL_CHANGE_WITHOUT_NEW_VERSION_AND_PREDECESSOR")

    def test_03_after_fact_target(self):
        n = target_change(self.case); n["transition"]["at_utc"] = "2026-10-21T11:00:00Z"
        self.has("CHANGE_WITHOUT_CAUSAL_AVAILABLE_EVIDENCE")

    def test_04_reached_not_breached(self):
        n = target_change(self.case); n["targets"]["history"][0]["state"] = "breached"
        self.has("TARGET_BREACH_NOT_CONFIRMED")

    def test_05_dynamic_fta_preserves_b(self):
        n = second(self.case); n["snapshot"]["payload"]["d2"]["scenario"]["FTA"] = 4058.97
        n["transition"]["classification"] = "DYNAMIC_FTA_UPDATE"
        r = self.clean_transition()["timeline"][-1]["transition"]
        self.assertEqual(r["structural_point_b_before"], r["structural_point_b_after"])

    def test_06_fta_without_evidence(self):
        n = second(self.case); n["snapshot"]["payload"]["d2"]["scenario"]["FTA"] = 4058.97
        n["transition"]["evidence_ids"] = []
        self.has("CHANGE_WITHOUT_CAUSAL_AVAILABLE_EVIDENCE")

    def test_07_material_same_minute_requires_d3(self):
        target_change(self.case)
        self.assertEqual(self.clean_transition()["timeline"][-1]["notification"]["status"], "UNKNOWN")

    def test_08_duplicate_same_minute_candidate(self):
        second(self.case); self.has("DUPLICATE_CANDIDATE_REQUIRES_D3")

    def test_09_unknown_order(self):
        n = second(self.case); n["sequence"] = 1
        self.assertEqual(d5.replay(seal(self.case)).report["reasons"], ["REPLAY_ORDER_UNPROVEN"])

    def test_10_title_not_material(self):
        n = second(self.case); n["title"] = "route updated"
        self.has("DUPLICATE_CANDIDATE_REQUIRES_D3")

    def obs(self, last):
        return dict(last=last, observed_at_utc="2026-10-21T10:00:00Z", available_at_utc="2026-10-21T10:00:01Z", sequence=1)

    def zone(self, obs):
        return d5.zone_diagnostic(dict(low=4058.97, high=4059.72), obs, self.case["steps"][0]["cutoff"])

    def test_11_zone_positions(self):
        for price, expected in ((4061, "ABOVE_ZONE"), (4059, "INSIDE_ZONE"), (4055, "BELOW_ZONE")):
            r = self.zone(self.obs(price)); self.assertEqual(r["position"], expected)
            self.assertEqual(r["touch"], "UNKNOWN"); self.assertEqual(r["break_state"], "UNKNOWN")

    def test_12_intrabar_not_close(self):
        o = self.obs(4055); o["candle"] = dict(high=4061, low=4055, close=4055, end_at_utc="2026-10-21T10:00:00Z", available_at_utc="2026-10-21T10:00:01Z", closed=False)
        r = self.zone(o); self.assertEqual(r["touch"], "TOUCH_CONFIRMED"); self.assertEqual(r["break_state"], "UNKNOWN")

    def test_13_later_screenshot_no_backfill(self):
        o = self.obs(4055); o["available_at_utc"] = "2026-10-21T10:30:00Z"
        self.assertEqual(set(self.zone(o).values()), {"UNKNOWN"})

    def test_14_conditional_as_current(self):
        self.case["steps"][0]["narratives"] = [dict(kind="CURRENT_STATE", requires_trigger=True, asserted_active=True, trigger_id="H1-4179", trigger_evidence_id=None)]
        self.has("STATE_SEMANTICS_MISMATCH")

    def test_15_distinct_thresholds(self):
        ns = [dict(kind="TRANSITION_TRIGGER", price=4107.50, asserted_active=False), dict(kind="INVALIDATION_CONDITION", price=4179.07, asserted_active=False)]
        self.case["steps"][0]["narratives"] = ns
        self.assertEqual(self.run_case()["timeline"][0]["narratives_ref"], fingerprint(ns))

    def test_16_confirmed_direction_late_entry(self):
        r = self.run_case()["timeline"][0]
        self.assertEqual((r["direction"], r["entry_timing"]), ("bearish", "late"))
        self.assertTrue(r["actionable_conclusion_prohibited"])

    def test_17_local_anchor_preserves_source(self):
        n = second(self.case); n["targets"]["local_anchor"] = 4141.35
        self.clean_transition()
        n["targets"]["primary_source"] = 4141.35
        self.has("PRIMARY_SOURCE_REPLACED_BY_LOCAL_ANCHOR")

    def test_18_macro_contradictions_retained(self):
        step = self.case["steps"][0]
        for eid, relation, status, tf in (("usd", "supports", "current", "H1"), ("yields", "conflicts", "current", "H1"), ("cot", "conflicts", "current", "W1"), ("volumeD1", "neutral", "unconfirmed", "D1"), ("exhaustionH1", "conflicts", "current", "H1")):
            e = deepcopy(step["evidence"][0]); e.update(id=eid, item_id=eid, relation=relation, status=status, timeframe=tf)
            step["evidence"].append(e)
        r = self.run_case()["timeline"][0]
        self.assertEqual(len(r["contradicting"]), 3); self.assertTrue(r["missing"])

    def test_19_stale_w1_excluded(self):
        self.case["steps"][0]["evidence"][0].update(status="stale", timeframe="W1")
        self.assertFalse(self.run_case()["timeline"][0]["supporting"])

    def test_20_pending_pmi(self):
        self.case["steps"][0]["evidence"][0]["status"] = "scheduled"
        self.assertEqual(self.run_case()["timeline"][0]["evidence"][0]["reason"], "SCHEDULED_NOT_PUBLISHED")

    def test_21_4073_to_4019_valid(self):
        n = target_change(self.case)
        self.assertEqual(n["targets"]["original_point_b"]["price"], 4073.55)
        self.assertEqual(n["targets"]["current_point_b"]["price"], 4019.92)
        self.clean_transition()

    def test_22_4073_to_4019_invalid(self):
        n = target_change(self.case); n["snapshot"]["payload"]["d2"]["authority"]["target_change_authorized"] = False
        self.has("TARGET_REACHED_IS_NOT_NEW_TARGET_AUTHORITY")

    def fta_case(self):
        self.case["steps"][0]["snapshot"]["payload"]["d2"]["scenario"]["FTA"] = 4019.92
        n = second(self.case); n["snapshot"]["payload"]["d2"]["scenario"]["FTA"] = 4058.97
        n["transition"]["classification"] = "DYNAMIC_FTA_UPDATE"
        return n

    def test_23_4019_to_4058_valid(self):
        self.fta_case(); self.clean_transition()

    def test_24_4019_to_4058_unexplained(self):
        n = self.fta_case(); n["transition"] = None
        self.has("CHANGE_WITHOUT_CAUSAL_AVAILABLE_EVIDENCE")

    def test_25_two_messages_causal_order(self):
        n = self.fta_case(); n["displayed_time"] = "16:02"
        self.case["steps"][0]["displayed_time"] = "16:02"
        self.assertEqual(len(self.clean_transition()["timeline"]), 2)

    def test_26_two_messages_without_order(self):
        n = self.fta_case(); n["transition"]["predecessor_event_id"] = "missing"
        self.has("CHANGE_WITHOUT_CAUSAL_AVAILABLE_EVIDENCE")

    def test_27_earlier_report_stable(self):
        before = self.run_case()["timeline"][0]
        target_change(self.case)
        self.assertEqual(before, self.run_case()["timeline"][0])

    def test_28_full_prefix_equal(self):
        target_change(self.case); raw = seal(self.case)
        self.assertEqual(d5.replay(raw).report["timeline"][:1], d5.replay(raw, stop_after=1).report["timeline"])


class IdmProgressionTests(CaseAssertions, unittest.TestCase):
    """XAU clarification: target provenance is separate from historical availability."""
    def setUp(self):
        self.case = json.loads((FIXTURE.parent / "idm_progression.json").read_text(encoding="utf-8"))

    def idms(self):
        return [e for st in self.case["steps"] for e in st["evidence"] if e["id"] == "synthetic:d1-idm"]

    def last(self):
        return self.run_case()["timeline"][-1]

    def test_known_idm_same_hypothesis_valid_progression(self):
        r = self.clean_transition()["timeline"]
        self.assertEqual(r[-1]["transition"]["classification"], "TARGET_PROGRESSION")
        self.assertEqual(r[-1]["transition"]["historical_availability"], "SUPPORTED_SYNTHETIC")
        self.assertEqual(r[0]["primary_ref"], r[1]["primary_ref"])
        self.assertEqual(r[0]["direction"], r[1]["direction"])
        self.assertNotEqual(r[0]["scenario_ref"], r[1]["scenario_ref"])
        self.assertNotIn("TARGET_REACHED_IS_NOT_NEW_TARGET_AUTHORITY", self.codes())

    def test_missing_availability_is_unknown_not_invalid_target(self):
        for e in self.idms():
            e["available_at_utc"] = None
        r = self.last()
        self.assertEqual(r["transition"]["structural_candidate"], "DECLARED_D1_FRACTAL_IDM")
        self.assertEqual(r["transition"]["historical_availability"], "UNKNOWN")
        self.assertEqual(r["transition"]["classification"], "TARGET_PROGRESSION_AVAILABILITY_UNKNOWN")
        self.assertEqual(r["final_status"], "INSUFFICIENT_EVIDENCE")
        self.assertNotIn("CHANGE_WITHOUT_CAUSAL_AVAILABLE_EVIDENCE", self.codes())
        self.assertEqual(r["notification"]["status"], "UNKNOWN")
        self.case["scope"] = "synthetic_ledgers"
        contexts = {self.case["steps"][1]["event_id"]: dict(audit=object(), request={})}
        with patch.object(d5, "assess_ledger_notification", side_effect=AssertionError("unverified progression reached D3")):
            checked = self.run_case(ledger_context=contexts)
        self.assertEqual(checked["timeline"][-1]["notification"]["status"], "UNKNOWN")

    def test_later_available_idm_is_temporal_not_structural_failure(self):
        for e in self.idms():
            e["available_at_utc"] = "2026-10-21T10:00:18Z"
        r = self.last()
        self.assertEqual(r["transition"]["structural_candidate"], "DECLARED_D1_FRACTAL_IDM")
        self.assertEqual(r["transition"]["historical_availability"], "NOT_AVAILABLE_AT_CUTOFF")
        self.assertIn("IDM_NOT_AVAILABLE_AT_CUTOFF", self.codes())
        self.assertNotIn("UNAUTHORIZED_DIRECTION_REVERSAL", self.codes())

    def test_equal_time_wrong_sequence_not_available(self):
        for e in self.idms():
            e["sequence"] = 4
        self.has("IDM_NOT_AVAILABLE_AT_CUTOFF")

    def test_missing_establishment_chronology_unknown(self):
        for e in self.idms():
            del e["payload"]["established_at_utc"]
            e["content_hash"] = fingerprint(e["payload"])
        self.has("IDM_HISTORICAL_AVAILABILITY_UNKNOWN")
        self.assertEqual(self.last()["transition"]["historical_availability"], "UNKNOWN")

    def test_established_after_availability_rejected(self):
        for e in self.idms():
            e["payload"]["established_at_utc"] = "2026-10-21T10:00:02Z"
            e["content_hash"] = fingerprint(e["payload"])
        self.has("IDM_ESTABLISHED_AFTER_CLAIMED_AVAILABILITY")

    def test_corruption_is_not_downgraded_to_unknown_availability(self):
        for e in self.idms():
            e["content_hash"] = "0" * 64
        self.has("EVIDENCE_HASH_MISMATCH")
        self.assertNotIn("IDM_HISTORICAL_AVAILABILITY_UNKNOWN", self.codes())
        self.assertEqual(self.last()["final_status"], "CONTRACT_VIOLATION")

    def test_missing_predecessor_still_rejected(self):
        self.case["steps"][1]["snapshot"]["payload"]["d2"]["predecessor"] = None
        self.has("MATERIAL_CHANGE_WITHOUT_NEW_VERSION_AND_PREDECESSOR")

    def test_new_version_still_required(self):
        self.case["steps"][1]["snapshot"]["payload"]["d2"]["scenario_version_id"] = self.case["steps"][0]["snapshot"]["payload"]["d2"]["scenario_version_id"]
        self.has("MATERIAL_CHANGE_WITHOUT_NEW_VERSION_AND_PREDECESSOR")

    def test_progression_cannot_hide_reversal(self):
        s = self.case["steps"][1]["snapshot"]["payload"]["d2"]["scenario"]
        s["direction"] = s["primary"]["direction"] = "bullish"
        self.has("UNAUTHORIZED_DIRECTION_REVERSAL")
        self.assertNotEqual(self.last()["transition"]["classification"], "TARGET_PROGRESSION")

    def test_pwl_fta_ta_and_source_preserved(self):
        before, after = self.case["steps"]
        s = after["snapshot"]["payload"]["d2"]["scenario"]
        self.assertEqual(after["targets"]["preceding_liquidity_objective"]["price"], 4073.545)
        self.assertEqual((s["FTA"], s["TA"], s["point_b"]["price"]), (4058.97, 4044.76, 4019.92))
        self.assertEqual(before["targets"]["primary_source"], after["targets"]["primary_source"])
        self.assertEqual(after["targets"]["history"][0]["target"]["price"], 4073.545)
        self.assertEqual(self.last()["transition"]["fta_after"], fingerprint(4058.97))

    def test_no_htf_obstacle_is_declared_not_recalculated(self):
        self.assertEqual(self.idms()[0]["payload"]["htf_obstacle_claim"], "USER_REPORTED_NONE_IDENTIFIED")
        r = self.last()
        self.assertTrue(r["actionable_conclusion_prohibited"])
        self.assertEqual(r["notification"]["status"], "UNKNOWN")

    def test_later_proof_does_not_backfill_earlier_record(self):
        self.idms()[0]["available_at_utc"] = None
        raw = seal(self.case)
        full = d5.replay(raw).report["timeline"]
        prefix = deepcopy(self.case); prefix["steps"] = prefix["steps"][:1]
        self.assertEqual(full[:1], d5.replay(seal(prefix)).report["timeline"])
        self.assertEqual(full[-1]["transition"]["historical_availability"], "SUPPORTED_SYNTHETIC")
        self.assertIn(fingerprint("synthetic:d1-idm"), full[0]["missing"])

    def test_stale_idm_not_misreported_as_missing_availability(self):
        for e in self.idms():
            e["status"] = "stale"
        self.assertNotIn("IDM_HISTORICAL_AVAILABILITY_UNKNOWN", self.codes())
        self.assertNotEqual(self.last()["transition"]["classification"], "TARGET_PROGRESSION")


class EvidenceCorrectionTests(CaseAssertions, unittest.TestCase):
    def corrected(self):
        n = second(self.case)
        n["snapshot"]["payload"]["d2"]["scenario"]["point_a"]["price"] = 4200
        old = n["evidence"][0]
        replacement = deepcopy(old)
        replacement.update(id="synthetic:correction", revision="2", predecessor_id=old["id"], relation="conflicts")
        replacement["payload"] = {"corrected": True}
        replacement["content_hash"] = fingerprint(replacement["payload"])
        return n, old, replacement

    def both_orders(self, n, items):
        results = []
        for ordering in (items, list(reversed(items))):
            n["evidence"] = ordering
            results.append(self.run_case())
        self.assertEqual(results[0], results[1])
        return results[0]["timeline"][-1]

    def test_replaced_cause_rejected_in_both_orders(self):
        n, old, replacement = self.corrected()
        r = self.both_orders(n, [old, replacement])
        self.assertEqual(r["transition"]["classification"], "UNEXPLAINED_TRANSITION")
        self.assertNotIn(fingerprint(old["id"]), r["supporting"])
        self.assertIn(fingerprint(old["id"]), r["missing"])
        self.assertIn("CHANGE_WITHOUT_CAUSAL_AVAILABLE_EVIDENCE", {x["reason_code"] for x in r["discrepancies"]})

    def test_replacement_cause_accepted_in_both_orders(self):
        n, old, replacement = self.corrected()
        n["transition"]["evidence_ids"] = [replacement["id"]]
        self.assertEqual(self.both_orders(n, [old, replacement])["transition"]["classification"], "SCENARIO_REVISION")

    def test_multiple_corrections_resolve_before_selection(self):
        n, old, replacement = self.corrected()
        latest = deepcopy(replacement)
        latest.update(id="synthetic:correction-3", revision="3", predecessor_id=replacement["id"])
        n["transition"]["evidence_ids"] = [replacement["id"]]
        r = self.both_orders(n, [old, replacement, latest])
        self.assertEqual(r["transition"]["classification"], "UNEXPLAINED_TRANSITION")
        self.assertEqual(r["contradicting"], [fingerprint(latest["id"])])

    def test_stale_replacement_does_not_fall_back(self):
        n, old, replacement = self.corrected()
        replacement["status"] = "stale"
        r = self.both_orders(n, [old, replacement])
        self.assertFalse(r["supporting"] or r["contradicting"])
        self.assertEqual(r["transition"]["classification"], "UNEXPLAINED_TRANSITION")

    def test_unknown_replacement_does_not_fall_back(self):
        n, old, replacement = self.corrected()
        replacement["status"] = "unknown"
        r = self.both_orders(n, [old, replacement])
        self.assertEqual(r["transition"]["classification"], "UNEXPLAINED_TRANSITION")
        self.assertFalse(r["supporting"])

    def test_after_decision_correction_preserves_historical_cause(self):
        n, old, replacement = self.corrected()
        replacement["available_at_utc"] = "2026-10-21T10:00:18Z"
        r = self.both_orders(n, [old, replacement])
        self.assertEqual(r["transition"]["classification"], "SCENARIO_REVISION")
        self.assertNotIn(fingerprint(old["id"]), r["supporting"])

    def test_after_report_correction_does_not_supersede(self):
        n, old, replacement = self.corrected()
        replacement["available_at_utc"] = "2026-10-21T11:00:00Z"
        r = self.both_orders(n, [old, replacement])
        self.assertEqual(r["transition"]["classification"], "SCENARIO_REVISION")
        self.assertIn(fingerprint(old["id"]), r["supporting"])

    def test_correction_after_decision_sequence_preserves_cause(self):
        n, old, replacement = self.corrected()
        replacement["sequence"] = 4
        r = self.both_orders(n, [old, replacement])
        self.assertEqual(r["transition"]["classification"], "SCENARIO_REVISION")

    def test_report_staleness_does_not_rewrite_decision(self):
        n, old, replacement = self.corrected()
        for step in self.case["steps"]:
            step["evidence"][0]["max_age_seconds"] = 17
        replacement["available_at_utc"] = "2026-10-21T10:00:18Z"
        r = self.both_orders(n, [old, replacement])
        self.assertEqual(r["transition"]["classification"], "SCENARIO_REVISION")

    def test_correction_preserves_earlier_report(self):
        before = self.run_case()["timeline"][0]
        n, old, replacement = self.corrected()
        n["evidence"] = [replacement, old]
        self.assertEqual(before, self.run_case()["timeline"][0])

    def test_new_chain_in_same_batch_is_order_independent(self):
        n, old, replacement = self.corrected()
        old.update(id="synthetic:new-source", item_id="new-source")
        replacement.update(item_id=old["item_id"], predecessor_id=old["id"])
        n["transition"]["evidence_ids"] = [old["id"]]
        self.assertEqual(self.both_orders(n, [old, replacement])["transition"]["classification"], "UNEXPLAINED_TRANSITION")

    def test_duplicate_ids_rejected(self):
        n, old, replacement = self.corrected()
        replacement["id"] = old["id"]
        n["evidence"] = [old, replacement]
        self.assertEqual(d5.replay(seal(self.case)).report["reasons"], ["DUPLICATE_OR_INVALID_EVIDENCE_ID"])

    def test_cycle_rejected(self):
        n, old, replacement = self.corrected()
        old["predecessor_id"] = replacement["id"]
        n["evidence"] = [old, replacement]
        self.assertEqual(d5.replay(seal(self.case)).report["reasons"], ["CYCLIC_EVIDENCE_LINEAGE"])


class InputBoundaryTests(CaseAssertions, unittest.TestCase):
    def test_malformed_and_executable(self):
        for raw in (b"import os", b"\x80pickle", b'{"a":', b'{"x":NaN}', b'{"x":1,"x":2}'):
            self.assertEqual(d5.replay(raw).report["final_status"], "CONTRACT_VIOLATION")

    def test_size_depth_count(self):
        for raw in (b" " * (d5.MAX_BYTES + 1), b'{"a":' + b"[" * 41 + b"0" + b"]" * 41 + b"}"):
            self.assertFalse(d5.replay(raw).report["timeline"])
        self.case["steps"] *= 129
        self.assertEqual(d5.replay(seal(self.case)).report["reasons"], ["SNAPSHOT_COUNT_LIMIT"])

    def test_duplicate_submission_and_cutoff(self):
        raw = seal(self.case)
        self.assertEqual(d5.replay(raw).report_json, d5.replay(raw).report_json)

    def test_conflicting_source_revision(self):
        n = second(self.case); n["evidence"][0]["id"] = "other-same-revision"
        self.has("CONFLICTING_SOURCE_REVISION")

    def test_missing_correction_predecessor(self):
        self.case["steps"][0]["evidence"][0]["predecessor_id"] = "absent"
        self.has("CORRECTION_PREDECESSOR_MISSING")

    def test_unavailable_used(self):
        self.case["steps"][0]["evidence"][0].update(available_at_utc=None, used_as_current=True)
        self.has("UNAVAILABLE_EVIDENCE_USED_AS_CURRENT")

    def test_public_privacy(self):
        token = "SYNTHETIC_PRIVATE_SENTINEL"
        self.case["case_id"] = token
        self.case["steps"][0]["evidence"][0]["id"] = token
        self.case["steps"][0]["snapshot"]["payload"]["private"] = dict(password=token, recipient=token)
        self.case["steps"][0]["cutoff"]["private_extension"] = token
        result = d5.replay(seal(self.case))
        self.assertNotIn(token, result.report_json)
        self.assertNotIn(token, repr(result))

    def test_no_io_candidate_replay(self):
        raw = seal(self.case)
        with patch("builtins.open", side_effect=AssertionError("IO")), patch.object(Path, "open", side_effect=AssertionError("IO")):
            self.assertTrue(d5.replay(raw).report["timeline"])

    def test_no_transport_calls_in_module(self):
        tree = ast.parse(Path(d5.__file__).read_text())
        calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertFalse(calls & {"generate", "prepare", "delivery", "ingest", "publish", "record", "connect", "send", "now"})

    def test_unknown_history_gate(self):
        self.case["steps"][0]["historical_evidence"] = "user_screenshot"
        self.has("HISTORICAL_CHRONOLOGY_UNPROVEN")

    def test_entry_reset_rejected(self):
        n = second(self.case); n["entry"]["timing"] = "continuation"
        self.has("ANCHOR_CANNOT_RESET_ENTRY_TIMING")

    def test_closed_candle_diagnostic(self):
        o = dict(last=4055, observed_at_utc="2026-10-21T10:00:00Z", available_at_utc="2026-10-21T10:00:01Z", sequence=1,
            candle=dict(high=4061, low=4055, close=4055, end_at_utc="2026-10-21T10:00:00Z", available_at_utc="2026-10-21T10:00:01Z", closed=True, confirmation="synthetic_closed_candle", rule_id="synthetic:rule"))
        self.assertEqual(d5.zone_diagnostic(dict(low=4058.97, high=4059.72), o, self.case["steps"][0]["cutoff"])["break_state"], "BREAK_CONFIRMED")

    def test_cutoff_sequence_cannot_rollback(self):
        n = second(self.case); n["cutoff"]["maximum_visible_ledger_sequence"] = 2
        self.assertEqual(d5.replay(seal(self.case)).report["reasons"], ["CUTOFF_SEQUENCE_ROLLBACK"])

    def test_corrupt_later_input_does_not_change_earlier(self):
        before = self.run_case()["timeline"][0]
        n = target_change(self.case); seal(self.case); n["snapshot_hash"] = "0" * 64
        result = d5.replay(canonical(self.case).encode()).report
        self.assertEqual(result["timeline"][0], before)
        self.assertEqual(result["timeline"][0]["evidence_prefix_fingerprint"], result["timeline"][1]["evidence_prefix_fingerprint"])

    def test_catalog_matches_cases(self):
        catalog = json.loads((FIXTURE.parent / "case_catalog.json").read_text())
        expected = {"test_offline_shadow_assessment." + cls.__name__ + "." + name
                    for cls in (GeneralReplayTests, XauFocusedTests, IdmProgressionTests) for name in dir(cls) if name.startswith("test_")}
        self.assertEqual(expected, {x["test"] for x in catalog["scenarios"]})
        self.assertEqual(len(expected), 74)


class AcceptedLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clock_value = dict(at_utc="2026-10-21T10:00:00Z", session_id="synthetic:boot", monotonic=1, uncertainty_seconds=0, health_id="synthetic:healthy")
        self.clock = lambda: deepcopy(self.clock_value)
        cp = dict(classification="synthetic", policy_id="synthetic:clock", max_uncertainty_seconds=0)
        self.d1 = Ledger(self.root / "d1.db", clock=self.clock, policy=cp, private_root=self.root)
        self.d2 = ScenarioLedger(self.root / "d2.db", evidence=self.d1, clock=self.clock, clock_policy=cp, private_root=self.root)
        self.d3 = NotificationAudit(self.root / "d3.db", decisions=self.d2, clock=self.clock, clock_policy=cp, private_root=self.root)
        c = fresh(); p = c["steps"][0]["snapshot"]["payload"]
        self.scenario = deepcopy(p["d2"]["scenario"])
        self.policy = p["d3"]["policy"]
        self.refs = {r: None for r in ROLES}
        for role in ("technical", "usd", "yields", "volume"):
            raw = canonical({"synthetic": role}).encode()
            meta = dict(source_id="synthetic:"+role, item_id=role, source_revision="1", schema_version="synthetic:v1", content_hash=sha(raw),
                published=dict(at_utc=self.clock_value["at_utc"], authority="synthetic:publisher"), observed=dict(at_utc=self.clock_value["at_utc"], authority="synthetic:receipt"),
                quality="synthetic", validation_status="valid", attestation=dict(verification_method="unverified", claimed_source_identity=role, lineage=[role], uncertainty="synthetic only"))
            vid = self.d1.ingest(role, meta, payload=raw); self.d1.publish(vid)
            self.refs[role] = dict(source_id="synthetic:"+role, item_id=role)
        self.cutoff = self.d1.cutoff(self.clock_value["at_utc"])
        self.ep = dict(classification="synthetic", policy_id="synthetic:eval", wait_on_conflict=True,
            inputs={r:dict(required=r!="news", max_age_seconds=3600, age_anchor="observed", source_id="synthetic:"+r, accepted_verification_methods=["unverified"]) for r in ROLES})
        self.v = self.d2.record("first", kind="HYPOTHESIS_FORMED", scenario_id="synthetic:scenario", scenario=self.scenario, cutoff=self.cutoff, refs=self.refs, authority=dict(kind="synthetic_technical_authority", engine_version="synthetic:engine"))
        self.ev = self.d2.evaluate("first", scenario_version_id=self.v["version_id"], policy=self.ep, engine_version="synthetic:eval")
        self.req = dict(evaluation_id=self.ev["evaluation_id"], hypothesis_id="primary", episode_id="synthetic:episode", event_type="NEW", policy=self.policy,
                        recipients=dict(set_id="synthetic:audience", fingerprint=fingerprint(["synthetic:recipient"])), reason="synthetic test")

    def tearDown(self):
        self.d3.close(); self.d2.close(); self.d1.close(); self.temp.cleanup()

    def assess(self, op="test"):
        selected = self.ev["manifest"]["inputs"]["technical"]["selected"]["version"]
        return d5.assess_ledger_notification(self.d3, self.req, scenario_hash=fingerprint(self.scenario), decision_cutoff=self.cutoff,
            scenario_version_id=self.v["version_id"], evidence_version_id=selected["version_id"], evidence_content_hash=selected["metadata"]["content_hash"],
            report_cutoff=dict(as_of_utc=self.clock_value["at_utc"], maximum_visible_ledger_sequence=self.cutoff["maximum_visible_ledger_sequence"]), operation_id=op)

    def seed_notification(self):
        # Explicit synthetic historical fixture setup through D3, outside D5; never a delivery.
        a = self.d3.assess("fixture-first", **self.req)
        n = self.d3.generate("fixture-message", eligibility_id=a["eligibility_id"], message=dict(title="synthetic", summary="offline only", action_labels=["Open chart"]))
        self.req["predecessor_notification_id"] = n["notification_id"]
        return n

    def revise(self, news=None):
        self.v = self.d2.record("revision", kind="HYPOTHESIS_REVISED", scenario_id="synthetic:scenario", scenario=self.scenario,
            cutoff=self.cutoff, refs=self.refs, authority=dict(kind="synthetic_technical_authority", engine_version="synthetic:engine"), predecessor_version_id=self.v["version_id"])
        self.ev = self.d2.evaluate("revision", scenario_version_id=self.v["version_id"], policy=self.ep, engine_version="synthetic:eval", news=news)
        self.req["evaluation_id"] = self.ev["evaluation_id"]

    def add_news(self, value):
        raw = canonical(value).encode(); now = self.clock_value["at_utc"]
        meta = dict(source_id="synthetic:news", item_id="news", source_revision="1", schema_version="synthetic:v1", content_hash=sha(raw),
            published=dict(at_utc=now, authority="synthetic:publisher"), observed=dict(at_utc=now, authority="synthetic:receipt"),
            quality="synthetic", validation_status="valid", attestation=dict(verification_method="unverified", claimed_source_identity="news", lineage=["news"], uncertainty="synthetic"))
        vid = self.d1.ingest("news", meta, payload=raw); self.d1.publish(vid)
        self.refs["news"] = dict(source_id="synthetic:news", item_id="news")
        self.cutoff = self.d1.cutoff(now)
        return vid

    def test_actual_d3_eligibility(self):
        r = self.assess(); self.assertEqual(r["status"], "ELIGIBLE"); self.assertEqual(r["mode"], "ACCEPTED_SYNTHETIC_LEDGER")
        self.assertFalse(self.d3.projection()["notifications"])

    def test_same_minute_duplicate_suppression(self):
        self.seed_notification(); self.req["event_type"] = "MATERIAL_UPDATE"
        self.assertEqual(self.assess()["status"], "SUPPRESSED_DUPLICATE")

    def test_same_minute_material_update(self):
        self.seed_notification(); self.scenario["FTA"] = 4058.97; self.revise(); self.req["event_type"] = "MATERIAL_UPDATE"
        r = self.assess(); self.assertEqual(r["status"], "ELIGIBLE"); self.assertEqual(r["lifecycle_state"], "DEVELOPING")

    def test_title_is_not_material(self):
        self.seed_notification(); self.req["event_type"] = "MATERIAL_UPDATE"; self.req["reason"] = "different title"
        self.assertEqual(self.assess()["status"], "SUPPRESSED_DUPLICATE")

    def test_expired(self):
        self.seed_notification(); self.clock_value.update(at_utc="2026-10-21T10:02:00Z", monotonic=121); self.req["event_type"] = "EXPIRED"
        self.assertEqual(self.assess()["status"], "ELIGIBLE")

    def test_resolution_missing_proof(self):
        self.seed_notification(); self.req["event_type"] = "RESOLVED"
        self.assertEqual(self.assess()["reason"], "ACCEPTED_LEDGER_REJECTED")

    def test_missing_evaluation(self):
        self.req["evaluation_id"] = "missing"
        self.assertEqual(self.assess()["status"], "UNKNOWN")

    def test_policy_excluded(self):
        self.req["policy"] = deepcopy(self.policy); self.req["policy"]["allowed_roles"] = []
        self.assertEqual(self.assess()["status"], "INELIGIBLE")

    def test_idempotency(self):
        first = self.assess(); events = deepcopy(self.d3.events())
        self.assertEqual(first, self.assess()); self.assertEqual(events, self.d3.events())

    def test_no_d1_d2_mutation(self):
        before = (self.d1.events(), self.d2.events()); self.assess()
        self.assertEqual(before, (self.d1.events(), self.d2.events()))

    def test_full_replay_with_authoritative_bindings(self):
        case = fresh(); case["scope"] = "synthetic_ledgers"
        step = case["steps"][0]; b = step["snapshot"]; p = b["payload"]
        step["cutoff"]["maximum_visible_ledger_sequence"] = self.cutoff["maximum_visible_ledger_sequence"]
        b["cutoff"] = deepcopy(step["cutoff"])
        d = p["d2"]; selected = self.ev["manifest"]["inputs"]["technical"]["selected"]
        d.update(scenario_version_id=self.v["version_id"], scenario=deepcopy(self.scenario), cutoff=deepcopy(self.cutoff),
                 created_at_utc=self.clock_value["at_utc"], formation_at_utc=self.clock_value["at_utc"])
        step["formation"]["primary"] = self.clock_value["at_utc"]
        p["d1"].update(version_id=selected["version"]["version_id"], selected_version_id=selected["version"]["version_id"],
            content_hash=selected["version"]["metadata"]["content_hash"], evidence_payload={"synthetic":"technical"},
            available_at_utc=self.clock_value["at_utc"], ready_sequence=selected["ready"]["sequence"])
        p["d1"]["observed"]["at_utc"] = self.clock_value["at_utc"]
        d["evidence_ref"] = dict(version_id=p["d1"]["version_id"], content_hash=p["d1"]["content_hash"], cutoff=deepcopy(self.cutoff))
        p["d3"]["evaluation"].update(evaluation_id=self.ev["evaluation_id"], scenario_version_id=self.v["version_id"], cutoff=deepcopy(self.cutoff), evaluated_at_utc=self.ev["evaluated_at_utc"])
        # D3 audit clock is exactly the report cutoff; no future authoritative history.
        self.clock_value.update(at_utc=step["cutoff"]["as_of_utc"], monotonic=11)
        contexts = {step["event_id"]: dict(audit=self.d3, request=self.req)}
        result = d5.replay(seal(case), ledger_context=contexts).report
        self.assertEqual(result["timeline"][0]["notification"]["mode"], "ACCEPTED_SYNTHETIC_LEDGER", result)
        self.assertEqual(result["timeline"][0]["notification"]["status"], "ELIGIBLE")
        self.assertFalse(self.d3.projection()["notifications"])

    def test_deferred_d2_wait(self):
        self.ep["policy_id"] = "synthetic:required-news"
        self.ep["inputs"]["news"]["required"] = True
        self.ev = self.d2.evaluate("unknown", scenario_version_id=self.v["version_id"], policy=self.ep, engine_version="synthetic:eval")
        self.req["evaluation_id"] = self.ev["evaluation_id"]
        self.assertEqual(self.assess()["status"], "UNKNOWN")

    def test_accepted_ledger_restart_idempotent(self):
        first = self.assess()
        self.d3.close()
        cp = dict(classification="synthetic", policy_id="synthetic:clock", max_uncertainty_seconds=0)
        self.d3 = NotificationAudit(self.root / "d3.db", decisions=self.d2, clock=self.clock, clock_policy=cp, private_root=self.root)
        self.assertEqual(first, self.assess())

    def test_valid_resolution_with_frozen_proof(self):
        self.seed_notification()
        proof = dict(verified=True, relation="resolves", reason="authorized_manual")
        vid = self.add_news(dict(resolution=proof))
        self.revise()
        self.req.update(event_type="RESOLVED", resolution=dict(reason="authorized_manual", evidence_version_id=vid, attestation=proof))
        self.assertEqual(self.assess()["status"], "ELIGIBLE")

    def test_conflicting_news_deferred_by_d3(self):
        value = dict(status="conflicts", explanation="synthetic conflict")
        vid = self.add_news(value)
        self.revise(news=dict(evidence_version_id=vid, interpretation=value))
        self.assertEqual(self.assess()["status"], "DEFERRED")

    def test_d2_idm_progression_preserves_existing_hypothesis(self):
        old = deepcopy(self.v)
        self.scenario["point_b"].update(price=4019.92, definition="synthetic D1 fractal IDM")
        self.scenario["FTA"], self.scenario["TA"] = 4058.97, 4044.76
        self.revise()
        self.assertEqual(old["scenario"]["primary"]["id"], self.v["scenario"]["primary"]["id"])
        self.assertEqual(old["formation_at_utc"], self.v["formation_at_utc"])
        self.assertEqual(old["hypothesis_history"], self.v["hypothesis_history"])
        self.assertEqual(self.v["predecessor_version_id"], old["version_id"])
        self.assertEqual(self.d2.projection()["versions"][old["version_id"]], old)


if __name__ == "__main__":
    unittest.main()
