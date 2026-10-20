import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from offline_validation import (load_fixture, replay, validate, canonical, chronology,
                                chain_input, summary)

MANIFEST = Path(__file__).parent / "tests/fixtures/c336/manifest.json"


class OfflineValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture, self.manifest = load_fixture(MANIFEST)

    def run_replay(self, **kwargs):
        return replay(self.fixture, self.root, **kwargs)

    def test_complete_validation_and_canonical_determinism(self):
        left = validate(MANIFEST, self.root)
        right = validate(MANIFEST, self.root)
        self.assertEqual(left["verdict"], "PASS")
        self.assertEqual(canonical(left), canonical(right))
        self.assertTrue(all(v == "PASS" for v in left["invariants"].values()))
        self.assertEqual(len(left["cases"]), 19)
        self.assertIn("synthetic: PASS", summary(left))

    def test_restart_after_every_operation_preserves_all_state(self):
        a = self.run_replay()
        b = self.run_replay(restart_after=[op["id"] for op in self.fixture["operations"]])
        self.assertEqual(a, b)

    def test_future_records_do_not_change_earlier_results(self):
        full = self.run_replay()
        prefix = copy.deepcopy(self.fixture)
        prefix["operations"] = prefix["operations"][:8]
        short = replay(prefix, self.root)
        self.assertEqual(short["timeline"], full["timeline"][:8])

    def test_real_chain_outputs_feed_lifecycle_without_rewriting_scenario(self):
        trace = self.run_replay()["timeline"][0]
        self.assertEqual(trace["chain"]["validation"]["mechanisms"][0]["status"], "supported")
        self.assertEqual(trace["result"]["scenario"], self.fixture["scenario"])
        self.assertFalse(any(trace["chain"]["explanation"]["news_context"]["permissions"].values()))
        self.assertEqual(trace["result"]["semantic_version"], 1)
        self.assertEqual(trace["chain"]["interpretation"]["fact_verified"], False)

    def test_duplicate_and_rejected_input_do_not_change_intents(self):
        rows = {row["id"]: row for row in self.run_replay()["timeline"]}
        self.assertEqual(rows["duplicate"]["state"]["outbox"], rows["communicated"]["state"]["outbox"])
        self.assertEqual(rows["recipient_mismatch"]["state"], rows["mixed_unsupported_fact"]["state"])
        self.assertEqual(rows["future_rejected"]["state"], rows["recipient_mismatch"]["state"])
        self.assertEqual(rows["unverified_rejected"]["state"], rows["future_rejected"]["state"])

    def test_unknown_stays_unknown_without_extra_attempt(self):
        rows = {row["id"]: row for row in self.run_replay()["timeline"]}
        before, after = rows["unknown_delivery"]["state"], rows["unknown_no_retry"]["state"]
        self.assertEqual(before["attempts"], after["attempts"])
        self.assertEqual(before["outbox"], after["outbox"])

    def test_mixed_change_retains_accepted_facts(self):
        rows = {row["id"]: row for row in self.run_replay()["timeline"]}
        result = rows["mixed_unsupported_fact"]["result"]
        self.assertEqual(result["rejected_semantic_fields"], ["fact_refs", "fact_status"])
        ep = json.loads(rows["mixed_unsupported_fact"]["state"]["episodes"][0]["payload"])
        self.assertEqual(ep["semantic"]["fact_refs"], ["claim_a", "claim_b"])
        self.assertEqual(ep["semantic"]["assessment"], "supports")

    def test_timezone_naive_and_missing_availability_fail(self):
        value = copy.deepcopy(self.fixture["operations"][2]["input"])
        for mode in ("missing", "naive", "backwards"):
            candidate = copy.deepcopy(value)
            if mode == "missing":
                candidate.pop("available_at_utc")
            elif mode == "naive":
                candidate["available_at_utc"] = "2026-10-19T12:01:00"
            else:
                candidate["observed_at_utc"] = "2026-10-20T12:01:00Z"
            with self.assertRaises((ValueError, KeyError)):
                chronology(candidate, "2026-10-19T13:00:00Z")

    def test_chain_rejects_future_bar_availability(self):
        spec = copy.deepcopy(self.fixture["operations"][0]["chain"])
        spec["assets"]["gold"]["candles"][0]["available_at_utc"] = "2026-10-21T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "BAR_AVAILABILITY"):
            chain_input(spec, self.fixture["operations"][0]["at"])

    def test_lifecycle_cannot_predate_its_validation(self):
        spec = copy.deepcopy(self.fixture["operations"][0]["chain"])
        spec["lifecycle"]["available_at_utc"] = "2026-10-19T12:00:00Z"
        with self.assertRaisesRegex(ValueError, "LIFECYCLE_BEFORE_VALIDATION"):
            chain_input(spec, self.fixture["operations"][0]["at"])

    def test_chain_rejects_unclosed_and_naive_bars(self):
        for key, value in (("is_closed", False), ("utc_time", "2026-10-19T10:00:00")):
            spec = copy.deepcopy(self.fixture["operations"][0]["chain"])
            spec["assets"]["gold"]["candles"][0][key] = value
            with self.assertRaises(ValueError):
                chain_input(spec, self.fixture["operations"][0]["at"])

    def test_input_immutability_and_no_persistent_database_left(self):
        original = canonical(self.fixture)
        self.run_replay()
        self.assertEqual(canonical(self.fixture), original)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_equal_timestamp_order_is_explicit_not_list_order(self):
        f = copy.deepcopy(self.fixture)
        f["operations"] = f["operations"][:4]
        f["operations"][3]["at"] = f["operations"][2]["at"]
        f["operations"][3]["input"]["available_at_utc"] = f["operations"][2]["at"]
        f["operations"][3]["input"]["evidence"] = []
        f["operations"][3]["input"]["fact_status"] = "unverified"
        f["operations"][3]["input"]["assessment"] = "not_evaluated"
        f["operations"][3]["input"]["hypothesis_status"] = "not_evaluated"
        a = replay(f, self.root)
        f["operations"].reverse()
        self.assertEqual(a, replay(f, self.root))

    def write_manifest(self, fixture):
        raw = canonical(fixture).encode()
        (self.root / "fixture.json").write_bytes(raw)
        manifest = {"contract_version": "c336_v1", "classification": "synthetic",
                    "provenance": "declared_synthetic_test_attestations", "file": "fixture.json",
                    "sha256": hashlib.sha256(raw).hexdigest()}
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_manifest_hash_and_classification_enforced(self):
        path = self.write_manifest(self.fixture)
        (self.root / "fixture.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "CONTENT_HASH"):
            load_fixture(path)
        f = copy.deepcopy(self.fixture)
        f["classification"] = "historical"
        with self.assertRaisesRegex(ValueError, "SYNTHETIC_ONLY"):
            load_fixture(self.write_manifest(f))

    def test_missing_cases_cannot_report_all_invariants_pass(self):
        f = copy.deepcopy(self.fixture)
        f["operations"] = []
        self.assertEqual(validate(self.write_manifest(f), self.root)["verdict"], "FAIL")

    def test_wrong_expected_result_is_reported_as_failure(self):
        f = copy.deepcopy(self.fixture)
        f["operations"][0]["expect"]["semantic_version"] = 999
        result = validate(self.write_manifest(f), self.root)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertIn("EXPECT_SEMANTIC_VERSION", result["failures"])

    def test_network_is_not_used(self):
        with patch("socket.socket", side_effect=AssertionError("network")), \
             patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            self.run_replay()
