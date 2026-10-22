"""Pure D4 synthetic snapshot assessment; no accepted ledger imports or writes."""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import hashlib
import json
import math
import os
import stat


CONTRACT = "d4_boundary_v1"
RUNTIME = r"C:\Users\User\MarketRadar"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(value):
    return sha(canonical(value).encode())


@dataclass(frozen=True)
class Assessment:
    report: dict
    # Never include these exact retained bytes in public reports or repr.
    envelope_bytes: bytes = field(repr=False)
    payload_bytes: bytes = field(repr=False)


class InputRejected(ValueError):
    pass


def _policy(policy):
    if (policy.get("classification") != "synthetic" or policy.get("contract_version") != CONTRACT
            or not str(policy.get("policy_id", "")).startswith("synthetic:")):
        raise InputRejected("POLICY_UNSUPPORTED")
    for name in ("max_file_bytes", "max_envelope_bytes", "max_payload_bytes", "max_depth", "max_nodes"):
        if type(policy.get(name)) is not int or not 0 < policy[name] <= 16_000_000:
            raise InputRejected("POLICY_LIMIT_INVALID")
    if policy["max_depth"] > 100:
        raise InputRejected("POLICY_DEPTH_UNSAFE")
    u = policy.get("max_clock_uncertainty_seconds")
    if type(u) not in (int, float) or not math.isfinite(u) or u < 0:
        raise InputRejected("POLICY_CLOCK_INVALID")
    if policy.get("provenance_method") != "synthetic_fixture_attestation" or policy.get("availability_method") != "synthetic_ready_attestation":
        raise InputRejected("POLICY_AUTHORITY_UNSUPPORTED")


def _parse(data, limit, policy):
    if not isinstance(data, bytes):
        raise InputRejected("BYTES_REQUIRED")
    if len(data) > limit:
        raise InputRejected("PAYLOAD_SIZE_LIMIT")
    if not data.lstrip().startswith(b"{"):
        raise InputRejected("NONJSON_OR_EXECUTABLE_FORMAT")
    # Bound nesting before json.loads; braces inside escaped strings do not count.
    depth, quoted, escaped = 0, False, False
    for ch in data:
        if quoted:
            if escaped:
                escaped = False
            elif ch == 92:
                escaped = True
            elif ch == 34:
                quoted = False
        elif ch == 34:
            quoted = True
        elif ch in (91, 123):
            depth += 1
            if depth > policy["max_depth"]:
                raise InputRejected("NESTING_DEPTH_LIMIT")
        elif ch in (93, 125):
            depth -= 1
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise InputRejected("DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def constant(_):
        raise InputRejected("NONFINITE_JSON_NUMBER")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except InputRejected:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise InputRejected("MALFORMED_JSON") from None
    pending, count = [value], 0
    while pending:
        item = pending.pop()
        count += 1
        if count > policy["max_nodes"]:
            raise InputRejected("NODE_COUNT_LIMIT")
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise InputRejected("NONFINITE_JSON_NUMBER")
    return value


def _inside_runtime(path):
    # Lexical check precedes filesystem calls, including resolution/stat.
    name = str(path).replace("/", "\\").rstrip("\\").casefold()
    runtime = RUNTIME.casefold()
    return name == runtime or name.startswith(runtime + "\\")


def _safe_file(path, root, limit):
    supplied, root = Path(path), Path(root)
    if _inside_runtime(supplied) or _inside_runtime(root):
        raise InputRejected("RUNTIME_PATH_FORBIDDEN")
    if ".." in supplied.parts or ".." in root.parts:
        raise InputRejected("PATH_TRAVERSAL")
    if str(supplied).startswith(("\\\\", "//")) or str(root).startswith(("\\\\", "//")):
        raise InputRejected("NETWORK_PATH_FORBIDDEN")
    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(supplied if supplied.is_absolute() else root / supplied))
    if _inside_runtime(path) or _inside_runtime(root):
        raise InputRejected("RUNTIME_PATH_FORBIDDEN")
    if path.suffix.lower() != ".json":
        raise InputRejected("NONJSON_FILE_FORBIDDEN")
    if not path.is_relative_to(root):
        raise InputRejected("OUTSIDE_FIXTURE_ROOT")
    def check():
        try:
            for p in reversed((path, *path.parents)):
                info = p.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise InputRejected("SYMLINK_OR_REPARSE_POINT")
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                raise InputRejected("REGULAR_FILE_REQUIRED")
            if info.st_size > limit:
                raise InputRejected("FILE_SIZE_LIMIT")
            return info
        except OSError:
            raise InputRejected("SOURCE_UNAVAILABLE") from None
    before = check()
    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise InputRejected("SOURCE_CHANGED")
            data = stream.read(limit + 1)
            closed = os.fstat(stream.fileno())
    except OSError:
        raise InputRejected("SOURCE_UNAVAILABLE") from None
    after = check()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if len(data) > limit or identity(before) != identity(closed) or identity(before) != identity(after):
        raise InputRejected("SOURCE_CHANGED")
    return data


def assess_files(envelope_path, payload_path, *, fixture_root, cutoff, intake_clock, policy):
    """Read two explicit files under the caller-approved synthetic fixture root.

    No discovery, export or persistence API. Source references inside JSON are
    never opened. Static symlinks/reparse points and detected concurrent changes
    fail closed; fixture roots must be protected from hostile concurrent mutation.
    """
    try:
        _policy(policy)
        envelope = _safe_file(envelope_path, fixture_root, min(policy["max_file_bytes"], policy["max_envelope_bytes"]))
        payload = _safe_file(payload_path, fixture_root, min(policy["max_file_bytes"], policy["max_payload_bytes"]))
    except InputRejected as exc:
        return _rejected(str(exc))
    except (TypeError, AttributeError, KeyError, ValueError):
        return _rejected("INPUT_CONFIGURATION_INVALID")
    return assess_bytes(envelope, payload, cutoff=cutoff, intake_clock=intake_clock, policy=policy)


def _rejected(code):
    report = dict(contract_version=CONTRACT, final_decision="BOUNDARY_VIOLATION",
                  reasons=[dict(scope="boundary", code=code, status="FAIL")],
                  checks={k: "UNKNOWN" for k in ("PARSEABLE", "SCHEMA_VALID", "SEMANTICALLY_COMPATIBLE",
                                                 "PROVENANCE_SUPPORTED", "AVAILABILITY_SUPPORTED", "SHADOW_READY")},
                  d1={"status": "UNKNOWN"}, d2={"status": "UNKNOWN"}, d3={"status": "UNKNOWN"},
                  warnings=["NO_RUNTIME_AUTHORIZATION", "NO_SOURCE_AUTHENTICATION"])
    report["report_hash"] = fingerprint(report)
    return Assessment(report, b"", b"")


def assess_bytes(envelope_bytes, payload_bytes, *, cutoff, intake_clock, policy):
    """Fail-closed public boundary, including wrong JSON field types."""
    try:
        return _assess_bytes(envelope_bytes, payload_bytes, cutoff=cutoff, intake_clock=intake_clock, policy=policy)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError):
        result = _rejected("INVALID_FIELD_SHAPE")
        report = result.report
        report.pop("report_hash")
        report["final_decision"] = "SCHEMA_INCOMPATIBLE"
        report["reasons"] = [dict(scope="schema", code="INVALID_FIELD_SHAPE", status="FAIL")]
        report["checks"]["SCHEMA_VALID"] = "FAIL"
        retained = []
        try:
            _policy(policy)
            for data, limit in ((envelope_bytes, policy["max_envelope_bytes"]), (payload_bytes, policy["max_payload_bytes"])):
                _parse(data, limit, policy)
                retained.append(data)
            report["checks"]["PARSEABLE"] = "PASS"
        except (InputRejected, ValueError, TypeError, AttributeError, KeyError):
            retained = []
        report["report_hash"] = fingerprint(report)
        return Assessment(report, retained[0] if retained else b"", retained[1] if retained else b"")


def _assess_bytes(envelope_bytes, payload_bytes, *, cutoff, intake_clock, policy):
    """Pure synthetic assessment. Only report is suitable for public serialization.

    Original bounded bytes remain in the private Assessment fields unchanged.
    Reports never echo arbitrary source strings, identifiers, keys or values.
    """
    try:
        _policy(policy)
    except (InputRejected, KeyError, TypeError, AttributeError):
        return _rejected("POLICY_INVALID")
    reasons = []
    def issue(scope, code, status="FAIL"):
        row = dict(scope=scope, code=code, status=status)
        if row not in reasons:
            reasons.append(row)
    parsed = True
    try:
        env = _parse(envelope_bytes, policy["max_envelope_bytes"], policy)
        data = _parse(payload_bytes, policy["max_payload_bytes"], policy)
    except InputRejected as exc:
        parsed = False
        code = str(exc)
        if code in {"PAYLOAD_SIZE_LIMIT", "NESTING_DEPTH_LIMIT", "NODE_COUNT_LIMIT", "NONJSON_OR_EXECUTABLE_FORMAT", "BYTES_REQUIRED"}:
            return _rejected(code)
        report = _rejected(code).report
        report.pop("report_hash")
        report["final_decision"] = "SCHEMA_INCOMPATIBLE"
        report["reasons"] = [dict(scope="schema", code=code, status="FAIL")]
        report["checks"]["PARSEABLE"] = "FAIL"
        report["checks"]["SCHEMA_VALID"] = "FAIL"
        report["report_hash"] = fingerprint(report)
        return Assessment(report, b"", b"")
    def required(obj, names, scope):
        if not isinstance(obj, dict):
            issue("schema", scope.upper() + "_OBJECT_REQUIRED")
            return False
        missing = [n for n in names if n not in obj]
        if missing:
            issue("schema", scope.upper() + "_REQUIRED_FIELDS_MISSING")
            for name in missing:
                issue("schema", scope.upper() + "_MISSING_" + name.upper())
        return not missing
    def instant(value, scope, code):
        if value is None:
            issue(scope, code + "_MISSING", "UNKNOWN")
            return None
        try:
            if not isinstance(value, str):
                raise ValueError()
            t = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if t.tzinfo is None or t.utcoffset().total_seconds() != 0:
                raise ValueError()
            return t
        except ValueError:
            issue(scope, code + "_INVALID")
            return None
    def ordered(left, right, scope, code):
        if left is not None and right is not None and left > right:
            issue(scope, code)
    def identifier(value):
        return isinstance(value, str) and bool(value)
    def cutoff_ok(value):
        if not isinstance(value, dict) or type(value.get("maximum_visible_ledger_sequence")) is not int or value["maximum_visible_ledger_sequence"] < 0:
            issue("schema", "CUTOFF_INVALID")
            return None, None
        return instant(value.get("as_of_utc"), "availability", "CUTOFF"), value["maximum_visible_ledger_sequence"]
    asof, sequence = cutoff_ok(cutoff)
    now = instant(intake_clock.get("at_utc") if isinstance(intake_clock, dict) else None, "availability", "INTAKE_CLOCK")
    if not isinstance(intake_clock, dict) or not identifier(intake_clock.get("session_id")) or not identifier(intake_clock.get("health_id")):
        issue("availability", "CLOCK_IDENTITY_MISSING", "UNKNOWN")
    else:
        for key in ("monotonic", "uncertainty_seconds"):
            v = intake_clock.get(key)
            if type(v) not in (int, float) or not math.isfinite(v) or v < 0:
                issue("availability", "CLOCK_MEASUREMENT_INVALID")
        u = intake_clock.get("uncertainty_seconds")
        if type(u) in (int, float) and u > policy["max_clock_uncertainty_seconds"]:
            issue("availability", "CLOCK_UNCERTAINTY_EXCEEDED")
    ordered(asof, now, "availability", "FUTURE_CUTOFF")
    envelope_valid = required(env, ("schema_version", "classification", "snapshot_id", "source", "content_hash", "raw_payload_ref",
                                    "observation_start_utc", "observation_end_utc", "published_at_utc", "claimed_available_at_utc",
                                    "availability_attestation", "declared_completeness", "known_limitations"), "envelope")
    if env.get("schema_version") != "d4_snapshot_v1":
        issue("schema", "ENVELOPE_SCHEMA_UNSUPPORTED")
    if env.get("classification") != "synthetic":
        issue("boundary", "ONLY_SYNTHETIC_FIXTURES_ALLOWED")
    if not identifier(env.get("snapshot_id")):
        issue("schema", "SNAPSHOT_ID_MISSING")
    source = env.get("source", {})
    if required(source, ("component", "producer_version", "artifact_type", "instrument", "timeframe", "provenance"), "source"):
        if any(not identifier(source[k]) for k in ("component", "producer_version", "artifact_type")):
            issue("schema", "SOURCE_IDENTITY_MISSING")
    if isinstance(source, dict):
        provenance = source.get("provenance", {})
        if not isinstance(provenance, dict) or provenance.get("method") != policy["provenance_method"] or not identifier(provenance.get("attestation_id")):
            issue("provenance", "SOURCE_PROVENANCE_UNVERIFIED", "UNKNOWN")
    else:
        source = {}
    payload_hash = sha(payload_bytes) if isinstance(payload_bytes, bytes) else None
    if env.get("content_hash") != payload_hash or env.get("raw_payload_ref") != "sha256:" + str(payload_hash):
        issue("schema", "PAYLOAD_INTEGRITY_MISMATCH")
    start = instant(env.get("observation_start_utc"), "availability", "OBSERVATION_START")
    end = instant(env.get("observation_end_utc"), "availability", "OBSERVATION_END")
    published = (None if env.get("published_at_utc") is None and env.get("publication_missing_reason") else
                 instant(env.get("published_at_utc"), "availability", "PUBLICATION"))
    available = instant(env.get("claimed_available_at_utc"), "availability", "AVAILABILITY")
    for left, right, code in ((start, end, "OBSERVATION_WINDOW_REVERSED"), (published, available, "PUBLICATION_AFTER_AVAILABILITY"),
                              (end, available, "OBSERVATION_AFTER_AVAILABILITY"), (end, asof, "FUTURE_OBSERVATION"),
                              (available, asof, "AVAILABLE_AFTER_CUTOFF")):
        ordered(left, right, "availability", code)
    att = env.get("availability_attestation")
    if (not isinstance(att, dict) or att.get("method") != policy["availability_method"]
            or not identifier(att.get("authority")) or type(att.get("ready_sequence")) is not int or att.get("ready_sequence", -1) < 0):
        issue("availability", "AVAILABILITY_AUTHORITY_UNKNOWN", "UNKNOWN")
    elif sequence is not None and att["ready_sequence"] > sequence:
        issue("availability", "READY_AFTER_SEQUENCE_CUTOFF")
    if env.get("declared_completeness") != "complete":
        issue("semantic", "SNAPSHOT_DECLARED_INCOMPLETE", "UNKNOWN")
    if not isinstance(env.get("known_limitations"), list):
        issue("schema", "LIMITATIONS_LIST_REQUIRED")
    if required(data, ("schema_version", "d1", "d2", "d3"), "payload") and data["schema_version"] != "d4_candidate_bundle_v1":
        issue("schema", "PAYLOAD_SCHEMA_UNSUPPORTED")

    # D1: a candidate contract, never a READY publisher or an authenticated ledger.
    d1 = data.get("d1", {})
    d1_fields = ("source_id", "item_id", "version_id", "selected_version_id", "schema_version", "source_revision",
                 "content_hash", "evidence_payload", "published", "observed", "available_at_utc", "ready_sequence",
                 "availability_authority", "attestation", "quality", "validation_status", "predecessor")
    if required(d1, d1_fields, "d1"):
        for k in ("source_id", "item_id", "version_id", "schema_version", "source_revision"):
            if not identifier(d1[k]):
                issue("d1", "D1_IDENTITY_MISSING")
        if fingerprint(d1["evidence_payload"]) != d1["content_hash"]:
            issue("d1", "D1_PAYLOAD_HASH_MISMATCH")
        if d1["selected_version_id"] != d1["version_id"]:
            issue("d1", "SELECTED_VERSION_UNAVAILABLE")
        if d1["validation_status"] != "valid":
            issue("d1", "D1_VALIDATION_NOT_ESTABLISHED", "UNKNOWN")
        stamps = []
        for fieldname in ("published", "observed"):
            obj = d1[fieldname]
            if not isinstance(obj, dict) or not obj.get("authority"):
                issue("d1", "D1_TIMESTAMP_AUTHORITY_MISSING", "UNKNOWN")
                obj = {}
            stamps.append(None if fieldname == "published" and obj.get("at_utc") is None and obj.get("missing_reason") else
                          instant(obj.get("at_utc"), "d1", "D1_" + fieldname.upper()))
        da = instant(d1["available_at_utc"], "availability", "D1_AVAILABILITY")
        ordered(stamps[0], stamps[1], "d1", "D1_PUBLICATION_AFTER_RECEIPT")
        ordered(stamps[1], da, "availability", "D1_RECEIPT_AFTER_AVAILABILITY")
        ordered(stamps[1], asof, "availability", "D1_FUTURE_OBSERVATION")
        ordered(da, asof, "availability", "D1_AVAILABLE_AFTER_CUTOFF")
        if d1["availability_authority"] != "synthetic_ready_attestation":
            issue("availability", "D1_AVAILABILITY_UNSUPPORTED", "UNKNOWN")
        if type(d1["ready_sequence"]) is not int or d1["ready_sequence"] < 0 or (sequence is not None and d1["ready_sequence"] > sequence):
            issue("availability", "D1_READY_SEQUENCE_INELIGIBLE")
        a = d1["attestation"]
        if required(a, ("verification_method", "claimed_source_identity", "lineage", "uncertainty", "independent"), "d1_attestation"):
            if a["verification_method"] != policy["provenance_method"]:
                issue("provenance", "D1_PROVENANCE_UNVERIFIED", "UNKNOWN")
            if (a["claimed_source_identity"] != d1["source_id"] or not isinstance(a["lineage"], list)
                    or not a["lineage"] or any(not identifier(x) for x in a["lineage"])):
                issue("provenance", "D1_SOURCE_LINEAGE_UNSUPPORTED", "UNKNOWN")
            if type(a["independent"]) is not bool:
                issue("provenance", "D1_INDEPENDENCE_UNDECLARED", "UNKNOWN")
        prior = d1["predecessor"]
        if d1["source_revision"] != "1" and not prior:
            issue("d1", "D1_CORRECTION_LINEAGE_UNKNOWN", "UNKNOWN")
        if prior is not None and (not isinstance(prior, dict) or not prior.get("version_id") or prior.get("version_id") == d1["version_id"]
                                  or prior.get("source_id") != d1["source_id"] or prior.get("item_id") != d1["item_id"]):
            issue("d1", "D1_CORRECTION_LINEAGE_AMBIGUOUS")
    else:
        issue("d1", "D1_CANDIDATE_INCOMPLETE", "UNKNOWN")

    # D2 uses a D4 normalized candidate, retaining the entire supplied scenario.
    d2 = data.get("d2", {})
    scenario, hypotheses = {}, {}
    d2_fields = ("scenario_id", "scenario_version_id", "scenario", "cutoff", "evidence_ref", "authority",
                 "event_type", "hypothesis_id", "predecessor", "created_at_utc", "formation_at_utc")
    if required(d2, d2_fields, "d2"):
        if not identifier(d2["scenario_id"]) or not identifier(d2["scenario_version_id"]):
            issue("d2", "D2_IDENTITY_MISSING")
        decision_time, decision_sequence = cutoff_ok(d2["cutoff"])
        ordered(decision_time, asof, "d2", "D2_CUTOFF_AFTER_ASSESSMENT")
        if decision_sequence is not None and sequence is not None and decision_sequence > sequence:
            issue("d2", "D2_SEQUENCE_AFTER_ASSESSMENT")
        ordered(instant(d1.get("available_at_utc"), "d2", "D2_EVIDENCE_AVAILABILITY"), decision_time,
                "d2", "D2_EVIDENCE_AFTER_DECISION_CUTOFF")
        if type(d1.get("ready_sequence")) is int and decision_sequence is not None and d1["ready_sequence"] > decision_sequence:
            issue("d2", "D2_EVIDENCE_SEQUENCE_AFTER_DECISION")
        expected_ref = dict(version_id=d1.get("version_id"), content_hash=d1.get("content_hash"), cutoff=d2["cutoff"])
        if d2["evidence_ref"] != expected_ref:
            issue("d2", "D2_EVIDENCE_REFERENCE_MISMATCH")
        authority = d2["authority"]
        if not isinstance(authority, dict) or authority.get("kind") != "synthetic_technical_authority" or not authority.get("engine_version"):
            issue("d2", "TECHNICAL_AUTHORITY_UNKNOWN", "UNKNOWN")
            authority = {}
        created = instant(d2["created_at_utc"], "d2", "D2_CREATED")
        ordered(decision_time, created, "d2", "D2_CUTOFF_AFTER_CREATION")
        ordered(created, asof, "d2", "FUTURE_SCENARIO")
        scenario = d2["scenario"]
        if not isinstance(scenario, dict):
            issue("schema", "SCENARIO_OBJECT_REQUIRED")
            scenario = {}
        complete = ("instrument", "timeframe", "point_a", "point_b", "primary", "nested", "alternatives", "direction",
                    "htf", "market_structure", "FTA", "TA", "invalidation", "liquidity_objectives", "permissions")
        if any(k not in scenario or scenario[k] is None for k in complete):
            issue("d2", "TECHNICAL_SNAPSHOT_INCOMPLETE", "UNKNOWN")
        if scenario.get("instrument") != source.get("instrument") or scenario.get("timeframe") != source.get("timeframe"):
            issue("d2", "INSTRUMENT_TIMEFRAME_MISMATCH")
        if not isinstance(scenario.get("permissions"), dict):
            issue("d2", "TECHNICAL_PERMISSIONS_MISSING", "UNKNOWN")
        def collect(s):
            if not isinstance(s, dict) or not isinstance(s.get("primary"), dict) or not isinstance(s.get("nested"), list) or not isinstance(s.get("alternatives"), list):
                issue("d2", "HYPOTHESIS_STRUCTURE_INVALID")
                return {}
            out = {}
            for h in [s["primary"], *s["nested"], *s["alternatives"]]:
                if not isinstance(h, dict) or not identifier(h.get("id")) or h.get("id") in out or not identifier(h.get("direction")):
                    issue("d2", "HYPOTHESIS_IDENTITY_INVALID")
                    continue
                out[h["id"]] = h
            for hid, h in out.items():
                seen = set()
                while h.get("parent_id") is not None:
                    if h["id"] in seen or h["parent_id"] not in out:
                        issue("d2", "HYPOTHESIS_TOPOLOGY_INVALID")
                        break
                    seen.add(h["id"])
                    h = out[h["parent_id"]]
            return out
        hypotheses = collect(scenario)
        if isinstance(scenario.get("primary"), dict) and scenario["primary"].get("direction") != scenario.get("direction"):
            issue("d2", "PRIMARY_DIRECTION_MISMATCH")
        for point in ("point_a", "point_b"):
            obj = scenario.get(point)
            if not isinstance(obj, dict) or not all(k in obj for k in ("price", "time", "definition")):
                issue("d2", "TECHNICAL_POINT_INCOMPLETE", "UNKNOWN")
            else:
                if (type(obj["price"]) not in (int, float) or not math.isfinite(obj["price"])
                        or not identifier(obj["definition"])):
                    issue("d2", "TECHNICAL_POINT_INVALID")
                t = instant(obj["time"], "d2", "TECHNICAL_POINT_TIME")
                ordered(t, decision_time, "d2", "FUTURE_TECHNICAL_POINT")
        # No other timestamp becomes availability; validate supplied technical *_at_utc fields too.
        pending = [scenario]
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                for k, v in item.items():
                    if k.endswith("_at_utc") and not k.startswith("scheduled_") and v is not None:
                        ordered(instant(v, "d2", "TECHNICAL_TIMESTAMP"), decision_time, "d2", "FUTURE_TECHNICAL_TIMESTAMP")
                    pending.append(v)
            elif isinstance(item, list):
                pending.extend(item)
        target, event, prior = d2["hypothesis_id"], d2["event_type"], d2["predecessor"]
        if target not in hypotheses:
            issue("d2", "HYPOTHESIS_TARGET_MISSING")
        if event not in {"HYPOTHESIS_FORMED", "HYPOTHESIS_REVISED", "HYPOTHESIS_INVALIDATED", "SCENARIO_OBSERVED_EXISTING"}:
            issue("d2", "LIFECYCLE_EVENT_INVALID")
        if event == "SCENARIO_OBSERVED_EXISTING":
            if prior is not None or d2["formation_at_utc"] is not None:
                issue("d2", "LEGACY_FORMATION_NOT_ESTABLISHED")
        elif event == "HYPOTHESIS_FORMED" and prior is None:
            formation = instant(d2["formation_at_utc"], "d2", "FORMATION_TIME")
            ordered(formation, created, "d2", "FORMATION_AFTER_CREATION")
            if formation != created:
                issue("d2", "BACKDATED_FORMATION_UNSUPPORTED")
        if event in {"HYPOTHESIS_REVISED", "HYPOTHESIS_INVALIDATED"} and prior is None:
            issue("d2", "D2_PREDECESSOR_MISSING", "UNKNOWN")
        if prior is not None:
            if event == "HYPOTHESIS_FORMED":
                formation = instant(d2["formation_at_utc"], "d2", "FORMATION_TIME")
                if formation != created:
                    issue("d2", "BACKDATED_FORMATION_UNSUPPORTED")
            if not isinstance(prior, dict) or not identifier(prior.get("version_id")) or prior.get("version_id") == d2["scenario_version_id"] or prior.get("scenario_id") != d2["scenario_id"]:
                issue("d2", "D2_PREDECESSOR_INVALID")
            else:
                previous = collect(prior.get("scenario"))
                new = set(hypotheses) - set(previous)
                if not set(previous).issubset(hypotheses) or len(new) > 1 or (new and (event != "HYPOTHESIS_FORMED" or new != {target})) or (event == "HYPOTHESIS_FORMED" and target in previous):
                    issue("d2", "D2_LIFECYCLE_ID_INVARIANT")
                old_s = prior.get("scenario", {})
                if (old_s.get("direction") != scenario.get("direction") or old_s.get("primary", {}).get("id") != scenario.get("primary", {}).get("id")) and authority.get("direction_change_authorized") is not True:
                    issue("d2", "DIRECTION_CHANGE_UNAUTHORIZED")
                if any(hypotheses[i].get("parent_id") != h.get("parent_id") for i, h in previous.items() if i in hypotheses) and authority.get("topology_change_authorized") is not True:
                    issue("d2", "TOPOLOGY_CHANGE_UNAUTHORIZED")
                pc = prior.get("cutoff")
                pt, ps = cutoff_ok(pc)
                ordered(pt, decision_time, "d2", "PREDECESSOR_CUTOFF_AFTER_CURRENT")
                if ps is not None and decision_sequence is not None and ps > decision_sequence:
                    issue("d2", "PREDECESSOR_SEQUENCE_AFTER_CURRENT")
        if event == "HYPOTHESIS_INVALIDATED" and hypotheses.get(target, {}).get("state") != "invalidated":
            issue("d2", "INVALIDATION_STATE_MISMATCH")
    else:
        issue("d2", "D2_CANDIDATE_INCOMPLETE", "UNKNOWN")

    d3 = data.get("d3", {})
    if required(d3, ("evaluation", "hypothesis_id", "episode_id", "event_type", "reason", "policy", "recipients", "frozen_recipients", "eligibility", "delivery"), "d3"):
        evaluation = d3["evaluation"]
        if required(evaluation, ("evaluation_id", "scenario_version_id", "cutoff", "manifest_fingerprint", "technical_context_hash", "evaluated_at_utc", "knowledge", "action", "news_status"), "evaluation"):
            if not identifier(evaluation["evaluation_id"]) or evaluation["scenario_version_id"] != d2.get("scenario_version_id"):
                issue("d3", "EVALUATION_REFERENCE_MISMATCH")
            if evaluation["cutoff"] != d2.get("cutoff") or evaluation["manifest_fingerprint"] != fingerprint(d2.get("evidence_ref")):
                issue("d3", "FROZEN_EVIDENCE_CONTEXT_MISMATCH")
            if evaluation["technical_context_hash"] != fingerprint(scenario):
                issue("d3", "TECHNICAL_CONTEXT_OR_PERMISSIONS_CHANGED")
            evaluated = instant(evaluation["evaluated_at_utc"], "d3", "EVALUATED_TIME")
            ordered(evaluated, asof, "d3", "FUTURE_EVALUATION")
            ordered(instant(d2.get("created_at_utc"), "d3", "SCENARIO_TIME"), evaluated, "d3", "EVALUATION_BEFORE_SCENARIO")
            if evaluation["knowledge"] not in {"KNOWN", "UNKNOWN"} or evaluation["action"] not in {"RECORDED", "WAIT"}:
                issue("d3", "EVALUATION_OUTCOME_INVALID")
            if evaluation["news_status"] not in {"supports", "conflicts", "insufficient_data", "not_evaluated"}:
                issue("d3", "NEWS_CONTEXT_INVALID")
        if d3["hypothesis_id"] not in hypotheses or not str(d3["episode_id"]).startswith("synthetic:"):
            issue("d3", "EPISODE_HYPOTHESIS_IDENTITY_MISSING")
        if not identifier(d3["reason"]) or not d3["reason"].strip():
            issue("d3", "NOTIFICATION_REASON_MISSING")
        if d3["event_type"] not in {"NEW", "MATERIAL_UPDATE", "RESOLVED", "EXPIRED"}:
            issue("d3", "NOTIFICATION_EVENT_INVALID")
        elif d3["event_type"] != "NEW":
            issue("d3", "LIFECYCLE_HISTORY_NOT_VALIDATED", "UNKNOWN")
        np = d3["policy"]
        if not isinstance(np, dict) or np.get("classification") != "synthetic" or not str(np.get("policy_id", "")).startswith("synthetic:"):
            issue("d3", "NOTIFICATION_POLICY_MISSING", "UNKNOWN")
        elif required(np, ("allowed_roles", "allowed_events", "allowed_states", "require_entry_permission", "episode_max_age_seconds", "retry"), "notification_policy"):
            if (not isinstance(np["allowed_roles"], list) or not set(np["allowed_roles"]).issubset({"primary", "nested", "alternative"})
                    or not isinstance(np["allowed_events"], list) or not set(np["allowed_events"]).issubset({"NEW", "MATERIAL_UPDATE", "RESOLVED", "EXPIRED"})
                    or not isinstance(np["allowed_states"], list) or any(not identifier(s) for s in np["allowed_states"])
                    or type(np["require_entry_permission"]) is not bool
                    or type(np["episode_max_age_seconds"]) not in (int, float)
                    or not math.isfinite(np["episode_max_age_seconds"]) or np["episode_max_age_seconds"] < 0):
                issue("d3", "NOTIFICATION_POLICY_INVALID")
            retry = np["retry"]
            if (not isinstance(retry, dict) or retry.get("classification") != "synthetic" or not str(retry.get("policy_id", "")).startswith("synthetic:")
                    or type(retry.get("max_attempts")) is not int or retry["max_attempts"] < 1
                    or any(type(retry.get(k)) is not bool for k in ("allow_failed_retry", "allow_reconciled_unknown_retry"))):
                issue("d3", "RETRY_POLICY_INVALID")
        recipients = d3["recipients"]
        if not isinstance(recipients, dict) or set(recipients) != {"set_id", "fingerprint"} or not str(recipients.get("set_id", "")).startswith("synthetic:") or not isinstance(recipients.get("fingerprint"), str) or len(recipients["fingerprint"]) != 64 or any(c not in "0123456789abcdef" for c in recipients["fingerprint"]):
            issue("d3", "RECIPIENT_DESCRIPTOR_INVALID")
        if recipients != d3["frozen_recipients"]:
            issue("d3", "RECIPIENT_SET_MISMATCH")
        eligibility = d3["eligibility"]
        if eligibility is not None:
            if not isinstance(eligibility, dict) or eligibility.get("status") not in {"ELIGIBLE", "SUPPRESSED_DUPLICATE", "DEFERRED", "INELIGIBLE", "UNKNOWN"}:
                issue("d3", "ELIGIBILITY_DESCRIPTOR_INVALID")
            elif eligibility.get("generation_claimed") is not False:
                issue("d3", "ELIGIBILITY_IS_NOT_GENERATION")
        delivery = d3["delivery"]
        if delivery is not None:
            if not isinstance(delivery, dict) or delivery.get("state") not in {"PREPARED", "STARTED", "ACKNOWLEDGED", "FAILED", "UNKNOWN", "RECONCILED"}:
                issue("d3", "DELIVERY_DESCRIPTOR_INVALID")
            else:
                if type(delivery.get("authorize_retry")) is not bool:
                    issue("d3", "RETRY_DECLARATION_REQUIRED")
                if delivery["state"] == "RECONCILED" and delivery.get("resolution") not in {"provider_accepted", "confirmed_not_accepted", "still_unknown"}:
                    issue("d3", "RECONCILIATION_RESOLUTION_INVALID")
                if delivery.get("recipient_read_proven") is not False or delivery.get("recipient_delivery_proven") is not False:
                    issue("d3", "RECIPIENT_RECEIPT_NOT_ESTABLISHED")
                if delivery.get("authorize_retry") is True and (delivery.get("state") != "RECONCILED" or delivery.get("resolution") != "confirmed_not_accepted"):
                    issue("d3", "UNKNOWN_OR_ACCEPTED_RETRY_FORBIDDEN")
                if delivery.get("authorize_retry") is True:
                    rp = np.get("retry", {}) if isinstance(np, dict) else {}
                    if (rp.get("allow_reconciled_unknown_retry") is not True or delivery.get("duplicate_risk_acknowledged") is not True
                            or type(delivery.get("attempt_count")) is not int or delivery["attempt_count"] < 1
                            or type(rp.get("max_attempts")) is not int or delivery.get("attempt_count", 0) >= rp["max_attempts"]):
                        issue("d3", "RETRY_PREREQUISITES_UNSATISFIED")
                if delivery["state"] in {"UNKNOWN", "STARTED"} or (delivery["state"] == "RECONCILED" and delivery.get("resolution") == "still_unknown"):
                    issue("d3", "DELIVERY_OUTCOME_REMAINS_UNKNOWN", "UNKNOWN")
    else:
        issue("d3", "D3_CANDIDATE_INCOMPLETE", "UNKNOWN")

    def state(*scopes):
        selected = [r for r in reasons if r["scope"] in scopes]
        return "FAIL" if any(r["status"] == "FAIL" for r in selected) else "UNKNOWN" if selected else "PASS"
    checks = dict(PARSEABLE="PASS" if parsed else "FAIL", SCHEMA_VALID=state("schema"),
                  SEMANTICALLY_COMPATIBLE=state("semantic", "d1", "d2", "d3"),
                  PROVENANCE_SUPPORTED=state("provenance"), AVAILABILITY_SUPPORTED=state("availability"))
    final = "SHADOW_READY"
    for scopes, decision in ((["boundary"], "BOUNDARY_VIOLATION"), (["schema"], "SCHEMA_INCOMPATIBLE"),
                             (["provenance"], "PROVENANCE_UNVERIFIED"), (["availability"], "AVAILABILITY_UNKNOWN"),
                             (["semantic", "d1", "d2", "d3"], "INSUFFICIENT_EVIDENCE")):
        if any(r["scope"] in scopes for r in reasons):
            final = decision
            break
    checks["SHADOW_READY"] = "PASS" if final == "SHADOW_READY" else "FAIL"
    report = dict(contract_version=CONTRACT, policy_fingerprint=fingerprint(policy), snapshot_id_fingerprint=fingerprint(env.get("snapshot_id")),
                  source_identity_fingerprint=fingerprint(source), snapshot_content_hash=payload_hash,
                  envelope_hash=sha(envelope_bytes), cutoff_fingerprint=fingerprint(cutoff),
                  intake_clock=dict(at_utc=now.isoformat() if now else None, provenance_fingerprint=fingerprint(intake_clock)),
                  checks=checks, d1=dict(status=state("d1"), authoritative_ready_created=False),
                  d2=dict(status=state("d2"), scenario_created=False, technical_permissions_modified=False),
                  d3=dict(status=state("d3"), eligibility_granted=False, notification_generated=False, delivery_started=False),
                  reasons=sorted(reasons, key=lambda r: (r["scope"], r["code"])), final_decision=final,
                  warnings=["SYNTHETIC_SUPPORT_ONLY", "NO_SOURCE_AUTHENTICATION", "NO_HISTORICAL_TIMELINESS_PROOF", "NO_RUNTIME_AUTHORIZATION",
                            "INDEPENDENCE_IS_AN_UPSTREAM_CLAIM", "NO_RECIPIENT_DELIVERY_OR_READ_PROOF"],
                  retained_input_hashes=dict(envelope=sha(envelope_bytes), payload=payload_hash))
    report["report_hash"] = fingerprint(report)
    return Assessment(report, envelope_bytes, payload_bytes)
