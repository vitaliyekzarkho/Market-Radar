import copy
import json
import unittest

from news_pa_context_adapter import explain_scenario, normalize_legacy_role
from story_intermarket_validation_engine import evaluate_story


def fixtures(direction="bullish"):
    scenario = {"direction": "bullish", "score": 73, "point_a": {"price": 100},
                "point_b": {"price": 110}, "targets": [105, 110],
                "conditions": {"invalidation": {"price": 98}},
                "permissions": {"entry": False, "manual_review": True},
                "route_id": "technical-route", "pa_state": {"stage": "testing"}}
    interpretation = {"story_id": "synthetic", "mechanism_candidates": [
        {"mechanism_id": "m1", "expected": {"gold": direction}}]}
    report = {"event_id": "synthetic", "event_time_utc": "2026-10-09T18:00:00Z",
              "as_of_utc": "2026-10-09T19:00:00Z", "assets": {"gold": {
                  "source": "synthetic", "symbol": "gold", "post_event": {"post_60m": {
                      "complete": True, "available": True,
                      "move_percent": 1 if direction == "bullish" else -1}}}}}
    validation = evaluate_story(interpretation, report)
    return scenario, {"available_at_utc": "2026-10-09T18:01:00Z", "payload": interpretation}, {
        "available_at_utc": "2026-10-09T19:01:00Z", "payload": validation}


def explain(s, i=None, v=None, **kwargs):
    return explain_scenario(s, i, v, as_of_utc="2026-10-09T19:05:00Z",
                            max_age_seconds=600, **kwargs)


class NewsPAContextTests(unittest.TestCase):
    def test_backdated_interpretation_rejected_with_or_without_own_event(self):
        for explicit in [False, True]:
            s, i, v = fixtures()
            i["available_at_utc"] = "2026-10-09T17:59:00Z"
            if explicit:
                i["payload"]["event_time_utc"] = "2026-10-09T18:00:14Z"
            self.assertEqual(explain(s, i, v)["news_context"]["reason"], "interpretation_precedes_event")

    def test_interpretation_own_event_not_just_validation_anchor(self):
        s, i, v = fixtures()
        i["payload"]["event_time_utc"] = "2026-10-09T18:02:00Z"
        self.assertEqual(explain(s, i, v)["news_context"]["reason"], "interpretation_precedes_event")

    def test_explicit_pre_event_hypothesis_allowed_after_observation(self):
        s, i, v = fixtures()
        i.update(available_at_utc="2026-10-09T17:59:00Z", timing_mode="pre_event_hypothesis")
        i["payload"]["event_time_utc"] = "2026-10-09T18:00:00Z"
        original = copy.deepcopy((s, i, v))
        result = explain(s, i, v)
        self.assertEqual(result["news_context"]["relation"], "supports")
        self.assertEqual(result["scenario"], s)
        self.assertEqual((s, i, v), original)
        self.assertFalse(any(result["news_context"]["permissions"].values()))
        v["available_at_utc"] = "2026-10-09T20:00:00Z"
        self.assertEqual(explain(s, i, v)["news_context"]["reason"], "future_data")

    def test_interpretation_generation_and_validation_chronology(self):
        s, i, v = fixtures()
        i["payload"]["generated_at_utc"] = "2026-10-09T18:02:00Z"
        self.assertEqual(explain(s, i, v)["news_context"]["reason"], "interpretation_generated_after_availability")
        i["available_at_utc"] = "2026-10-09T19:02:00Z"
        self.assertEqual(explain(s, i, v)["news_context"]["reason"], "validation_precedes_interpretation")

    def test_observation_anchors_checked_directly_and_in_provenance(self):
        for nested in [False, True]:
            for key, value in {"event_id": "wrong", "story_id": "wrong", "window": "post_4h",
                               "event_time_utc": "2026-10-09T17:00:00Z",
                               "as_of_utc": "2026-10-09T18:59:00Z",
                               "available_at_utc": "2026-10-09T19:02:00Z"}.items():
                with self.subTest(nested=nested, key=key):
                    s, i, v = fixtures()
                    observation = v["payload"]["mechanisms"][0]["observations"][0]
                    target = observation.setdefault("provenance", {}) if nested else observation
                    target[key] = value
                    before = copy.deepcopy((s, i, v))
                    result = explain(s, i, v)
                    self.assertEqual(result["news_context"]["relation"], "insufficient_data")
                    self.assertEqual(result["scenario"], s)
                    self.assertEqual((s, i, v), before)

    def test_matching_observation_anchors_accept_timezone_equivalence(self):
        s, i, v = fixtures()
        v["payload"]["mechanisms"][0]["observations"][0]["provenance"] = {
            "event_id": "synthetic", "story_id": "synthetic", "window": "post_60m",
            "event_time_utc": "2026-10-09T19:00:00+01:00", "as_of_utc": "2026-10-09T19:00:00Z",
            "available_at_utc": "2026-10-09T19:01:00Z"}
        self.assertEqual(explain(s, i, v)["news_context"]["relation"], "supports")

    def test_unanchored_observations_explicitly_inherit_validator_trust(self):
        s, i, v = fixtures()
        binding = explain(s, i, v)["news_context"]["provenance"]["observation_binding"]
        self.assertFalse(binding["independent_origin_verification"])
        self.assertEqual(binding["trust"], "caller_supplied_validator_output")
        del v["payload"]["interpretation"]
        self.assertEqual(explain(s, i, v)["news_context"]["reason"], "unsupported_validator_contract")

    def test_malformed_observation_time_or_provenance_fails_closed(self):
        for fields in [{"event_time_utc": "2026-10-09T18:00:00"}, {"as_of_utc": None}, {"provenance": None}]:
            s, i, v = fixtures()
            v["payload"]["mechanisms"][0]["observations"][0].update(fields)
            self.assertEqual(explain(s, i, v)["news_context"]["relation"], "insufficient_data")

    def test_four_acceptance_cases_preserve_entire_scenario_and_inputs(self):
        s, i, v = fixtures()
        _, opposed_i, opposed_v = fixtures("bearish")
        missing = copy.deepcopy(v)
        missing["payload"]["mechanisms"][0]["status"] = "insufficient_data"
        baseline = json.dumps(s, sort_keys=True).encode()
        for ip, vp, relation in [(None, None, "not_evaluated"), (i, v, "supports"),
                                  (opposed_i, opposed_v, "conflicts"), (i, missing, "insufficient_data")]:
            with self.subTest(relation=relation):
                before = copy.deepcopy((s, ip, vp))
                result = explain(s, ip, vp)
                self.assertEqual(result["news_context"]["relation"], relation)
                self.assertEqual(json.dumps(result["scenario"], sort_keys=True).encode(), baseline)
                self.assertEqual((s, ip, vp), before)
                self.assertFalse(any(result["news_context"]["permissions"].values()))
                self.assertFalse(result["news_context"]["causality_confirmed"])

    def test_returned_objects_do_not_alias_inputs(self):
        s, i, v = fixtures()
        original = copy.deepcopy((s, i, v))
        result = explain(s, i, v)
        result["scenario"]["conditions"]["invalidation"]["price"] = 0
        result["news_context"]["evidence"][0]["observations"][0]["source"] = "changed"
        self.assertEqual((s, i, v), original)

    def test_legacy_roles_are_claims_only(self):
        for role, expected in {"confirmed_driver": "supports", "correlated_driver": "supports",
                               "supports": "supports", "driver": "supports",
                               "conflicting_driver": "conflicts", "conflicts": "conflicts",
                               "mixed_driver": "insufficient_data", "unconfirmed_driver": "insufficient_data",
                               "scheduled_risk": "not_evaluated", "unknown": "not_evaluated"}.items():
            with self.subTest(role=role):
                norm = normalize_legacy_role(role)
                self.assertEqual(norm["claimed_relation"], expected)
                self.assertFalse(norm["independently_validated"])
                self.assertFalse(norm["causality_confirmed"])
                self.assertEqual(explain(fixtures()[0], legacy_role=role)["news_context"]["relation"], "not_evaluated")

    def test_legacy_support_does_not_override_independent_conflict(self):
        s, i, v = fixtures("bearish")
        self.assertEqual(explain(s, i, v, legacy_role="confirmed_driver")["news_context"]["relation"], "conflicts")

    def test_missing_validation_is_not_evaluated(self):
        s, i, _ = fixtures()
        self.assertEqual(explain(s, i)["news_context"]["relation"], "not_evaluated")

    def test_future_availability_of_either_input_is_rejected(self):
        for which in [1, 2]:
            values = fixtures()
            values[which]["available_at_utc"] = "2026-10-09T20:00:00Z"
            self.assertEqual(explain(*values)["news_context"]["reason"], "future_data")

    def test_future_observation_is_rejected(self):
        s, i, v = fixtures()
        v["payload"]["as_of_utc"] = "2026-10-09T20:00:00Z"
        self.assertEqual(explain(s, i, v)["news_context"]["reason"], "future_data")

    def test_stale_observation_not_refreshed_by_late_ingestion(self):
        s, i, v = fixtures()
        v["available_at_utc"] = "2026-10-10T19:04:00Z"
        r = explain_scenario(s, i, v, as_of_utc="2026-10-10T19:05:00Z", max_age_seconds=600)
        self.assertEqual(r["news_context"]["reason"], "stale_observation")

    def test_timezone_offsets_and_exact_cutoff(self):
        s, i, v = fixtures()
        v["available_at_utc"] = "2026-10-09T20:05:00+01:00"
        r = explain_scenario(s, i, v, as_of_utc="2026-10-09T19:05:00Z", max_age_seconds=300)
        self.assertEqual(r["news_context"]["relation"], "supports")

    def test_missing_or_naive_evidence_time(self):
        for value in [None, "bad", "2026-10-09T19:00:00"]:
            s, i, v = fixtures()
            v["available_at_utc"] = value
            self.assertEqual(explain(s, i, v)["news_context"]["relation"], "insufficient_data")

    def test_observation_cannot_postdate_availability(self):
        s, i, v = fixtures()
        v["available_at_utc"] = "2026-10-09T18:59:00Z"
        self.assertEqual(explain(s, i, v)["news_context"]["reason"], "invalid_chronology")

    def test_unfinished_or_unknown_window(self):
        for window in ["post_24h", "unknown"]:
            s, i, v = fixtures()
            v["payload"]["window"] = window
            self.assertEqual(explain(s, i, v)["news_context"]["reason"], "invalid_or_unfinished_window")

    def test_identity_mismatch(self):
        for key in ["story_id", "event_id"]:
            s, i, v = fixtures()
            v["payload"][key] = "other"
            self.assertEqual(explain(s, i, v)["news_context"]["reason"], "identity_mismatch")

    def test_failed_mechanism_does_not_imply_opposite_direction(self):
        for status in ["contradicted", "mixed", "insufficient_data", "unknown"]:
            s, i, v = fixtures()
            v["payload"]["mechanisms"][0]["status"] = status
            self.assertEqual(explain(s, i, v)["news_context"]["relation"], "insufficient_data")

    def test_duplicate_missing_or_unmatched_mechanisms(self):
        for mutation in ["duplicate", "missing", "unmatched"]:
            s, i, v = fixtures()
            mechanisms = v["payload"]["mechanisms"]
            if mutation == "duplicate": mechanisms.append(copy.deepcopy(mechanisms[0]))
            elif mutation == "missing": mechanisms.clear()
            else: mechanisms[0]["mechanism_id"] = "wrong"
            self.assertEqual(explain(s, i, v)["news_context"]["relation"], "insufficient_data")

    def test_conflicting_candidates_remain_unresolved(self):
        s, i, v = fixtures()
        _, other_i, other_v = fixtures("bearish")
        other_i["payload"]["mechanism_candidates"][0]["mechanism_id"] = "m2"
        other_v["payload"]["mechanisms"][0]["mechanism_id"] = "m2"
        i["payload"]["mechanism_candidates"].extend(other_i["payload"]["mechanism_candidates"])
        v["payload"]["mechanisms"].extend(other_v["payload"]["mechanisms"])
        self.assertEqual(explain(s, i, v)["news_context"]["relation"], "insufficient_data")

    def test_conditional_gold_is_not_directional_support(self):
        s, i, v = fixtures()
        i["payload"]["mechanism_candidates"][0]["expected"]["gold"] = "conditional"
        self.assertEqual(explain(s, i, v)["news_context"]["relation"], "insufficient_data")

    def test_nonfinite_and_missing_observations_fail_closed(self):
        for value in [float("nan"), float("inf"), True, None]:
            s, i, v = fixtures()
            v["payload"]["mechanisms"][0]["observations"][0]["move_percent"] = value
            self.assertEqual(explain(s, i, v)["news_context"]["relation"], "insufficient_data")
        s, i, v = fixtures()
        v["payload"]["mechanisms"][0]["observations"] = []
        self.assertEqual(explain(s, i, v)["news_context"]["relation"], "insufficient_data")

    def test_invalid_caller_policy_raises(self):
        for age in [-1, float("nan"), float("inf"), True]:
            with self.assertRaises(ValueError):
                explain_scenario(fixtures()[0], as_of_utc="2026-10-09T19:05:00Z", max_age_seconds=age)

    def test_revised_hypothesis_cannot_reuse_old_validation(self):
        s, i, v = fixtures()
        i["payload"]["mechanism_candidates"][0]["expected"]["gold"] = "bearish"
        self.assertEqual(explain(s, i, v)["news_context"]["relation"], "insufficient_data")

    def test_unknown_technical_direction_is_not_guessed(self):
        s, i, v = fixtures()
        s["direction"] = "neutral"
        self.assertEqual(explain(s, i, v)["news_context"]["reason"], "unknown_technical_direction")

    def test_audit_identity_window_and_bridge_provenance_preserved(self):
        s, i, v = fixtures()
        v["payload"]["provenance"] = {"reaction_report_original_event_id": "related"}
        p = explain(s, i, v)["news_context"]["provenance"]
        self.assertEqual((p["story_id"], p["event_id"], p["window"]), ("synthetic", "synthetic", "post_60m"))
        self.assertEqual(p["bridge_provenance"], v["payload"]["provenance"])


if __name__ == "__main__":
    unittest.main()
