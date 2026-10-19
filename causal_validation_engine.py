from __future__ import annotations

from copy import deepcopy
from typing import Any


HORIZON_WINDOWS = {
    "immediate_headline": "post_15m",
    "immediate_to_intraday": "post_60m",
    "intraday_to_multisession": "post_4h",
    "headline_to_multisession": "post_4h",
    "multisession": "post_24h",
}

ASSET_ALIASES = {
    "gold": ("gold", "xau", "xauusd", "gold.pro"),
    "oil": ("oil", "brent", "wti", "ukoil", "usoil", "oilbrnt.pro"),
    "usd": ("usd", "dxy", "usd_breadth"),
    "real_yields": ("real_yields", "us10y_real", "dfii10"),
    "nominal_yields": ("nominal_yields", "yields", "us10y", "dgs10"),
    "yields": ("yields", "nominal_yields", "us10y", "us2y"),
    "equities": ("equities", "spx", "sp500", "nasdaq"),
    "volatility": ("volatility", "vix"),
    "inflation_expectations": ("inflation_expectations", "breakevens"),
}

# A sign alone is not a market reaction. Require at least half of the average
# pre-release M15 true range before a channel may satisfy or contradict a
# causal mechanism. The raw direction remains visible for audit.
MIN_MATERIAL_DISPLACEMENT = 0.50


def material_reaction(window: dict[str, Any]) -> bool:
    try:
        displacement = float(window.get("displacement_ratio_to_pre_atr"))
    except (TypeError, ValueError):
        return False
    return bool(window.get("complete") and displacement >= MIN_MATERIAL_DISPLACEMENT)


def normalize_direction(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    return {
        "up": "bullish", "rising": "bullish", "higher": "bullish",
        "down": "bearish", "falling": "bearish", "lower": "bearish",
        "flat": "mixed", "neutral": "mixed",
    }.get(text, text)


def expected_relation(expected: str, observed: str) -> str:
    exp = normalize_direction(expected)
    obs = normalize_direction(observed)
    if obs not in {"bullish", "bearish", "mixed"}:
        return "unknown"
    if exp in {"bullish", "bearish"}:
        return "aligned" if exp == obs else "contradicted" if obs in {"bullish", "bearish"} else "mixed"
    if exp in {"bullish_or_mixed", "rising_or_mixed", "bullish_or_neutral", "mixed_or_rising"}:
        return "aligned" if obs in {"bullish", "mixed"} else "contradicted"
    if exp in {"bearish_or_mixed", "falling_or_mixed", "bearish_or_neutral", "mixed_or_falling"}:
        return "aligned" if obs in {"bearish", "mixed"} else "contradicted"
    if exp in {"mixed", "unknown"}:
        return "non_directional"
    if exp == "not_rising":
        return "aligned" if obs != "bullish" else "contradicted"
    if exp == "not_strongly_bullish":
        return "aligned" if obs in {"bearish", "mixed"} else "unknown"
    return "unknown"


def find_asset(assets: dict[str, Any], channel: str) -> tuple[str | None, dict[str, Any] | None]:
    aliases = {item.lower() for item in ASSET_ALIASES.get(channel, (channel,))}
    for key, value in assets.items():
        if not isinstance(value, dict):
            continue
        names = {
            str(key).lower(),
            str(value.get("symbol") or "").lower(),
            str(value.get("channel") or "").lower(),
        }
        if names & aliases:
            return str(key), value
    return None, None


def best_available_window(asset: dict[str, Any], preferred: str) -> tuple[str | None, dict[str, Any] | None]:
    post = asset.get("post_event") if isinstance(asset.get("post_event"), dict) else {}
    preferred_window = post.get(preferred) if isinstance(post.get(preferred), dict) else None
    if preferred_window and preferred_window.get("available"):
        return preferred, preferred_window
    fallback_order = ("post_60m", "post_15m") if preferred != "post_15m" else ()
    for name in fallback_order:
        value = post.get(name)
        if isinstance(value, dict) and value.get("available"):
            return name, value
    return None, None


VERIFIED_FAMILY_EVIDENCE = {
    "monetary_hawkish": "verified_policy_fact",
    "monetary_dovish": "verified_policy_fact",
    "geopolitical_escalation": "verified_escalation_fact",
    "geopolitical_deescalation": "verified_deescalation_fact",
    "oil_supply_tightening": "verified_supply_fact",
    "oil_supply_relief": "verified_supply_fact",
    "inflation_upside_surprise": "verified_upside_surprise",
    "growth_labor_upside_surprise": "verified_upside_surprise",
    "inflation_downside_surprise": "verified_downside_surprise",
    "growth_labor_downside_surprise": "verified_downside_surprise",
    "inflation_mixed_release": "verified_mixed_release",
}


def derived_evidence(
    channel_results: dict[str, dict[str, Any]],
    event_gates: dict[str, bool],
    event_family: str = "unknown",
) -> set[str]:
    evidence: set[str] = set()
    gold = channel_results.get("gold") or {}
    oil = channel_results.get("oil") or {}
    usd = channel_results.get("usd") or {}
    rates = channel_results.get("real_yields") or channel_results.get("nominal_yields") or channel_results.get("yields") or {}

    if gold.get("material") and gold.get("observed_direction") == "bullish":
        evidence.add("gold_bullish_reaction")
    if gold.get("material") and gold.get("observed_direction") == "bearish":
        evidence.add("gold_bearish_reaction")
    if oil.get("material") and oil.get("observed_direction") == "bullish":
        evidence.update({"oil_bullish_reaction", "oil_or_safe_haven_reaction"})
    if oil.get("material") and oil.get("observed_direction") == "bearish":
        evidence.add("oil_bearish_reaction")
    if usd.get("material") and usd.get("observed_direction") == "bullish":
        evidence.add("usd_bullish_reaction")
    if rates.get("available") and rates.get("material"):
        evidence.add("fresh_rate_move")
    if (usd.get("available") and usd.get("material")) or (rates.get("available") and rates.get("material")):
        evidence.update({"fresh_rate_or_usd_move", "rates_or_usd_confirmation", "usd_or_rates_confirmation"})
    if event_gates.get("fact_verified"):
        family_evidence = VERIFIED_FAMILY_EVIDENCE.get(event_family)
        if family_evidence:
            evidence.add(family_evidence)
    if event_gates.get("timing_match"):
        evidence.add("timing_alignment")
    return evidence


def validate_mechanism(
    driver_contract: dict[str, Any],
    mechanism: dict[str, Any],
    reaction_report: dict[str, Any],
    external_evidence: dict[str, bool] | None = None,
) -> dict[str, Any]:
    external_evidence = external_evidence or {}
    driver_id = str(driver_contract.get("driver_id") or "")
    report_event_id = str(reaction_report.get("event_id") or "")
    mechanism_event_id = str(mechanism.get("event_id") or "")
    ids_match = bool(driver_id and report_event_id == driver_id and mechanism_event_id == driver_id)
    fact = driver_contract.get("fact") if isinstance(driver_contract.get("fact"), dict) else {}
    source = driver_contract.get("source") if isinstance(driver_contract.get("source"), dict) else {}
    timing = driver_contract.get("timing") if isinstance(driver_contract.get("timing"), dict) else {}
    event_time = str(timing.get("event_time_utc") or timing.get("published_at_utc") or "")
    timing_match = bool(ids_match and event_time and event_time == str(reaction_report.get("event_time_utc") or ""))
    event_gates = {
        "fact_verified": fact.get("status") == "verified",
        "source_verified": bool(source.get("verified") and source.get("fact_permission")),
        "timing_match": timing_match,
    }

    preferred_window = HORIZON_WINDOWS.get(str(mechanism.get("horizon") or ""), "post_60m")
    assets = reaction_report.get("assets") if isinstance(reaction_report.get("assets"), dict) else {}
    expected = mechanism.get("expected") if isinstance(mechanism.get("expected"), dict) else {}
    channel_results: dict[str, dict[str, Any]] = {}
    for channel, expected_direction in expected.items():
        asset_key, asset = find_asset(assets, str(channel))
        if not asset:
            channel_results[str(channel)] = {
                "available": False, "expected": expected_direction, "relation": "missing",
            }
            continue
        window_name, window = best_available_window(asset, preferred_window)
        if not window:
            channel_results[str(channel)] = {
                "available": False, "asset": asset_key, "expected": expected_direction,
                "relation": "pending",
            }
            continue
        observed = normalize_direction(window.get("direction"))
        material = material_reaction(window)
        channel_results[str(channel)] = {
            "available": True,
            "asset": asset_key,
            "expected": expected_direction,
            "observed_direction": observed,
            "relation": expected_relation(str(expected_direction), observed),
            "material": material,
            "reaction_quality": "material" if material else "immaterial",
            "materiality_threshold": MIN_MATERIAL_DISPLACEMENT,
            "window": window_name,
            "window_status": window.get("status"),
            "window_complete": bool(window.get("complete")),
            "move_percent": window.get("move_percent"),
            "displacement_ratio": window.get("displacement_ratio_to_pre_atr"),
        }

    automatic = derived_evidence(channel_results, event_gates, str(mechanism.get("event_family") or "unknown"))
    supplied = {name for name, available in external_evidence.items() if available}
    satisfied = automatic | supplied
    required = [str(item) for item in mechanism.get("required_evidence") or []]
    missing_evidence = [item for item in required if item not in satisfied]

    gold_result = channel_results.get("gold") or {}
    gold_aligned = gold_result.get("relation") == "aligned" and gold_result.get("material") is True
    gold_contradicted = gold_result.get("relation") == "contradicted" and gold_result.get("material") is True
    gold_complete = bool(gold_result.get("window_complete"))
    transmission = [value for key, value in channel_results.items() if key != "gold" and value.get("available")]
    transmission_aligned = any(
        value.get("relation") == "aligned" and value.get("material") is True for value in transmission
    )
    hard_conflicts = [
        key for key, value in channel_results.items()
        if value.get("relation") == "contradicted" and value.get("material") is True
    ]

    evidence_complete = all(event_gates.values()) and not missing_evidence
    confirmation_eligible = bool(
        evidence_complete and gold_aligned and gold_complete and transmission_aligned and not hard_conflicts
    )
    if gold_contradicted or (hard_conflicts and gold_result.get("available")):
        alignment = "contradicted"
    elif confirmation_eligible:
        alignment = "eligible_after_priced_in_check"
    elif gold_aligned or transmission_aligned:
        alignment = "partially_aligned"
    elif any(
        value.get("available") and value.get("relation") == "aligned" and not value.get("window_complete")
        for value in channel_results.values()
    ):
        alignment = "partially_aligned"
    elif any(
        value.get("available") and value.get("relation") == "aligned"
        and value.get("window_complete") and value.get("material") is False
        for value in channel_results.values()
    ):
        alignment = "directionally_aligned_but_immaterial"
    else:
        alignment = "insufficient"

    return {
        "candidate_id": mechanism.get("candidate_id"),
        "mechanism_id": mechanism.get("mechanism_id"),
        "event_id": driver_id,
        "preferred_window": preferred_window,
        "event_gates": event_gates,
        "channels": channel_results,
        "required_evidence": required,
        "satisfied_evidence": sorted(satisfied),
        "missing_evidence": missing_evidence,
        "hard_conflicts": hard_conflicts,
        "alignment": alignment,
        "confirmation_eligible": confirmation_eligible,
        "confirmation_blocker": (
            "PRICED_IN_CHECK_PENDING" if confirmation_eligible
            else "IMMATERIAL_REACTION" if alignment == "directionally_aligned_but_immaterial"
            else "EVIDENCE_INCOMPLETE_OR_CONTRADICTED"
        ),
        "permissions": {
            "confirm_driver": False,
            "entry": False,
            "direction_switch": False,
            "score": False,
        },
    }


def validate_candidates(
    driver_contract: dict[str, Any],
    mechanism_report: dict[str, Any],
    reaction_report: dict[str, Any],
    external_evidence: dict[str, bool] | None = None,
) -> dict[str, Any]:
    evaluations = [
        validate_mechanism(driver_contract, item, reaction_report, external_evidence)
        for item in mechanism_report.get("candidates") or [] if isinstance(item, dict)
    ]
    rank = {
        "eligible_after_priced_in_check": 0,
        "partially_aligned": 1,
        "directionally_aligned_but_immaterial": 2,
        "insufficient": 3,
        "contradicted": 4,
    }
    evaluations.sort(key=lambda item: rank.get(str(item.get("alignment")), 9))
    any_eligible = any(item.get("confirmation_eligible") for item in evaluations)
    alignments = {str(item.get("alignment") or "") for item in evaluations}
    if any_eligible:
        next_gate = "priced_in_detection"
    elif "partially_aligned" in alignments:
        next_gate = "wait_for_window_close"
    elif "directionally_aligned_but_immaterial" in alignments:
        next_gate = "reaction_window_expired"
    else:
        next_gate = "evidence_incomplete"
    return {
        "schema_version": 1,
        "event_id": driver_contract.get("driver_id"),
        "evaluations": evaluations,
        "best_candidate_id": evaluations[0].get("candidate_id") if evaluations else None,
        "any_confirmation_eligible": any_eligible,
        "driver_confirmation_allowed": False,
        "next_gate": next_gate,
        "scoring_applied": False,
    }


def candidate_contract(driver_contract: dict[str, Any], evaluation: dict[str, Any]) -> dict[str, Any]:
    """Attach audited gates without promoting the lifecycle to confirmed_driver."""
    updated = deepcopy(driver_contract)
    updated["causal_evaluation"] = evaluation
    gates = updated.get("evidence_gate") if isinstance(updated.get("evidence_gate"), dict) else {}
    gates["mechanism_defined"] = bool(evaluation.get("mechanism_id"))
    gates["reaction_aligned"] = bool(evaluation.get("confirmation_eligible"))
    updated["evidence_gate"] = gates
    if updated.get("lifecycle_state") == "reported_fact" and evaluation.get("mechanism_id"):
        updated["lifecycle_state"] = "driver_candidate"
    return updated
