"""C3.3.5 pure informational policy. No transport or production defaults."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
import re


def utc(value):
    if not isinstance(value, str):
        raise ValueError("aware timestamp required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("naive timestamp")
    return result.astimezone(timezone.utc)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identifier(value):
    # Store opaque references, never source text, URLs or arbitrary exceptions.
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,95}", value):
        raise ValueError("opaque identifier required")
    if value.startswith(("ghp_", "github_pat_", "sk-")):
        raise ValueError("credential-like identifier")
    return value


@dataclass(frozen=True)
class Policy:
    version: str
    episode_max_seconds: int
    inactivity_seconds: int
    notification_ttl_seconds: int
    suppression_seconds: int
    evidence_max_age_seconds: int
    retry_max_attempts: int
    retry_backoff_seconds: int
    attempt_timeout_seconds: int

    def __post_init__(self):
        identifier(self.version)
        for key, value in asdict(self).items():
            if key == "version":
                continue
            if type(value) is not int or value < (0 if key == "suppression_seconds" else 1):
                raise ValueError("invalid policy: " + key)


def intent(episode, previous, kind, recipient, evidence, available, now, policy):
    identifier(recipient)
    if not recipient.startswith("recipient_"):
        raise ValueError("recipient must be an opaque recipient_ key")
    identity = [episode["canonical_story_id"], episode["episode_id"],
                episode["semantic_version"], kind, recipient]
    return {
        "canonical_story_id": episode["canonical_story_id"],
        "episode_id": episode["episode_id"], "semantic_version": episode["semantic_version"],
        "notification_kind": kind, "recipient_key": recipient,
        "idempotency_key": digest(identity), "reason": kind,
        "previous_assessment": previous.get("assessment") if previous else None,
        "current_assessment": episode["semantic"]["assessment"],
        "hypothesis_id": episode["semantic"]["hypothesis_id"],
        "contradicted_hypothesis_id": episode["semantic"].get("contradicts_hypothesis_id"),
        "evidence_refs": sorted(evidence), "available_at_utc": available,
        "created_at_utc": stamp(now),
        "expires_at_utc": stamp(now + timedelta(seconds=policy.notification_ttl_seconds)),
        "policy_version": policy.version, "informational_only": True,
        "semantic_fingerprint": digest(episode["semantic"]),
        "delivery_policy": {key: getattr(policy, key) for key in
                            ("retry_max_attempts", "retry_backoff_seconds", "attempt_timeout_seconds")},
        "label": "MARKET RADAR — HYPOTHESIS UPDATE",
        "disclaimer": "Informational update — not a trading signal.",
        "permissions": {"entry": False, "exit": False, "direction_switch": False,
                        "score": False, "confirm_cause": False},
    }
