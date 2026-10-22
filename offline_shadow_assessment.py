"""D5 bounded, deterministic synthetic replay. No collectors or transport APIs."""
from dataclasses import dataclass, field
from datetime import datetime
import json
import math
import sqlite3

from offline_shadow_boundary import assess_bytes, canonical, fingerprint, sha
from offline_notification_audit import NotificationAudit

BASE = "bd0070056aa9c10ef8e74799af4bbf421cc1ba89"
CONTRACT = "d5_shadow_v1"
MAX_BYTES, MAX_STEPS, MAX_DEPTH, MAX_NODES = 4_000_000, 128, 40, 100_000
CATEGORIES = {"CONTRACT_VIOLATION", "TEMPORAL_VIOLATION", "LIFECYCLE_VIOLATION",
              "EVIDENCE_GAP", "UNEXPLAINED_TRANSITION", "POLICY_MISMATCH"}


class ReplayError(ValueError):
    """Messages are fixed codes, never source-provided text."""


def utc(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if t.tzinfo is None or t.utcoffset().total_seconds() != 0 or not 2000 <= t.year <= 2100:
            raise ValueError()
        return t
    except ValueError:
        raise ReplayError("INVALID_UTC_TIMESTAMP") from None


def _number(v):
    return type(v) in (int, float) and math.isfinite(v)


def _identity(v):
    return isinstance(v, str) and bool(v.strip()) and len(v) <= 256


def _hash(v):
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v)


def _load(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ReplayError("INPUT_BYTES_OR_SIZE")
    depth, quoted, escaped = 0, False, False
    for ch in raw:
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
            if depth > MAX_DEPTH:
                raise ReplayError("INPUT_DEPTH")
        elif ch in (93, 125):
            depth -= 1
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise ReplayError("DUPLICATE_JSON_KEY")
            out[k] = v
        return out
    def nonfinite(_):
        raise ReplayError("NONFINITE_NUMBER")
    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
    except (ValueError, UnicodeError, RecursionError):
        raise ReplayError("INVALID_JSON") from None
    pending, count = [result], 0
    while pending:
        item = pending.pop()
        count += 1
        if count > MAX_NODES:
            raise ReplayError("INPUT_NODE_LIMIT")
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ReplayError("NONFINITE_NUMBER")
    return result


@dataclass(frozen=True)
class ReplayResult:
    """Immutable canonical public report; no private input attached."""
    report_json: str = field(repr=False)

    @property
    def report(self):
        return json.loads(self.report_json)  # caller mutation cannot affect original


def _seal(value):
    value["record_hash"] = fingerprint(value)
    return value


def _fail(code):
    return ReplayResult(canonical(_seal(dict(contract_version=CONTRACT, baseline=BASE,
        final_status="CONTRACT_VIOLATION", reasons=[code], timeline=[],
        limitations=["NO_RUNTIME_AUTHORIZATION", "NO_HISTORICAL_PERFORMANCE_PROOF"]))))


def zone_diagnostic(zone, observation, cutoff):
    """Supplied synthetic price-path diagnostics, never PA confirmation overrides."""
    out = dict(position="UNKNOWN", touch="UNKNOWN", break_state="UNKNOWN")
    try:
        low, high = zone["low"], zone["high"]
        if not _number(low) or not _number(high) or low > high:
            return out
        if (utc(observation["available_at_utc"]) > utc(cutoff["as_of_utc"])
                or utc(observation["observed_at_utc"]) > utc(observation["available_at_utc"])
                or type(observation["sequence"]) is not int
                or observation["sequence"] < 0
                or observation["sequence"] > cutoff["maximum_visible_ledger_sequence"]):
            return out
        price = observation.get("last")
        if _number(price):
            out["position"] = "ABOVE_ZONE" if price > high else "BELOW_ZONE" if price < low else "INSIDE_ZONE"
        bar = observation.get("candle")
        if isinstance(bar, dict):
            h, l, c = bar.get("high"), bar.get("low"), bar.get("close")
            # A candle needs its own availability, range and end time; last price is insufficient.
            if (not all(_number(v) for v in (h, l, c)) or not l <= c <= h
                    or utc(bar["end_at_utc"]) > utc(observation["observed_at_utc"])
                    or utc(bar["available_at_utc"]) > utc(cutoff["as_of_utc"])):
                return out
            if l <= high and h >= low:
                out["touch"] = "TOUCH_CONFIRMED"
            if (bar.get("closed") is True and bar.get("confirmation") == "synthetic_closed_candle"
                    and _identity(bar.get("rule_id")) and (c < low or c > high)):
                out["break_state"] = "BREAK_CONFIRMED"
        return out
    except (KeyError, TypeError, ValueError):
        return out


def assess_ledger_notification(audit, request, *, scenario_hash, scenario_version_id, evidence_version_id,
                               evidence_content_hash, decision_cutoff, report_cutoff, operation_id):
    """Explicit opt-in: accepted D3 assessment on caller-owned synthetic ledgers.

    Writes only D3 eligibility/policy events through its accepted public API.
    Does not construct ledgers, ingest/publish evidence, form scenarios, generate
    notifications, prepare attempts or invoke delivery. No path-based API.
    """
    unknown = dict(mode="UNAVAILABLE", status="UNKNOWN", reason="AUTHORITATIVE_RECORD_MISSING", delivery_authorized=False)
    if type(audit) is not NotificationAudit:
        return unknown
    try:
        if audit.clock_policy.get("classification") != "synthetic":
            return unknown
        evaluation = audit.decisions.projection()["evaluations"][request["evaluation_id"]]
        clock = audit.clock()
        if (fingerprint(evaluation["technical_context"]) != scenario_hash
                or evaluation["scenario_version_id"] != scenario_version_id
                or evaluation["cutoff"] != decision_cutoff
                or utc(evaluation["evaluated_at_utc"]) > utc(report_cutoff["as_of_utc"])
                or utc(clock["at_utc"]) != utc(report_cutoff["as_of_utc"])):
            return dict(unknown, reason="AUTHORITATIVE_CONTEXT_MISMATCH")
        selected = [x["selected"] for x in evaluation["manifest"]["inputs"].values() if x["selected"]]
        if not any(x["version"]["version_id"] == evidence_version_id
                   and x["version"]["metadata"]["content_hash"] == evidence_content_hash for x in selected):
            return dict(unknown, reason="AUTHORITATIVE_EVIDENCE_MISMATCH")
        allowed = {"evaluation_id", "hypothesis_id", "episode_id", "event_type", "recipients", "policy", "reason",
                   "predecessor_notification_id", "resolution"}
        if set(request) - allowed:
            return dict(unknown, reason="UNSUPPORTED_NOTIFICATION_REQUEST")
        result = audit.assess("d5:" + operation_id, **request)
        # Replayed idempotent operations must still refer to the requested historical instant.
        if utc(result["recorded_at_utc"]) != utc(report_cutoff["as_of_utc"]):
            return dict(unknown, reason="AUTHORITATIVE_CONTEXT_MISMATCH")
        return dict(mode="ACCEPTED_SYNTHETIC_LEDGER", status=result["status"], reason="D3_ASSESSED",
                    evaluation_ref=fingerprint(evaluation["evaluation_id"]),
                    scenario_ref=fingerprint(evaluation["scenario_version_id"]),
                    evidence_ref=fingerprint(evaluation["manifest"]),
                    eligibility_ref=fingerprint(result["eligibility_id"]),
                    lifecycle_state=result["lifecycle_state"], delivery_authorized=False)
    except (ValueError, KeyError, TypeError, AttributeError, OSError, sqlite3.Error):
        return dict(unknown, reason="ACCEPTED_LEDGER_REJECTED")


def replay(raw, *, ledger_context=None, stop_after=None):
    """Pure replay unless caller explicitly supplies a synthetic D3 context.

    ledger_context = {event_id: {audit: NotificationAudit, request: D3 args}}.
    Missing contexts produce UNKNOWN. stop_after bounds cutoffs for prefix replay.
    All fixture references are in-memory hashes, never paths or URLs.
    """
    try:
        return _replay(_load(raw), ledger_context=ledger_context, stop_after=stop_after)
    except ReplayError as exc:
        return _fail(str(exc))
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError):
        return _fail("INVALID_FIELD_SHAPE")


def _replay(case, *, ledger_context, stop_after):
    if (case["schema_version"] != CONTRACT or case["classification"] != "synthetic"
            or not _identity(case["case_id"]) or not _identity(case["instrument"])
            or case["baseline"] != BASE or case["scope"] not in {"candidate_only", "synthetic_ledgers"}
            or not isinstance(case["known_uncertainties"], list) or not isinstance(case["expected_invariants"], list)
            or case["policy"]["version"] != "d5_policy_v1"
            or case["policy"]["classification"] != "synthetic"):
        raise ReplayError("CASE_CONTRACT_INVALID")
    steps = case["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ReplayError("SNAPSHOT_COUNT_LIMIT")
    if stop_after is not None and (type(stop_after) is not int or not 0 <= stop_after <= len(steps)):
        raise ReplayError("PREFIX_LIMIT_INVALID")
    steps = steps if stop_after is None else steps[:stop_after]
    records, previous, seen_ids, eligible, version_contents = [], None, set(), {}, {}
    evidence_identity, latest_evidence = {}, {}
    last_position, last_cutoff_sequence = None, None
    # Stable prefix identity excludes later steps and expected outcomes (never assessment authority).
    identity = {k: case[k] for k in ("schema_version", "classification", "case_id", "instrument", "baseline", "scope", "policy")}
    prefix = []
    for step in steps:
        event, sequence, cutoff = step["event_id"], step["sequence"], step["cutoff"]
        if not _identity(event) or event in seen_ids or type(sequence) is not int or sequence < 0:
            raise ReplayError("EVENT_ID_OR_SEQUENCE_INVALID")
        now = utc(cutoff["as_of_utc"])
        if type(cutoff["maximum_visible_ledger_sequence"]) is not int or cutoff["maximum_visible_ledger_sequence"] < 0:
            raise ReplayError("CUTOFF_SEQUENCE_INVALID")
        cutoff = {k: cutoff[k] for k in ("as_of_utc", "maximum_visible_ledger_sequence")}
        if last_cutoff_sequence is not None and cutoff["maximum_visible_ledger_sequence"] < last_cutoff_sequence:
            raise ReplayError("CUTOFF_SEQUENCE_ROLLBACK")
        last_cutoff_sequence = cutoff["maximum_visible_ledger_sequence"]
        position = (now, sequence)
        if last_position is not None and (position <= last_position or sequence <= last_position[1]):
            raise ReplayError("REPLAY_ORDER_UNPROVEN")
        seen_ids.add(event)
        last_position = position
        discrepancies = []
        def gap(kind, code, refs=()):
            row = dict(classification=kind, reason_code=code, cutoff_ref=fingerprint(cutoff),
                       event_ref=fingerprint(event), relevant_refs=[fingerprint(r) for r in refs])
            if row not in discrepancies:
                discrepancies.append(row)
        bundle = step["snapshot"]
        if not _hash(step["snapshot_hash"]) or fingerprint(bundle) != step["snapshot_hash"]:
            gap("CONTRACT_VIOLATION", "SNAPSHOT_HASH_MISMATCH")
            prefix.append(dict(event_ref=fingerprint(event), rejected_snapshot_ref=fingerprint(bundle), cutoff=cutoff))
            records.append(_seal(dict(contract_version=CONTRACT, case_ref=fingerprint(identity),
                event_ref=fingerprint(event), cutoff=cutoff, sequence=sequence,
                input_prefix_fingerprint=fingerprint(prefix), evidence_prefix_fingerprint=fingerprint(sorted(eligible.items())),
                final_status="CONTRACT_VIOLATION", discrepancies=discrepancies,
                notification=dict(mode="UNAVAILABLE", status="UNKNOWN", delivery_authorized=False))))
            continue
        if bundle["cutoff"] != cutoff:
            gap("TEMPORAL_VIOLATION", "D4_OUTER_CUTOFF_MISMATCH")
        boundary = assess_bytes(canonical(bundle["envelope"]).encode(), canonical(bundle["payload"]).encode(),
                                cutoff=cutoff, intake_clock=bundle["clock"], policy=bundle["policy"]).report
        if boundary["final_decision"] != "SHADOW_READY":
            gap("EVIDENCE_GAP", "D4_NOT_SHADOW_READY")
            if boundary["final_decision"] in {"SCHEMA_INCOMPATIBLE", "BOUNDARY_VIOLATION"}:
                gap("CONTRACT_VIOLATION", "D4_CONTRACT_REJECTED")
            for reason in boundary["reasons"]:
                if reason["status"] == "FAIL" and any(x in reason["code"] for x in ("FUTURE", "AFTER_CUTOFF", "AFTER_DECISION", "AFTER_CREATION", "SEQUENCE_INELIGIBLE")):
                    gap("TEMPORAL_VIOLATION", "D4_TEMPORAL_REJECTION")
        data = bundle["payload"]
        scenario, d2, d3 = data["d2"]["scenario"], data["d2"], data["d3"]
        for hid, formed in step["formation"].items():
            if formed is None:
                gap("EVIDENCE_GAP", "HYPOTHESIS_FORMATION_UNKNOWN", [hid])
            elif utc(formed) > utc(d2["created_at_utc"]) or utc(formed) > now:
                gap("TEMPORAL_VIOLATION", "HYPOTHESIS_FORMATION_IN_FUTURE", [hid])
        if previous is None and step["formation"].get(d2["hypothesis_id"]) != d2["formation_at_utc"]:
            gap("TEMPORAL_VIOLATION", "INITIAL_FORMATION_UNSUPPORTED")
        if scenario["instrument"] != case["instrument"]:
            gap("CONTRACT_VIOLATION", "INSTRUMENT_MISMATCH")
        evidence = step["evidence"]
        if not isinstance(evidence, list) or len(evidence) > 128:
            raise ReplayError("EVIDENCE_COUNT_LIMIT")
        support, conflict, missing, statuses = [], [], [], []
        visible_ids = set()
        for e in evidence:
            eid = e["id"]
            if not _identity(eid) or not _identity(e["source_id"]) or not _identity(e["item_id"]):
                raise ReplayError("EVIDENCE_IDENTITY_INVALID")
            state, reason = "UNKNOWN", "AVAILABILITY_UNKNOWN"
            available, observed = e.get("available_at_utc"), e.get("observed_at_utc")
            eq = e.get("sequence")
            eligible_now = False
            if available is not None and observed is not None and type(eq) is int and eq >= 0:
                if utc(observed) > utc(available):
                    gap("TEMPORAL_VIOLATION", "OBSERVATION_AFTER_AVAILABILITY", [eid])
                elif utc(available) <= now and eq <= cutoff["maximum_visible_ledger_sequence"]:
                    eligible_now = True
                else:
                    reason = "NOT_YET_AVAILABLE"
            if eligible_now:
                if fingerprint(e["payload"]) != e["content_hash"]:
                    gap("CONTRACT_VIOLATION", "EVIDENCE_HASH_MISMATCH", [eid])
                    reason = "CORRUPT_EVIDENCE"
                else:
                    content = fingerprint(e)
                    if eid in eligible and eligible[eid] != content:
                        gap("CONTRACT_VIOLATION", "IMMUTABLE_EVIDENCE_CHANGED", [eid])
                        reason = "CONFLICTING_SOURCE_REVISION"
                    else:
                        prior = e.get("predecessor_id")
                        source_item = (e["source_id"], e["item_id"])
                        key = (e["source_id"], e["item_id"], e["revision"])
                        if key in version_contents and version_contents[key] != eid:
                            gap("CONTRACT_VIOLATION", "CONFLICTING_SOURCE_REVISION", [eid])
                            reason = "CONFLICTING_SOURCE_REVISION"
                        elif prior is not None and prior not in eligible:
                            gap("EVIDENCE_GAP", "CORRECTION_PREDECESSOR_MISSING", [eid, prior])
                            reason = "CORRECTION_PREDECESSOR_MISSING"
                        elif eid not in eligible and ((prior is not None and (evidence_identity.get(prior) != source_item
                                or latest_evidence.get(source_item) != prior))
                                or (prior is None and (e["revision"] != "1" or source_item in latest_evidence))):
                            gap("LIFECYCLE_VIOLATION", "CORRECTION_LINEAGE_CONFLICT", [eid])
                            reason = "CORRECTION_LINEAGE_CONFLICT"
                        else:
                            if eid not in eligible:
                                latest_evidence[source_item] = eid
                            eligible[eid], version_contents[key] = content, eid
                            evidence_identity[eid] = source_item
                            max_age = e["max_age_seconds"]
                            if not _number(max_age) or max_age < 0:
                                raise ReplayError("AGE_POLICY_INVALID")
                            if latest_evidence[source_item] != eid:
                                reason = "SUPERSEDED_EVIDENCE"
                            elif e["status"] == "scheduled":
                                reason = "SCHEDULED_NOT_PUBLISHED"
                            elif (now - utc(observed)).total_seconds() > max_age or e["status"] == "stale":
                                reason = "STALE_EVIDENCE"
                            elif e["status"] in {"unconfirmed", "unknown"}:
                                reason = "SOURCE_CONFIRMATION_UNKNOWN"
                            elif e["status"] != "current":
                                raise ReplayError("EVIDENCE_STATUS_INVALID")
                            else:
                                state, reason = "AVAILABLE_SYNTHETIC_CLAIM", "DECLARED_SYNTHETIC_ONLY"
                                visible_ids.add(eid)
            if state == "UNKNOWN":
                missing.append(fingerprint(eid))
                if e.get("used_as_current") is True:
                    gap("TEMPORAL_VIOLATION", "UNAVAILABLE_EVIDENCE_USED_AS_CURRENT", [eid])
            elif e["relation"] == "supports":
                support.append(fingerprint(eid))
            elif e["relation"] == "conflicts":
                conflict.append(fingerprint(eid))
            elif e["relation"] != "neutral":
                raise ReplayError("EVIDENCE_RELATION_INVALID")
            statuses.append(dict(evidence_ref=fingerprint(eid), source_ref=fingerprint(e["source_id"]),
                                 state=state, reason=reason, timeframe_ref=fingerprint(e.get("timeframe"))))
        # D4 is candidate assessment only. Availability claims do not create D1 READY membership.
        notification = dict(mode="UNAVAILABLE", status="UNKNOWN", reason="AUTHORITATIVE_RECORD_MISSING", delivery_authorized=False)
        comparison = dict(classification="INITIAL", causal_reason=None, change_event=None,
                          evidence_cutoff=fingerprint(d2["cutoff"]))
        transition = step["transition"]
        current_targets = step["targets"]
        progression_unverified = False
        if previous:
            old = previous["snapshot"]["payload"]["d2"]
            old_s = old["scenario"]
            fields = ("point_a", "point_b", "direction", "primary", "nested", "alternatives", "FTA", "TA",
                      "liquidity_objectives", "invalidation", "permissions", "htf", "market_structure")
            changes = [f for f in fields if old_s.get(f) != scenario.get(f)]
            target_changed = previous["targets"] != current_targets
            availability_only_gap = False
            progression_structure = False
            idm_late = False
            valid_transition = isinstance(transition, dict)
            if valid_transition:
                required = ("event_id", "predecessor_event_id", "at_utc", "sequence", "evidence_ids", "causal_reason", "classification")
                valid_transition = all(k in transition for k in required)
            if valid_transition:
                valid_transition = (transition["predecessor_event_id"] == previous["event_id"]
                    and transition["event_id"] == event and transition["sequence"] == sequence
                    and utc(transition["at_utc"]) <= utc(d2["created_at_utc"])
                    and utc(transition["at_utc"]) <= now
                    and utc(transition["at_utc"]) >= utc(previous["cutoff"]["as_of_utc"])
                    and _identity(transition["causal_reason"]) and bool(transition["evidence_ids"]))
                if valid_transition:
                    ev = {e["id"]: e for e in evidence}
                    progression = transition["classification"] == "TARGET_PROGRESSION"
                    def eligible_at_change(eid):
                        return (eid in visible_ids and utc(ev[eid]["available_at_utc"]) <= utc(transition["at_utc"])
                            and utc(ev[eid]["available_at_utc"]) <= utc(d2["cutoff"]["as_of_utc"])
                            and ev[eid]["sequence"] <= d2["cutoff"]["maximum_visible_ledger_sequence"])
                    valid_transition = all(eligible_at_change(i) for i in transition["evidence_ids"])
                    if progression:
                        candidates = [ev[i] for i in transition["evidence_ids"] if i in ev and
                            ev[i]["payload"].get("objective_type") == "D1_FRACTAL_IDM" and
                            ev[i]["payload"].get("price") == scenario["point_b"].get("price") and ev[i].get("timeframe") == "D1"]
                        candidate = candidates[0] if len(candidates) == 1 else None
                        progression_structure = (candidate is not None and d2["scenario_id"] == old["scenario_id"]
                            and scenario["primary"]["id"] == old_s["primary"]["id"]
                            and scenario["direction"] == old_s["direction"]
                            and d2["event_type"] == "HYPOTHESIS_REVISED"
                            and d2["authority"].get("kind") == "synthetic_technical_authority"
                            and _identity(d2["authority"].get("engine_version")))
                        comparison["structural_candidate"] = "DECLARED_D1_FRACTAL_IDM" if candidate else "UNKNOWN"
                        comparison["declared_change_event"] = fingerprint(transition["event_id"])
                        comparison["declared_causal_reason"] = fingerprint(transition["causal_reason"])
                        established = candidate["payload"].get("established_at_utc") if candidate else None
                        established_ok = (established is not None and candidate.get("available_at_utc") is not None
                            and utc(established) <= utc(candidate["available_at_utc"]))
                        if candidate and established is not None and candidate.get("available_at_utc") is not None and not established_ok:
                            gap("TEMPORAL_VIOLATION", "IDM_ESTABLISHED_AFTER_CLAIMED_AVAILABILITY", [candidate["id"]])
                        valid_transition = valid_transition and progression_structure and established_ok
                        # Unknown chronology must not disguise corruption, stale evidence or a missing cause.
                        candidate_reason = next((s["reason"] for s in statuses if candidate and s["evidence_ref"] == fingerprint(candidate["id"])), None)
                        availability_only_gap = (progression_structure and not valid_transition
                            and candidate_reason in {"AVAILABILITY_UNKNOWN", "NOT_YET_AVAILABLE", "DECLARED_SYNTHETIC_ONLY"}
                            and fingerprint(candidate["payload"]) == candidate["content_hash"]
                            and (established is None or candidate.get("available_at_utc") is None or established_ok)
                            and all(eligible_at_change(i) for i in transition["evidence_ids"] if i != candidate["id"]))
                        if availability_only_gap:
                            idm_late = (candidate.get("available_at_utc") is not None and
                                (utc(candidate["available_at_utc"]) > utc(d2["cutoff"]["as_of_utc"])
                                 or utc(candidate["available_at_utc"]) > utc(transition["at_utc"]))) or (
                                type(candidate.get("sequence")) is int and candidate["sequence"] > d2["cutoff"]["maximum_visible_ledger_sequence"])
                        progression_unverified = not valid_transition
                        comparison["historical_availability"] = ("SUPPORTED_SYNTHETIC" if valid_transition else
                            "NOT_AVAILABLE_AT_CUTOFF" if idm_late else "UNKNOWN")
            new_version = d2["scenario_version_id"] != old["scenario_version_id"]
            prior = d2.get("predecessor")
            proper_predecessor = (isinstance(prior, dict) and prior.get("version_id") == old["scenario_version_id"]
                                  and prior.get("scenario_id") == old["scenario_id"] and prior.get("scenario") == old_s)
            if changes and (not new_version or not proper_predecessor):
                gap("LIFECYCLE_VIOLATION", "MATERIAL_CHANGE_WITHOUT_NEW_VERSION_AND_PREDECESSOR")
            if (changes or target_changed) and not valid_transition:
                if availability_only_gap:
                    gap("TEMPORAL_VIOLATION" if idm_late else "EVIDENCE_GAP",
                        "IDM_NOT_AVAILABLE_AT_CUTOFF" if idm_late else "IDM_HISTORICAL_AVAILABILITY_UNKNOWN")
                else:
                    gap("UNEXPLAINED_TRANSITION", "CHANGE_WITHOUT_CAUSAL_AVAILABLE_EVIDENCE")
            if "permissions" in changes and d2["authority"].get("permission_change_authorized") is not True:
                gap("POLICY_MISMATCH", "TECHNICAL_PERMISSION_CHANGED_WITHOUT_AUTHORITY")
            if "direction" in changes and d2["authority"].get("direction_change_authorized") is not True:
                gap("LIFECYCLE_VIOLATION", "UNAUTHORIZED_DIRECTION_REVERSAL")
            def roles(s):
                return {h["id"]: (role, h.get("parent_id")) for role, hs in
                        (("primary", [s["primary"]]), ("nested", s["nested"]), ("alternative", s["alternatives"])) for h in hs}
            old_roles, new_roles = roles(old_s), roles(scenario)
            added = set(new_roles) - set(old_roles)
            if added and (len(added) != 1 or d2["event_type"] != "HYPOTHESIS_FORMED" or added != {d2["hypothesis_id"]}):
                gap("LIFECYCLE_VIOLATION", "NEW_HYPOTHESIS_WITHOUT_TARGETED_FORMATION", sorted(added))
            if not set(old_roles).issubset(new_roles):
                gap("LIFECYCLE_VIOLATION", "HYPOTHESIS_ID_REMOVED")
            if any(new_roles.get(k) != v for k, v in old_roles.items()) and d2["authority"].get("topology_change_authorized") is not True:
                gap("LIFECYCLE_VIOLATION", "HYPOTHESIS_ROLE_OR_PARENT_CHANGED")
            if d2["scenario_id"] != old["scenario_id"]:
                gap("LIFECYCLE_VIOLATION", "SCENARIO_ID_CHANGED")
            if d2["event_type"] != "HYPOTHESIS_FORMED" and step["formation"] != previous["formation"]:
                gap("TEMPORAL_VIOLATION", "ORIGINAL_FORMATION_REWRITTEN")
            before_targets = previous["targets"]
            if current_targets["original_point_b"] != before_targets["original_point_b"]:
                gap("LIFECYCLE_VIOLATION", "ORIGINAL_TARGET_REWRITTEN")
            if current_targets["primary_source"] != before_targets["primary_source"]:
                gap("LIFECYCLE_VIOLATION", "PRIMARY_SOURCE_REPLACED_BY_LOCAL_ANCHOR")
            b_changed = scenario["point_b"] != old_s["point_b"]
            f_changed = scenario["FTA"] != old_s["FTA"]
            if b_changed:
                history = current_targets["history"]
                if not any(t.get("target") == old_s["point_b"] and t.get("state") in {"reached", "breached", "superseded", "invalidated"} for t in history):
                    gap("UNEXPLAINED_TRANSITION", "PRIOR_TARGET_HISTORY_MISSING")
                if not d2["authority"].get("target_change_authorized") is True and not progression_structure:
                    gap("POLICY_MISMATCH", "TARGET_REACHED_IS_NOT_NEW_TARGET_AUTHORITY")
            classification = "UNCHANGED"
            if changes or target_changed:
                classification = (transition.get("classification") if valid_transition else
                    ("TARGET_PROGRESSION_UNVERIFIED" if idm_late else "TARGET_PROGRESSION_AVAILABILITY_UNKNOWN")
                    if availability_only_gap else "UNEXPLAINED_TRANSITION")
                allowed = {"SCENARIO_REVISION", "DYNAMIC_FTA_UPDATE", "NEARER_OBSTACLE", "ROUTE_ONLY_UPDATE", "TARGET_STATUS_UPDATE", "TARGET_PROGRESSION"}
                if availability_only_gap:
                    allowed.update({"TARGET_PROGRESSION_AVAILABILITY_UNKNOWN", "TARGET_PROGRESSION_UNVERIFIED"})
                if classification not in allowed:
                    classification = "UNEXPLAINED_TRANSITION"
                    gap("UNEXPLAINED_TRANSITION", "TRANSITION_CLASSIFICATION_UNSUPPORTED")
                if (classification in {"DYNAMIC_FTA_UPDATE", "NEARER_OBSTACLE"} and (b_changed or not f_changed)
                        or classification == "ROUTE_ONLY_UPDATE" and changes):
                    gap("CONTRACT_VIOLATION", "TARGET_CLASSIFICATION_INCONSISTENT")
            comparison.update(classification=classification, structural_point_b_before=fingerprint(old_s["point_b"]),
                structural_point_b_after=fingerprint(scenario["point_b"]), fta_before=fingerprint(old_s["FTA"]),
                fta_after=fingerprint(scenario["FTA"]), intermediate_targets_before=fingerprint(old_s["TA"]),
                intermediate_targets_after=fingerprint(scenario["TA"]),
                change_event=fingerprint(transition["event_id"]) if valid_transition else None,
                causal_reason=fingerprint(transition["causal_reason"]) if valid_transition else None)
            # Candidate duplicate evidence only, never substitute for accepted D3 eligibility.
            semantic_fields = ("primary", "nested", "alternatives", "point_a", "point_b", "FTA", "TA", "invalidation", "liquidity_objectives", "htf", "market_structure")
            semantic_same = all(scenario.get(k) == old_s.get(k) for k in semantic_fields) and d3["evaluation"]["news_status"] == previous["snapshot"]["payload"]["d3"]["evaluation"]["news_status"]
            same_stream = d3["episode_id"] == previous["snapshot"]["payload"]["d3"]["episode_id"] and d3["hypothesis_id"] == previous["snapshot"]["payload"]["d3"]["hypothesis_id"]
            if semantic_same and same_stream:
                gap("EVIDENCE_GAP", "DUPLICATE_CANDIDATE_REQUIRES_D3")
        if current_targets["current_point_b"] != scenario["point_b"]:
            gap("CONTRACT_VIOLATION", "STRUCTURAL_TARGET_REFERENCE_MISMATCH")
        for t in current_targets["history"]:
            if t["state"] not in {"reached", "breached", "superseded", "invalidated", "introduced"}:
                raise ReplayError("TARGET_STATE_INVALID")
            if t.get("evidence_id") not in visible_ids:
                gap("EVIDENCE_GAP", "TARGET_STATE_EVIDENCE_UNKNOWN")
            else:
                proof = next(e for e in evidence if e["id"] == t["evidence_id"])
                if (proof["payload"].get("target") != t["target"] or proof["payload"].get("target_state") != t["state"]
                        or t.get("at_utc") is None or utc(t["at_utc"]) > now
                        or utc(proof["available_at_utc"]) > utc(t["at_utc"])):
                    gap("EVIDENCE_GAP", "TARGET_STATE_PROOF_MISMATCH")
            if t["state"] == "breached" and t.get("closed_confirmation") is not True:
                gap("EVIDENCE_GAP", "TARGET_BREACH_NOT_CONFIRMED")
        # Explicit semantic annotations, not an NLP trading model.
        for n in step["narratives"]:
            if n["kind"] not in {"CURRENT_STATE", "CONDITIONAL_STATE", "TRANSITION_TRIGGER", "INVALIDATION_CONDITION", "UNVERIFIED_STATE"}:
                raise ReplayError("NARRATIVE_KIND_INVALID")
            if n.get("asserted_active") is True and n.get("requires_trigger") is True:
                proof = next((e for e in evidence if e["id"] == n.get("trigger_evidence_id") and e["id"] in visible_ids), None)
                if (proof is None or proof["payload"].get("trigger_id") != n.get("trigger_id")
                        or proof["payload"].get("closed_confirmation") is not True):
                    gap("CONTRACT_VIOLATION", "STATE_SEMANTICS_MISMATCH")
        entry = step["entry"]
        if entry["timing"] not in {"forming", "late", "expired", "continuation", "unknown"}:
            raise ReplayError("ENTRY_TIMING_INVALID")
        if entry.get("trade_permission_claimed") is True and scenario["permissions"].get("entry") is not True:
            gap("POLICY_MISMATCH", "DIRECTION_OR_ANCHOR_IS_NOT_ENTRY_PERMISSION")
        if previous and previous["entry"]["timing"] in {"late", "expired"} and entry["timing"] in {"forming", "continuation"}:
            if d2["authority"].get("entry_timing_reset_authorized") is not True:
                gap("POLICY_MISMATCH", "ANCHOR_CANNOT_RESET_ENTRY_TIMING")
        zone = zone_diagnostic(step["zone"]["bounds"], step["zone"]["observation"], cutoff) if step["zone"] else None
        if case["scope"] == "synthetic_ledgers" and ledger_context is not None:
            ctx = ledger_context.get(event)
            blockers = [d for d in discrepancies if d["classification"] != "EVIDENCE_GAP"]
            # D4's only accepted limitation bypass is missing D3 history; D3 supplies that history.
            boundary_ok = boundary["final_decision"] == "SHADOW_READY" or (
                boundary["final_decision"] == "INSUFFICIENT_EVIDENCE" and
                {r["code"] for r in boundary["reasons"]} == {"LIFECYCLE_HISTORY_NOT_VALIDATED"})
            if ctx and not blockers and boundary_ok and not progression_unverified:
                request = ctx["request"]
                if (request["hypothesis_id"] != d3["hypothesis_id"] or request["episode_id"] != d3["episode_id"]
                        or request["evaluation_id"] != d3["evaluation"]["evaluation_id"]
                        or request["event_type"] != d3["event_type"] or request["policy"] != d3["policy"]
                        or request["recipients"] != d3["recipients"]):
                    gap("POLICY_MISMATCH", "D3_REQUEST_DOES_NOT_MATCH_SNAPSHOT")
                else:
                    notification = assess_ledger_notification(ctx["audit"], request, scenario_hash=fingerprint(scenario),
                        scenario_version_id=d2["scenario_version_id"], evidence_version_id=data["d1"]["version_id"],
                        evidence_content_hash=data["d1"]["content_hash"],
                        decision_cutoff=d2["cutoff"], report_cutoff=cutoff,
                        operation_id=fingerprint(dict(case=identity, event=event, snapshot=step["snapshot_hash"])))
        if notification["mode"] == "UNAVAILABLE":
            gap("EVIDENCE_GAP", notification["reason"])
        declared = step.get("claimed_notification_status")
        if declared is not None and (notification["mode"] == "UNAVAILABLE" or declared != notification["status"]):
            gap("POLICY_MISMATCH", "NOTIFICATION_CLAIM_UNSUPPORTED")
        if step["historical_evidence"] != "synthetic_only":
            gap("EVIDENCE_GAP", "HISTORICAL_CHRONOLOGY_UNPROVEN")
        prefix.append(dict(event_ref=fingerprint(event), snapshot_hash=step["snapshot_hash"], cutoff=cutoff))
        final = "SHADOW_CONSISTENT"
        for cls in ("CONTRACT_VIOLATION", "TEMPORAL_VIOLATION", "LIFECYCLE_VIOLATION", "UNEXPLAINED_TRANSITION", "POLICY_MISMATCH", "EVIDENCE_GAP"):
            if any(d["classification"] == cls for d in discrepancies):
                final = "INSUFFICIENT_EVIDENCE" if cls == "EVIDENCE_GAP" else cls
                break
        record = _seal(dict(contract_version=CONTRACT, policy_ref=fingerprint(case["policy"]), case_ref=fingerprint(identity),
            event_ref=fingerprint(event), cutoff=cutoff, sequence=sequence, input_prefix_fingerprint=fingerprint(prefix),
            evidence_prefix_fingerprint=fingerprint(sorted(eligible.items())), d4_ref=boundary["report_hash"],
            candidate_status=boundary["final_decision"], authority_scope=notification["mode"],
            d4_reasons=boundary["reasons"],
            scenario_ref=fingerprint(d2["scenario_version_id"]), scenario_content_hash=fingerprint(scenario),
            primary_ref=fingerprint(scenario["primary"]["id"]), nested_refs=[fingerprint(h["id"]) for h in scenario["nested"]],
            alternative_refs=[fingerprint(h["id"]) for h in scenario["alternatives"]],
            direction=scenario["direction"] if scenario["direction"] in {"bullish", "bearish", "neutral"} else "UNKNOWN",
            formation_ref=fingerprint(step["formation"]), invalidation_ref=fingerprint(scenario["invalidation"]),
            targets_ref=fingerprint(current_targets), transition=comparison, supporting=support, contradicting=conflict,
            missing=missing, evidence=statuses, notification=notification, zone=zone,
            narratives_ref=fingerprint(step["narratives"]), entry_timing=entry["timing"],
            actionable_conclusion_prohibited=True, discrepancies=discrepancies, final_status=final))
        records.append(record)
        previous = step
    all_discrepancies = [d for r in records for d in r["discrepancies"]]
    overall = "SHADOW_CONSISTENT" if records else "INSUFFICIENT_EVIDENCE"
    for cls in ("CONTRACT_VIOLATION", "TEMPORAL_VIOLATION", "LIFECYCLE_VIOLATION", "UNEXPLAINED_TRANSITION", "POLICY_MISMATCH", "EVIDENCE_GAP"):
        if any(d["classification"] == cls for d in all_discrepancies):
            overall = "INSUFFICIENT_EVIDENCE" if cls == "EVIDENCE_GAP" else cls
            break
    report = dict(contract_version=CONTRACT, baseline=BASE, replay_case_fingerprint=fingerprint(identity),
                  assessed_prefix_fingerprint=fingerprint(prefix), timeline=records,
                  final_status=overall,
                  limitations=["SYNTHETIC_ONLY", "NO_HISTORICAL_PERFORMANCE_PROOF", "NO_DURABLE_RECOVERY",
                               "NO_RUNTIME_AUTHORIZATION", "NO_TRADING_OR_DELIVERY_AUTHORIZATION",
                               "D4_FILE_RACE_PROTECTION_NOT_EXTENDED"],
                  discrepancies=all_discrepancies)
    return ReplayResult(canonical(_seal(report)))
