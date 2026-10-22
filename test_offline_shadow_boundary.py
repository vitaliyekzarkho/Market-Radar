"""Synthetic D4 boundary tests; no production resources or ledger instances."""
import ast
import copy
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import offline_shadow_boundary as boundary


FIXTURE = Path(__file__).parent / "tests/fixtures/d4/bundle.json"


class ShadowBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.b = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.e = self.b["envelope"]
        self.p = self.b["payload"]

    def encoded(self):
        payload = boundary.canonical(self.p).encode()
        self.e["content_hash"] = boundary.sha(payload)
        self.e["raw_payload_ref"] = "sha256:" + boundary.sha(payload)
        return boundary.canonical(self.e).encode(), payload

    def assess(self, envelope=None, payload=None):
        eb, pb = self.encoded()
        return boundary.assess_bytes(eb if envelope is None else envelope, pb if payload is None else payload,
                                     cutoff=self.b["cutoff"], intake_clock=self.b["clock"], policy=self.b["policy"])

    def reason(self, code, final=None, **kwargs):
        result = self.assess(**kwargs)
        self.assertIn(code, [r["code"] for r in result.report["reasons"]], result.report)
        self.assertNotEqual(result.report["final_decision"], "SHADOW_READY")
        if final:
            self.assertEqual(result.report["final_decision"], final)
        return result

    def refresh_context(self):
        self.p["d3"]["evaluation"]["technical_context_hash"] = boundary.fingerprint(self.p["d2"]["scenario"])

    def delivery(self, state="UNKNOWN", **kw):
        self.p["d3"]["delivery"] = dict(state=state, authorize_retry=False,
            recipient_delivery_proven=False, recipient_read_proven=False, **kw)

    def revision(self):
        d = self.p["d2"]
        d["predecessor"] = dict(version_id="synthetic:prior", scenario_id=d["scenario_id"],
                                scenario=copy.deepcopy(d["scenario"]), cutoff=copy.deepcopy(d["cutoff"]))
        d["event_type"] = "HYPOTHESIS_REVISED"

    def test_valid_snapshot(self):
        r = self.assess().report
        self.assertEqual(r["final_decision"], "SHADOW_READY")
        self.assertEqual(set(r["checks"].values()), {"PASS"})
        self.assertEqual(r["reasons"], [])

    def test_malformed_json(self):
        r = self.reason("MALFORMED_JSON", "SCHEMA_INCOMPATIBLE", payload=b'{"oops":').report
        self.assertEqual(r["checks"]["PARSEABLE"], "FAIL")

    def test_oversized_payload(self):
        self.reason("PAYLOAD_SIZE_LIMIT", "BOUNDARY_VIOLATION", payload=b"{" + b" " * 131072)

    def test_excessive_depth(self):
        self.reason("NESTING_DEPTH_LIMIT", "BOUNDARY_VIOLATION", payload=b'{"x":' + b"[" * 30 + b"0" + b"]" * 30 + b"}")

    def test_missing_schema(self):
        del self.e["schema_version"]
        self.reason("ENVELOPE_MISSING_SCHEMA_VERSION", "SCHEMA_INCOMPATIBLE")

    def test_unknown_schema(self):
        self.p["schema_version"] = "future"
        self.reason("PAYLOAD_SCHEMA_UNSUPPORTED", "SCHEMA_INCOMPATIBLE")

    def test_unknown_fields_exactly_retained(self):
        self.p["unknown"] = {"command": "never execute", "values": [None, False, "x"]}
        self.e["correlation_id"] = "synthetic:correlation"
        eb, pb = self.encoded()
        r = self.assess()
        self.assertEqual(r.envelope_bytes, eb)
        self.assertEqual(r.payload_bytes, pb)
        self.assertEqual(r.report["final_decision"], "SHADOW_READY")

    def test_missing_source_identity(self):
        self.e["source"]["component"] = ""
        self.reason("SOURCE_IDENTITY_MISSING", "SCHEMA_INCOMPATIBLE")

    def test_payload_hash_mismatch(self):
        eb, pb = self.encoded()
        self.reason("PAYLOAD_INTEGRITY_MISMATCH", "SCHEMA_INCOMPATIBLE", envelope=eb, payload=pb + b" ")

    def test_unverified_provenance(self):
        self.e["source"]["provenance"]["method"] = "unverified"
        self.reason("SOURCE_PROVENANCE_UNVERIFIED", "PROVENANCE_UNVERIFIED")

    def test_missing_availability(self):
        self.e["claimed_available_at_utc"] = None
        self.reason("AVAILABILITY_MISSING", "AVAILABILITY_UNKNOWN")

    def test_future_availability(self):
        self.e["claimed_available_at_utc"] = "2026-10-21T10:00:11Z"
        self.reason("AVAILABLE_AFTER_CUTOFF", "AVAILABILITY_UNKNOWN")

    def test_publication_contradiction(self):
        self.e["published_at_utc"] = "2026-10-21T10:00:07Z"
        self.reason("PUBLICATION_AFTER_AVAILABILITY")

    def test_future_observation(self):
        self.e["observation_end_utc"] = "2026-10-21T10:00:12Z"
        self.reason("FUTURE_OBSERVATION")

    def test_equal_time_sequence(self):
        self.e["claimed_available_at_utc"] = self.b["cutoff"]["as_of_utc"]
        self.e["availability_attestation"]["ready_sequence"] = 10
        self.assertEqual(self.assess().report["final_decision"], "SHADOW_READY")
        self.e["availability_attestation"]["ready_sequence"] = 11
        self.reason("READY_AFTER_SEQUENCE_CUTOFF")

    def file_assess(self, root, ep="envelope.json", pp="payload.json"):
        return boundary.assess_files(ep, pp, fixture_root=root, cutoff=self.b["cutoff"],
                                     intake_clock=self.b["clock"], policy=self.b["policy"])

    def test_traversal_rejected_before_io(self):
        with patch.object(Path, "lstat", side_effect=AssertionError("unexpected IO")):
            r = self.file_assess(Path.cwd(), "../envelope.json")
        self.assertEqual(r.report["reasons"][0]["code"], "PATH_TRAVERSAL")

    def test_symlink_escape_rejected_before_open(self):
        # Deterministic platform metadata injection: Windows reparse point and POSIX symlink.
        # No privileges or real link creation needed; open must never be reached.
        for mode, attrs in ((stat.S_IFLNK, 0), (stat.S_IFDIR, 0x400)):
            fake = type("Metadata", (), {"st_mode": mode, "st_file_attributes": attrs})()
            with patch.object(Path, "lstat", return_value=fake), patch.object(Path, "open", side_effect=AssertionError("followed link")):
                r = self.file_assess(Path.cwd())
            self.assertEqual(r.report["reasons"][0]["code"], "SYMLINK_OR_REPARSE_POINT")

    def test_runtime_path_no_metadata_access(self):
        with patch.object(Path, "lstat", side_effect=AssertionError("runtime access")):
            for root, ep in ((boundary.RUNTIME, "input.json"), (Path.cwd(), boundary.RUNTIME + r"\input.json")):
                r = self.file_assess(root, ep)
                self.assertEqual(r.report["reasons"][0]["code"], "RUNTIME_PATH_FORBIDDEN")

    def test_real_directory_link_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root, outside, link = base / "fixtures", base / "outside", base / "fixtures" / "escape"
            root.mkdir()
            outside.mkdir()
            (outside / "envelope.json").write_text("{}", encoding="utf-8")
            if os.name == "nt":
                # Directory junction needs no Windows symlink privilege. Only synthetic temp paths.
                quoted = lambda p: "'" + str(p).replace("'", "''") + "'"
                subprocess.run(["powershell", "-NoProfile", "-Command",
                    "New-Item -ItemType Junction -Path " + quoted(link) + " -Target " + quoted(outside) + " | Out-Null"], check=True)
            else:
                link.symlink_to(outside, target_is_directory=True)
            try:
                with patch.object(Path, "open", side_effect=AssertionError("followed link")):
                    result = self.file_assess(root, link / "envelope.json")
                self.assertEqual(result.report["reasons"][0]["code"], "SYMLINK_OR_REPARSE_POINT")
                self.assertEqual((outside / "envelope.json").read_text(), "{}")
            finally:
                if os.name == "nt":
                    link.rmdir()  # remove the junction itself, never recurse into target
                else:
                    link.unlink()

    def test_executable_formats_rejected(self):
        for value in (b"\x80\x04pickle", b"import os", b"<xml/>"):
            self.reason("NONJSON_OR_EXECUTABLE_FORMAT", "BOUNDARY_VIOLATION", payload=value)

    def test_d1_schema_incompatible(self):
        del self.p["d1"]["observed"]
        self.reason("D1_MISSING_OBSERVED", "SCHEMA_INCOMPATIBLE")

    def test_d1_ambiguous_lineage(self):
        self.p["d1"]["source_revision"] = "2"
        self.p["d1"]["predecessor"] = {"version_id": self.p["d1"]["version_id"]}
        self.reason("D1_CORRECTION_LINEAGE_AMBIGUOUS")

    def test_selected_version_not_substituted(self):
        self.p["d1"]["selected_version_id"] = "synthetic:newer-unavailable"
        self.reason("SELECTED_VERSION_UNAVAILABLE")

    def test_d2_authority_missing(self):
        self.p["d2"]["authority"] = None
        self.reason("TECHNICAL_AUTHORITY_UNKNOWN")

    def test_d2_predecessor_missing(self):
        self.p["d2"]["event_type"] = "HYPOTHESIS_REVISED"
        self.reason("D2_PREDECESSOR_MISSING")

    def test_primary_nested_preserved(self):
        before = copy.deepcopy(self.p["d2"]["scenario"])
        result = self.assess()
        after = json.loads(result.payload_bytes)["d2"]["scenario"]
        self.assertEqual(after, before)
        self.assertEqual(after["direction"], "bearish")
        self.assertEqual(after["nested"][0]["direction"], "bullish")
        self.assertFalse(after["permissions"]["entry"])

    def test_duplicate_hypothesis(self):
        self.p["d2"]["scenario"]["nested"][0]["id"] = "primary"
        self.reason("HYPOTHESIS_IDENTITY_INVALID")

    def test_future_point(self):
        self.p["d2"]["scenario"]["point_b"]["time"] = "2026-10-22T00:00:00Z"
        self.reason("FUTURE_TECHNICAL_POINT")

    def test_missing_evaluation_reference(self):
        self.p["d3"]["evaluation"]["evaluation_id"] = ""
        self.reason("EVALUATION_REFERENCE_MISMATCH")

    def test_missing_policy(self):
        self.p["d3"]["policy"] = None
        self.reason("NOTIFICATION_POLICY_MISSING")

    def test_recipient_set_mismatch(self):
        self.p["d3"]["frozen_recipients"]["set_id"] = "synthetic:other"
        self.reason("RECIPIENT_SET_MISMATCH")

    def test_unknown_delivery_remains_unknown(self):
        self.delivery()
        r = self.reason("DELIVERY_OUTCOME_REMAINS_UNKNOWN", "INSUFFICIENT_EVIDENCE")
        self.assertEqual(r.report["d3"]["status"], "UNKNOWN")
        self.assertFalse(r.report["d3"]["delivery_started"])

    def test_later_correction_does_not_rewrite_report(self):
        earlier = self.assess()
        stored = boundary.canonical(earlier.report)
        self.p["d1"]["version_id"] = "synthetic:correction"
        self.p["d1"]["available_at_utc"] = "2026-10-22T00:00:00Z"
        self.reason("D1_AVAILABLE_AFTER_CUTOFF")
        self.assertEqual(boundary.canonical(earlier.report), stored)

    def test_no_ledger_service_or_network_dependencies(self):
        tree = ast.parse(Path(boundary.__file__).read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(n.name for n in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module)
        self.assertEqual(imports, {"dataclasses", "datetime", "pathlib", "hashlib", "json", "math", "os", "stat"})
        self.assertNotIn("eval", {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)})
        with patch("builtins.open", side_effect=AssertionError("unexpected file IO")), patch.object(Path, "open", side_effect=AssertionError("unexpected file IO")):
            self.assertEqual(self.assess().report["final_decision"], "SHADOW_READY")

    def test_files_are_read_only_and_no_ledgers_written(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            eb, pb = self.encoded()
            (root / "envelope.json").write_bytes(eb)
            (root / "payload.json").write_bytes(pb)
            for name in ("d1.sqlite", "d2.sqlite", "d3.sqlite"):
                (root / name).write_bytes(b"synthetic sentinel")
            before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()}
            self.assertEqual(self.file_assess(root).report["final_decision"], "SHADOW_READY")
            self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()})

    def test_complete_report_deterministic(self):
        self.assertEqual(self.assess().report, self.assess().report)
        r = self.assess().report
        digest = r.pop("report_hash")
        self.assertEqual(digest, boundary.fingerprint(r))

    def test_missing_file_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            r = self.file_assess(folder)
        self.assertEqual(r.report["final_decision"], "BOUNDARY_VIOLATION")
        self.assertEqual(r.report["reasons"][0]["code"], "SOURCE_UNAVAILABLE")

    def test_public_report_privacy(self):
        sentinel = "SYNTHETIC-PRIVATE-DO-NOT-PUBLISH"
        self.e["snapshot_id"] = sentinel
        self.e["source"]["component"] = sentinel
        self.p["unknown_secret"] = sentinel
        self.p["d3"]["recipients"]["raw_recipient"] = sentinel
        r = self.reason("RECIPIENT_DESCRIPTOR_INVALID")
        self.assertNotIn(sentinel, boundary.canonical(r.report))
        self.assertNotIn(sentinel, repr(r))
        self.assertIn(sentinel.encode(), r.payload_bytes)

    def test_duplicate_keys_rejected(self):
        self.reason("DUPLICATE_JSON_KEY", "SCHEMA_INCOMPATIBLE", payload=b'{"x":1,"x":2}')

    def test_nonfinite_rejected(self):
        for val in (b"NaN", b"Infinity", b"1e999"):
            self.reason("NONFINITE_JSON_NUMBER", "SCHEMA_INCOMPATIBLE", payload=b'{"x":' + val + b"}")

    def test_wrong_shapes_fail_closed(self):
        for section in ("d1", "d2", "d3"):
            original = self.p[section]
            for malformed in (None, [], "bad", 12):
                self.p[section] = malformed
                self.assertNotEqual(self.assess().report["final_decision"], "SHADOW_READY")
            self.p[section] = original

    def test_node_limit(self):
        self.b["policy"]["max_nodes"] = 2
        self.reason("NODE_COUNT_LIMIT", "BOUNDARY_VIOLATION")

    def test_clock_uncertainty(self):
        self.b["clock"]["uncertainty_seconds"] = 1
        self.reason("CLOCK_UNCERTAINTY_EXCEEDED", "AVAILABILITY_UNKNOWN")

    def test_no_clock_inference(self):
        self.b["clock"] = {}
        self.reason("INTAKE_CLOCK_MISSING", "AVAILABILITY_UNKNOWN")

    def test_timezone_required(self):
        self.e["claimed_available_at_utc"] = "2026-10-21T10:00:06"
        self.reason("AVAILABILITY_INVALID")

    def test_precedence_retains_reasons(self):
        self.e["schema_version"] = "bad"
        self.e["source"]["provenance"] = None
        self.e["claimed_available_at_utc"] = None
        r = self.reason("ENVELOPE_SCHEMA_UNSUPPORTED", "SCHEMA_INCOMPATIBLE")
        self.assertTrue({"SOURCE_PROVENANCE_UNVERIFIED", "AVAILABILITY_MISSING"}.issubset({x["code"] for x in r.report["reasons"]}))
        self.assertEqual(r.report["checks"]["PARSEABLE"], "PASS")

    def test_non_synthetic_forbidden(self):
        self.e["classification"] = "production"
        self.reason("ONLY_SYNTHETIC_FIXTURES_ALLOWED", "BOUNDARY_VIOLATION")

    def test_raw_reference_never_followed(self):
        self.encoded()
        self.e["raw_payload_ref"] = boundary.RUNTIME + r"\secret.json"
        eb = boundary.canonical(self.e).encode()
        with patch.object(Path, "open", side_effect=AssertionError("followed reference")):
            self.reason("PAYLOAD_INTEGRITY_MISMATCH", envelope=eb)

    def test_outside_root_rejected(self):
        r = self.file_assess(Path.cwd() / "fixtures", Path.cwd() / "outside.json")
        self.assertEqual(r.report["reasons"][0]["code"], "OUTSIDE_FIXTURE_ROOT")

    def test_file_extension_rejected(self):
        r = self.file_assess(Path.cwd(), "input.pkl")
        self.assertEqual(r.report["reasons"][0]["code"], "NONJSON_FILE_FORBIDDEN")

    def test_oversized_file(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "envelope.json").write_bytes(b" " * 32769)
            r = self.file_assess(folder)
        self.assertEqual(r.report["reasons"][0]["code"], "FILE_SIZE_LIMIT")

    def test_d1_unknown_independence(self):
        self.p["d1"]["attestation"]["independent"] = None
        self.reason("D1_INDEPENDENCE_UNDECLARED", "PROVENANCE_UNVERIFIED")

    def test_d1_identity_claim_mismatch(self):
        self.p["d1"]["attestation"]["claimed_source_identity"] = "synthetic:other"
        self.reason("D1_SOURCE_LINEAGE_UNSUPPORTED")

    def test_d1_payload_hash(self):
        self.p["d1"]["evidence_payload"]["synthetic"] = "changed"
        self.reason("D1_PAYLOAD_HASH_MISMATCH")

    def test_d1_sequence_future(self):
        self.p["d1"]["ready_sequence"] = 11
        self.reason("D1_READY_SEQUENCE_INELIGIBLE")

    def test_later_available_evidence_cannot_support_earlier_decision(self):
        self.p["d1"]["available_at_utc"] = "2026-10-21T10:00:04Z"
        self.reason("D2_EVIDENCE_AFTER_DECISION_CUTOFF")

    def test_equal_timestamp_decision_sequence_is_frozen(self):
        self.p["d1"]["ready_sequence"] = 4
        self.reason("D2_EVIDENCE_SEQUENCE_AFTER_DECISION")

    def test_decision_cannot_use_cutoff_after_formation(self):
        self.p["d2"]["cutoff"]["as_of_utc"] = "2026-10-21T10:00:04Z"
        self.reason("D2_CUTOFF_AFTER_CREATION")

    def test_point_before_report_but_after_decision_is_rejected(self):
        self.p["d2"]["scenario"]["point_b"]["time"] = "2026-10-21T10:00:04Z"
        self.reason("FUTURE_TECHNICAL_POINT")

    def test_revision_cannot_introduce_hypothesis(self):
        self.revision()
        self.p["d2"]["scenario"]["nested"].append(dict(id="new", direction="bullish", parent_id="primary"))
        self.refresh_context()
        self.reason("D2_LIFECYCLE_ID_INVARIANT")

    def test_direction_change_requires_authority(self):
        self.revision()
        self.p["d2"]["scenario"]["direction"] = "bullish"
        self.p["d2"]["scenario"]["primary"]["direction"] = "bullish"
        self.refresh_context()
        self.reason("DIRECTION_CHANGE_UNAUTHORIZED")

    def test_legacy_formation_unknown(self):
        self.p["d2"]["event_type"] = "SCENARIO_OBSERVED_EXISTING"
        self.p["d2"]["formation_at_utc"] = None
        self.assertEqual(self.assess().report["final_decision"], "SHADOW_READY")
        self.p["d2"]["formation_at_utc"] = "2026-10-21T08:00:00Z"
        self.reason("LEGACY_FORMATION_NOT_ESTABLISHED")

    def test_backdated_formation_forbidden(self):
        self.p["d2"]["formation_at_utc"] = "2026-10-21T08:00:00Z"
        self.reason("BACKDATED_FORMATION_UNSUPPORTED")

    def test_new_formation_with_predecessor_cannot_backdate(self):
        self.revision()
        self.p["d2"]["event_type"] = "HYPOTHESIS_FORMED"
        self.p["d2"]["hypothesis_id"] = "new"
        self.p["d2"]["scenario"]["nested"].append(dict(id="new", direction="bullish", parent_id="primary"))
        self.p["d2"]["formation_at_utc"] = "2026-10-21T08:00:00Z"
        self.refresh_context()
        self.reason("BACKDATED_FORMATION_UNSUPPORTED")

    def test_missing_point_value_not_fabricated(self):
        self.p["d2"]["scenario"]["point_a"]["price"] = None
        self.reason("TECHNICAL_POINT_INVALID")

    def test_scheduled_metadata_not_observed_evidence(self):
        self.p["d2"]["scenario"]["scheduled_release_at_utc"] = "2026-10-23T10:00:00Z"
        self.refresh_context()
        self.assertEqual(self.assess().report["final_decision"], "SHADOW_READY")

    def test_d1_future_observation_with_missing_availability(self):
        self.p["d1"]["available_at_utc"] = None
        self.p["d1"]["observed"]["at_utc"] = "2026-10-22T00:00:00Z"
        self.reason("D1_FUTURE_OBSERVATION")

    def test_invalid_boundary_policy(self):
        self.b["policy"]["max_depth"] = 200
        self.reason("POLICY_INVALID", "BOUNDARY_VIOLATION")

    def test_still_unknown_without_retry_is_not_ready(self):
        self.delivery("RECONCILED", resolution="still_unknown")
        self.reason("DELIVERY_OUTCOME_REMAINS_UNKNOWN", "INSUFFICIENT_EVIDENCE")

    def test_permission_tampering_detected(self):
        self.p["d2"]["scenario"]["permissions"]["entry"] = True
        self.reason("TECHNICAL_CONTEXT_OR_PERMISSIONS_CHANGED")

    def test_eligibility_not_generation(self):
        self.p["d3"]["eligibility"] = {"status": "ELIGIBLE", "generation_claimed": True}
        self.reason("ELIGIBILITY_IS_NOT_GENERATION")

    def test_still_unknown_retry_forbidden(self):
        self.delivery("RECONCILED", resolution="still_unknown", duplicate_risk_acknowledged=True, attempt_count=1)
        self.p["d3"]["delivery"]["authorize_retry"] = True
        self.reason("UNKNOWN_OR_ACCEPTED_RETRY_FORBIDDEN")

    def test_accepted_retry_forbidden(self):
        self.delivery("RECONCILED", resolution="provider_accepted", duplicate_risk_acknowledged=True, attempt_count=1)
        self.p["d3"]["delivery"]["authorize_retry"] = True
        self.reason("UNKNOWN_OR_ACCEPTED_RETRY_FORBIDDEN")

    def test_confirmed_not_accepted_requires_policy(self):
        self.delivery("RECONCILED", resolution="confirmed_not_accepted", attempt_count=1)
        self.p["d3"]["delivery"]["authorize_retry"] = True
        self.reason("RETRY_PREREQUISITES_UNSATISFIED")
        self.p["d3"]["delivery"]["duplicate_risk_acknowledged"] = True
        self.assertEqual(self.assess().report["final_decision"], "SHADOW_READY")
        self.p["d3"]["delivery"]["attempt_count"] = 3
        self.reason("RETRY_PREREQUISITES_UNSATISFIED")

    def test_ack_is_not_recipient_delivery(self):
        self.delivery("ACKNOWLEDGED")
        self.p["d3"]["delivery"]["recipient_delivery_proven"] = True
        self.reason("RECIPIENT_RECEIPT_NOT_ESTABLISHED")

    def test_invalid_notification_policy(self):
        self.p["d3"]["policy"]["episode_max_age_seconds"] = -1
        self.reason("NOTIFICATION_POLICY_INVALID")

    def test_lifecycle_history_not_assumed(self):
        self.p["d3"]["event_type"] = "MATERIAL_UPDATE"
        self.reason("LIFECYCLE_HISTORY_NOT_VALIDATED")

    def test_news_dedup_context_required(self):
        del self.p["d3"]["evaluation"]["news_status"]
        self.reason("EVALUATION_MISSING_NEWS_STATUS", "SCHEMA_INCOMPATIBLE")

    def test_process_replay_equivalence(self):
        # Stateless replay in a fresh interpreter; no ledger or persisted report.
        script = "import json,offline_shadow_boundary as b; x=json.load(open('tests/fixtures/d4/bundle.json')); print(b.canonical(b.assess_bytes(b.canonical(x['envelope']).encode(),b.canonical(x['payload']).encode(),cutoff=x['cutoff'],intake_clock=x['clock'],policy=x['policy']).report))"
        actual = subprocess.check_output([sys.executable, "-B", "-c", script], cwd=Path(__file__).parent, text=True)
        self.assertEqual(json.loads(actual), self.assess().report)


if __name__ == "__main__":
    unittest.main()
