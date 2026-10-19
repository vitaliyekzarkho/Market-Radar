from __future__ import annotations

from copy import deepcopy
from typing import Any

from causal_validation_engine import expected_relation, find_asset, normalize_direction


PRE_WINDOW_FOR_POST = {
    "post_15m": "pre_60m",
    "post_60m": "pre_60m",
    "post_4h": "pre_4h",
    "post_24h": "pre_24h",
}

MATERIAL_PRE_MOVE_RATIO = 0.60
POST_ACCELERATION_RATIO = 0.75
MATERIAL_DISPLACEMENT = 1.0


def as_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def novelty_gate(driver_contract: dict[str, Any], novelty_evidence: dict[str, Any] | None) -> dict[str, Any]:
    evidence = novelty_evidence or {}
    repeated = bool(evidence.get("repeated_statement") or evidence.get("known_before_event"))
    prior_reports = int(evidence.get("prior_verified_reports") or 0)
    explicit = str(evidence.get("status") or "").strip().lower()
    scheduled_actual = bool(evidence.get("scheduled_actual_release"))
    first_verified = bool(evidence.get("first_verified_publication"))

    if repeated or prior_reports > 0 or explicit in {"repeated", "known", "not_new"}:
        status = "repeated"
    elif explicit in {"new", "novel"} or scheduled_actual or first_verified:
        status = "new"
    else:
        status = "unknown"
    return {
        "status": status,
        "new_fact": status == "new",
        "repeated_or_known": status == "repeated",
        "prior_verified_reports": prior_reports,
        "evidence_supplied": bool(evidence),
    }


def no_material_reaction_evaluation(
    driver_contract: dict[str, Any], causal_evaluation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event_id": driver_contract.get("driver_id"),
        "candidate_id": causal_evaluation.get("candidate_id"),
        "classification": "no_material_reaction",
        "causal_role": "context_only",
        "reason_ru": (
            "Предпочтительное окно механизма закрыто, но согласованное движение "
            "не прошло минимальный порог материальности. Позднее движение не "
            "приписывается этому релизу автоматически."
        ),
        "causal_validation_ready": False,
        "driver_confirmation_allowed": False,
        "origin_claim_allowed": False,
        "terminal": True,
        "permissions": {
            "confirm_driver": False, "entry": False, "direction_switch": False,
            "score": False, "technical_invalidation_override": False,
        },
    }


def pre_window_result(asset: dict[str, Any], post_window: str, expected: str) -> dict[str, Any]:
    name = PRE_WINDOW_FOR_POST.get(post_window, "pre_60m")
    windows = asset.get("pre_event") if isinstance(asset.get("pre_event"), dict) else {}
    window = windows.get(name) if isinstance(windows.get(name), dict) else None
    if not window or not window.get("available"):
        return {"available": False, "window": name, "relation": "missing"}
    observed = normalize_direction(window.get("direction"))
    return {
        "available": True,
        "window": name,
        "complete": bool(window.get("complete")),
        "observed_direction": observed,
        "expected": expected,
        "relation": expected_relation(expected, observed),
        "move_percent": as_number(window.get("move_percent")),
        "displacement_ratio": as_number(window.get("displacement_ratio_to_pre_atr")),
    }


def move_geometry(pre: dict[str, Any], post: dict[str, Any]) -> dict[str, Any]:
    pre_move = abs(as_number(pre.get("move_percent")) or 0.0)
    post_move = abs(as_number(post.get("move_percent")) or 0.0)
    post_displacement = as_number(post.get("displacement_ratio"))
    total = pre_move + post_move
    pre_share = pre_move / total if total > 0 else 0.0
    aligned_pre = pre.get("relation") == "aligned"
    material_pre = bool(
        aligned_pre
        and pre.get("complete")
        and (
            pre_share >= MATERIAL_PRE_MOVE_RATIO
            or (pre_move > 0 and post_move > 0 and pre_move >= post_move * POST_ACCELERATION_RATIO)
            or (as_number(pre.get("displacement_ratio")) or 0.0) >= MATERIAL_DISPLACEMENT
        )
    )
    post_accelerated = bool(
        post.get("relation") == "aligned"
        and post.get("window_complete")
        and (
            (post_displacement is not None and post_displacement >= MATERIAL_DISPLACEMENT)
            or (post_move > 0 and post_move >= pre_move * POST_ACCELERATION_RATIO)
        )
    )
    return {
        "pre_move_percent_abs": round(pre_move, 5),
        "post_move_percent_abs": round(post_move, 5),
        "pre_share_of_combined_move": round(pre_share, 4),
        "material_aligned_pre_move": material_pre,
        "post_event_acceleration": post_accelerated,
    }


def evaluate_priced_in(
    driver_contract: dict[str, Any],
    causal_evaluation: dict[str, Any],
    reaction_report: dict[str, Any],
    novelty_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    novelty = novelty_gate(driver_contract, novelty_evidence)
    channels = causal_evaluation.get("channels") if isinstance(causal_evaluation.get("channels"), dict) else {}
    gold_post = channels.get("gold") if isinstance(channels.get("gold"), dict) else {}
    post_window = str(gold_post.get("window") or causal_evaluation.get("preferred_window") or "post_60m")
    assets = reaction_report.get("assets") if isinstance(reaction_report.get("assets"), dict) else {}
    _, gold_asset = find_asset(assets, "gold")
    expected = str(gold_post.get("expected") or "unknown")
    gold_pre = pre_window_result(gold_asset or {}, post_window, expected)
    geometry = move_geometry(gold_pre, gold_post)

    causal_ready = bool(causal_evaluation.get("confirmation_eligible"))
    complete_inputs = bool(causal_ready and gold_pre.get("available") and gold_pre.get("complete"))
    material_pre = geometry["material_aligned_pre_move"]
    acceleration = geometry["post_event_acceleration"]

    # Repeated/previously known information cannot become a new causal origin,
    # even when the proposed market mechanism is already contradicted.
    if novelty["repeated_or_known"]:
        classification = "priced_in"
        role = "explanation_only"
        reason = "Публикация повторяет уже известную рынку информацию и не является новым источником движения."
    elif not complete_inputs or novelty["status"] == "unknown":
        classification = "inconclusive"
        role = "unresolved"
        reason = "Новизна факта или завершённое предсобытийное окно не подтверждены."
    elif material_pre and not acceleration:
        classification = "reaction_preceded_headline"
        role = "explanation_only"
        reason = "Согласованное движение сформировалось до заголовка и не ускорилось после него."
    elif novelty["new_fact"] and material_pre and acceleration:
        classification = "amplifier"
        role = "amplifier_not_origin"
        reason = "Новый факт усилил уже существовавшее движение, но не являлся его началом."
    elif novelty["new_fact"] and not material_pre and acceleration:
        classification = "new_catalyst"
        role = "origin_candidate"
        reason = "До события не было материального согласованного движения; после факта возникло ускорение."
    else:
        classification = "inconclusive"
        role = "unresolved"
        reason = "Рынок не дал достаточного разделения движения до и после события."

    confirmation_allowed = classification in {"new_catalyst", "amplifier"}
    return {
        "schema_version": 1,
        "event_id": driver_contract.get("driver_id"),
        "candidate_id": causal_evaluation.get("candidate_id"),
        "classification": classification,
        "causal_role": role,
        "reason_ru": reason,
        "novelty": novelty,
        "gold_pre_event": gold_pre,
        "gold_post_event": gold_post,
        "geometry": geometry,
        "causal_validation_ready": causal_ready,
        "driver_confirmation_allowed": confirmation_allowed,
        "origin_claim_allowed": classification == "new_catalyst",
        "permissions": {
            "confirm_driver": confirmation_allowed,
            "entry": False,
            "direction_switch": False,
            "score": False,
            "technical_invalidation_override": False,
        },
    }


def apply_priced_in_gate(driver_contract: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(driver_contract)
    updated["priced_in_evaluation"] = result
    classification = str(result.get("classification") or "inconclusive")
    if classification == "priced_in" and updated.get("lifecycle_state") in {"reported_fact", "driver_candidate"}:
        updated["lifecycle_state"] = "priced_in"
    elif result.get("driver_confirmation_allowed") and updated.get("lifecycle_state") == "driver_candidate":
        updated["lifecycle_state"] = "confirmed_driver"
    updated["causal_role"] = result.get("causal_role")
    return updated
