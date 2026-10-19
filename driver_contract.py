from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = 1

LIFECYCLE_STATES = {
    "scheduled_risk",
    "reported_fact",
    "driver_candidate",
    "confirmed_driver",
    "contradicted",
    "priced_in",
    "expired",
}

ALLOWED_TRANSITIONS = {
    "scheduled_risk": {"reported_fact", "driver_candidate", "expired"},
    "reported_fact": {"driver_candidate", "contradicted", "priced_in", "expired"},
    "driver_candidate": {"confirmed_driver", "contradicted", "priced_in", "expired"},
    "confirmed_driver": {"contradicted", "expired"},
    "contradicted": {"expired"},
    "priced_in": {"expired"},
    "expired": set(),
}

DIRECTIONAL_EFFECTS = {"bullish", "bearish", "neutral", "mixed", "unknown", "risk_only"}
FACT_STATUSES = {"scheduled", "reported", "verified", "disputed", "unknown"}
REACTION_STATUSES = {"pending", "aligned", "contradicted", "mixed", "absent", "unknown"}


def parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: Any) -> str | None:
    parsed = parse_time(value)
    return parsed.isoformat() if parsed else None


def _clean(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _safe_url(value: Any) -> str | None:
    text = _clean(value)
    return text if text.startswith(("https://", "http://")) else None


def stable_driver_id(event: dict[str, Any]) -> str:
    supplied = _clean(event.get("driver_id") or event.get("event_id"))
    if supplied:
        return supplied
    identity = "|".join((
        _clean(event.get("title") or event.get("event"), "unknown").lower(),
        _clean(event.get("event_time_utc") or event.get("published_at_utc") or event.get("date")),
        _clean(event.get("source"), "unknown").lower(),
    ))
    return "drv_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def infer_initial_state(event: dict[str, Any]) -> str:
    explicit = _clean(event.get("lifecycle_state")).lower()
    if explicit in LIFECYCLE_STATES:
        return explicit
    status = _clean(event.get("status"), "scheduled").lower()
    actual = event.get("actual")
    if status in {"scheduled", "pending"} and actual in (None, ""):
        return "scheduled_risk"
    return "reported_fact"


def build_driver_contract(event: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Normalize a raw event without promoting it to a verified cause.

    P6.1 deliberately records evidence and permissions. It does not infer
    causality from a headline, a calendar surprise, USD breadth, or PA alone.
    """
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    lifecycle = infer_initial_state(event)
    raw_effect = _clean(event.get("effect_xauusd") or event.get("effect"), "risk_only").lower()
    if raw_effect not in DIRECTIONAL_EFFECTS:
        raw_effect = "unknown"
    # Forecasts may contain an analyst's directional expectation, but an event
    # that has not happened is volatility risk only. Preserve the raw value for
    # audit without leaking it into the live directional contract.
    effect = "risk_only" if lifecycle == "scheduled_risk" else raw_effect

    event_time = _iso(event.get("event_time_utc") or event.get("date"))
    published_at = _iso(event.get("published_at_utc"))
    observed_at = _iso(event.get("observed_at_utc")) or moment.isoformat()
    source_url = _safe_url(event.get("source_url"))
    primary_source = bool(event.get("primary_source") or event.get("verified_primary_source"))
    authoritative_fact = bool(event.get("authoritative_fact")) or primary_source
    source_verified = bool(event.get("source_verified")) or authoritative_fact

    fact_status = _clean(event.get("fact_status"), "scheduled" if lifecycle == "scheduled_risk" else "reported").lower()
    if fact_status not in FACT_STATUSES:
        fact_status = "unknown"
    reaction_status = _clean(event.get("reaction_status"), "pending").lower()
    legacy_reactions = {"confirmed": "aligned", "unconfirmed": "unknown"}
    reaction_status = legacy_reactions.get(reaction_status, reaction_status)
    if reaction_status not in REACTION_STATUSES:
        reaction_status = "unknown"

    mechanism = event.get("mechanism") if isinstance(event.get("mechanism"), dict) else {}
    mechanism_name = _clean(mechanism.get("name") or event.get("channel"))
    mechanism_explanation = _clean(mechanism.get("explanation") or event.get("explanation"))
    mechanism_defined = bool(mechanism_name and mechanism_explanation)

    market_reaction = event.get("market_reaction") if isinstance(event.get("market_reaction"), dict) else {}
    reaction_observed = bool(market_reaction.get("observed"))
    reaction_aligned = reaction_status == "aligned" and reaction_observed

    contract = {
        "schema_version": SCHEMA_VERSION,
        "driver_id": stable_driver_id(event),
        "title": _clean(event.get("title") or event.get("event"), "Неизвестное событие"),
        "event_family": _clean(event.get("event_family"), "unknown").lower(),
        "lifecycle_state": lifecycle,
        "fact": {
            "status": fact_status,
            "statement": _clean(event.get("fact_statement") or event.get("title") or event.get("event")),
            "actual": event.get("actual"),
            "forecast": event.get("forecast"),
            "previous": event.get("previous"),
        },
        "source": {
            "name": _clean(event.get("source"), "unknown"),
            "url": source_url,
            "primary": primary_source,
            "authoritative_fact": authoritative_fact,
            "transport": _clean(event.get("source_transport"), "official_primary" if primary_source else "unknown"),
            "verified": source_verified,
            # Explicit permission boundary for downstream causal engines.
            # A verified aggregator may describe a report, but only a verified
            # primary source or a separately verified issuer-syndicated release
            # may establish the fact itself. Transport is recorded explicitly.
            "fact_permission": bool(source_verified and authoritative_fact),
        },
        "timing": {
            "event_time_utc": event_time,
            "published_at_utc": published_at,
            "observed_at_utc": observed_at,
            "evaluated_at_utc": moment.isoformat(),
        },
        "expected_effect": {
            "xauusd": effect,
            "raw_xauusd": raw_effect,
            "horizon": _clean(event.get("horizon"), "unknown").lower(),
        },
        "mechanism": {
            "name": mechanism_name or None,
            "explanation": mechanism_explanation or None,
            "defined": mechanism_defined,
        },
        "market_reaction": {
            **market_reaction,
            "status": reaction_status,
            "observed": reaction_observed,
            "aligned": reaction_aligned,
        },
        "leg_binding": {
            "role": _clean(event.get("leg_role"), "unbound").lower(),
            "direction": _clean(event.get("leg_direction"), "unknown").lower(),
            "timeframe": _clean(event.get("leg_timeframe"), "unknown").upper(),
        },
        "evidence_gate": {
            "fact_verified": fact_status == "verified",
            "source_verified": source_verified,
            "mechanism_defined": mechanism_defined,
            "reaction_aligned": reaction_aligned,
        },
        "permissions": {
            "entry": False,
            "direction_switch": False,
            "score": False,
            "technical_invalidation_override": False,
        },
        "history": [{
            "state": lifecycle,
            "at_utc": moment.isoformat(),
            "reason": "contract_created",
        }],
    }
    contract["validation"] = validate_driver_contract(contract)
    return contract


def confirmation_ready(contract: dict[str, Any]) -> bool:
    gates = contract.get("evidence_gate") if isinstance(contract.get("evidence_gate"), dict) else {}
    return all(bool(gates.get(name)) for name in (
        "fact_verified", "source_verified", "mechanism_defined", "reaction_aligned",
    ))


def transition_driver(
    contract: dict[str, Any],
    target_state: str,
    reason: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    target = _clean(target_state).lower()
    current = _clean(contract.get("lifecycle_state")).lower()
    if current not in LIFECYCLE_STATES or target not in LIFECYCLE_STATES:
        raise ValueError(f"Unknown driver lifecycle transition: {current!r} -> {target!r}")
    if target == current:
        return deepcopy(contract)
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"Illegal driver lifecycle transition: {current} -> {target}")
    if target == "confirmed_driver" and not confirmation_ready(contract):
        missing = [name for name, passed in contract.get("evidence_gate", {}).items() if not passed]
        raise ValueError("Driver confirmation evidence is incomplete: " + ", ".join(missing))

    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    updated = deepcopy(contract)
    updated["lifecycle_state"] = target
    history = updated.get("history") if isinstance(updated.get("history"), list) else []
    history.append({"state": target, "at_utc": moment.isoformat(), "reason": _clean(reason, "unspecified")})
    updated["history"] = history
    updated["validation"] = validate_driver_contract(updated)
    return updated


def validate_driver_contract(contract: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    state = contract.get("lifecycle_state")
    if state not in LIFECYCLE_STATES:
        errors.append("INVALID_LIFECYCLE_STATE")
    if not _clean(contract.get("driver_id")):
        errors.append("MISSING_DRIVER_ID")
    if state == "scheduled_risk" and contract.get("expected_effect", {}).get("xauusd") not in {"risk_only", "unknown"}:
        errors.append("SCHEDULED_EVENT_CANNOT_BE_DIRECTIONAL")
    if state == "confirmed_driver" and not confirmation_ready(contract):
        errors.append("CONFIRMED_DRIVER_MISSING_EVIDENCE")
    permissions = contract.get("permissions") if isinstance(contract.get("permissions"), dict) else {}
    if any(bool(permissions.get(name)) for name in (
        "entry", "direction_switch", "score", "technical_invalidation_override",
    )):
        errors.append("DRIVER_EXCEEDS_ADVISORY_PERMISSION")
    return {"valid": not errors, "errors": errors}
