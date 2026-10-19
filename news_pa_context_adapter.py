"""Offline C3.3.4 explanation only. Never imported by the live pipeline.

Inputs are a selected technical scenario and two availability envelopes:
{"available_at_utc": <aware ISO timestamp>, "payload": <C3.3.x output>}.
Availability means first knowable time, not publication/event time. The caller
must supply trustworthy provenance; this adapter cannot reconstruct it.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math


ROLE_RELATIONS = {
    "confirmed_driver": "supports", "correlated_driver": "supports",
    "supports": "supports", "driver": "supports",
    "conflicting_driver": "conflicts", "conflicts": "conflicts",
    "mixed_driver": "insufficient_data", "unconfirmed_driver": "insufficient_data",
    "scheduled_risk": "not_evaluated", "none": "not_evaluated",
}


def normalize_legacy_role(role):
    """Preserve a legacy claim without upgrading it to independent evidence."""
    return {"original_role": deepcopy(role), "claimed_relation": ROLE_RELATIONS.get(role, "not_evaluated") if isinstance(role, str) else "not_evaluated",
            "source": "legacy_role", "independently_validated": False,
            "causality_confirmed": False}


def _time(value):
    if not isinstance(value, str):
        raise ValueError("An aware ISO timestamp is required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Naive timestamps are not allowed")
    return parsed.astimezone(timezone.utc)


def explain_scenario(scenario, interpretation=None, validation=None, *,
                     as_of_utc, max_age_seconds, legacy_role=None):
    """Return an independent scenario copy and a separate explanatory context.

    Freshness is caller policy, measured from the observation's as_of_utc,
    never refreshed by late ingestion. Unsupported/malformed evidence cannot
    produce support. A contradicted mechanism never implies the opposite trade.
    """
    cutoff = _time(as_of_utc)
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)) or not math.isfinite(max_age_seconds) or max_age_seconds < 0:
        raise ValueError("max_age_seconds must be finite and nonnegative")
    context = {
        "relation": "not_evaluated", "reason": "no_news",
        "as_of_utc": as_of_utc, "max_age_seconds": max_age_seconds,
        "legacy": normalize_legacy_role(legacy_role), "evidence": [],
        "provenance": {}, "causality_confirmed": False,
        "permissions": {"entry": False, "direction_switch": False,
                        "score": False, "confirm_cause": False},
    }
    result = {"scenario": deepcopy(scenario), "news_context": context}

    def finish(relation, reason):
        context.update(relation=relation, reason=reason)
        return result

    if interpretation is None and validation is None:
        return result
    if interpretation is None or validation is None:
        return finish("not_evaluated", "missing_interpretation_or_validation")
    try:
        ip, vp = interpretation["payload"], validation["payload"]
        ia, va = _time(interpretation["available_at_utc"]), _time(validation["available_at_utc"])
        observed = _time(vp["as_of_utc"])
        event = _time(vp["event_time_utc"])
        available = max(ia, va)
        context["provenance"] = {
            "story_id": ip.get("story_id"), "validation_story_id": vp.get("story_id"),
            "event_id": vp.get("event_id"), "event_time_utc": vp.get("event_time_utc"),
            "window": vp.get("window"), "observation_as_of_utc": vp.get("as_of_utc"),
            "interpretation_available_at_utc": interpretation["available_at_utc"],
            "validation_available_at_utc": validation["available_at_utc"],
            "available_at_utc": available.isoformat(),
            "bridge_provenance": deepcopy(vp.get("provenance")),
        }
        if available > cutoff or observed > cutoff or event > cutoff:
            return finish("not_evaluated", "future_data")
        if event > observed or observed > va:
            return finish("insufficient_data", "invalid_chronology")
        timing_mode = interpretation.get("timing_mode", "post_event")
        interpretation_event = _time(ip["event_time_utc"]) if "event_time_utc" in ip else event
        context["provenance"]["interpretation_timing"] = {
            "mode": timing_mode, "event_time_utc": interpretation_event.isoformat(),
            "event_time_source": "interpretation" if "event_time_utc" in ip else "validation_anchor",
        }
        if timing_mode not in {"post_event", "pre_event_hypothesis"}:
            return finish("insufficient_data", "unknown_interpretation_timing_mode")
        if interpretation_event > cutoff:
            return finish("not_evaluated", "future_interpretation_event")
        if ia < max(interpretation_event, event) and timing_mode != "pre_event_hypothesis":
            return finish("insufficient_data", "interpretation_precedes_event")
        if "generated_at_utc" in ip and _time(ip["generated_at_utc"]) > ia:
            return finish("insufficient_data", "interpretation_generated_after_availability")
        if ia > va:
            return finish("insufficient_data", "validation_precedes_interpretation")
        # Existing C3.3.3 observations lack individual anchors. Origin then
        # inherits the report's contract; asset completeness cannot prove it.
        if vp.get("schema_version") != 1 or vp.get("interpretation") != "directional_consistency_only_not_causal_attribution":
            return finish("insufficient_data", "unsupported_validator_contract")
        context["provenance"]["observation_binding"] = {
            "contract": "story_intermarket_validation_engine.evaluate_story/schema_v1",
            "trust": "caller_supplied_validator_output",
            "independent_origin_verification": False,
            "rule": "Missing anchors inherit the report; supplied anchors must match. Asset completeness does not prove origin.",
        }
        if cutoff - observed > timedelta(seconds=max_age_seconds):
            return finish("not_evaluated", "stale_observation")
        sid = ip.get("story_id")
        if not sid or sid != vp.get("story_id") or sid != vp.get("event_id"):
            return finish("insufficient_data", "identity_mismatch")
        windows = {"post_15m": 900, "post_60m": 3600, "post_4h": 14400, "post_24h": 86400}
        window = vp.get("window")
        if window not in windows or observed < event + timedelta(seconds=windows[window]):
            return finish("insufficient_data", "invalid_or_unfinished_window")
        direction = scenario.get("direction")
        if direction not in {"bullish", "bearish"}:
            return finish("insufficient_data", "unknown_technical_direction")
        candidates = ip.get("mechanism_candidates") or []
        mechanisms = vp.get("mechanisms") or []
        indexed = {c["mechanism_id"]: c for c in candidates}
        ids = [m["mechanism_id"] for m in mechanisms]
        if not indexed or len(indexed) != len(candidates) or len(set(ids)) != len(ids) or set(ids) != set(indexed) or not all(ids):
            return finish("insufficient_data", "ambiguous_or_missing_mechanisms")
        relations = []
        for mechanism in mechanisms:
            expected = indexed[mechanism["mechanism_id"]].get("expected", {}).get("gold")
            status = mechanism.get("status")
            observations = mechanism.get("observations") or []
            for observation in observations:
                bindings = [observation]
                if "provenance" in observation:
                    if not isinstance(observation["provenance"], dict):
                        return finish("insufficient_data", "invalid_observation_provenance")
                    bindings.append(observation["provenance"])
                for binding in bindings:
                    for key, value in (("event_id", vp["event_id"]), ("story_id", sid), ("window", window)):
                        if key in binding and binding[key] != value:
                            return finish("insufficient_data", "observation_identity_or_window_mismatch")
                    for key, value in (("event_time_utc", event), ("as_of_utc", observed)):
                        if key in binding and _time(binding[key]) != value:
                            return finish("insufficient_data", "observation_time_mismatch")
                    if "available_at_utc" in binding and not observed <= _time(binding["available_at_utc"]) <= va:
                        return finish("insufficient_data", "invalid_observation_availability")
            valid_observations = bool(observations) and all(
                o.get("result") == "supported" and isinstance(o.get("move_percent"), (int, float))
                and not isinstance(o.get("move_percent"), bool) and math.isfinite(o["move_percent"])
                for o in observations)
            expected_assets = set(indexed[mechanism["mechanism_id"]].get("expected", {}))
            observed_assets = [o.get("asset") for o in observations]
            complete_assets = len(observed_assets) == len(set(observed_assets)) and set(observed_assets) == expected_assets
            same_expectations = all(o.get("expected") == indexed[mechanism["mechanism_id"]].get("expected", {}).get(o.get("asset")) for o in observations)
            relation = "insufficient_data"
            if status == "supported" and valid_observations and complete_assets and same_expectations and expected in {"bullish", "bearish"}:
                relation = "supports" if expected == direction else "conflicts"
            relations.append(relation)
            context["evidence"].append({"mechanism_id": mechanism["mechanism_id"],
                "validation_status": status, "expected_gold": expected, "relation": relation,
                "observations": deepcopy(observations)})
        if set(relations) == {"supports"}:
            return finish("supports", "validated_direction_agrees")
        if set(relations) == {"conflicts"}:
            return finish("conflicts", "validated_direction_opposes")
        return finish("insufficient_data", "unresolved_or_mixed_evidence")
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return finish("insufficient_data", "malformed_or_missing_evidence")
