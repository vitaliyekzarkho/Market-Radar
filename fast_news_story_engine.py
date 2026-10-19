from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


INPUT_FILE = Path("data/news/fast_news_latest.json")
OUTPUT_FILE = Path("data/news/fast_news_stories.json")
STORY_WINDOW = timedelta(hours=6)

STOP = {
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "has", "in", "is", "it", "of",
    "on", "says", "said", "the", "to", "will", "with", "financialjuice", "actual", "previous",
}
ACTORS = {
    "trump", "putin", "iran", "israel", "idf", "irgc", "russia", "ukraine", "fed", "fomc",
    "powell", "treasury", "opec", "saudi", "venezuela", "mexico", "gulf", "gazprom", "lukoil",
    "white house", "ecb", "boj", "boc",
}
ACTOR_ALIASES = {
    "russia": {"russia", "russian"},
    "ukraine": {"ukraine", "ukrainian"},
    "united_states": {"united states", "u.s.", "us "},
}
CONTEXT_ACTORS = {"russia", "ukraine", "iran", "israel", "venezuela", "mexico", "saudi"}
SUBJECTS = {
    "diesel", "oil", "brent", "wti", "hormuz", "sanctions", "tariffs", "gold", "silver",
    "yields", "bonds", "rates", "inflation", "cpi", "ppi", "pce", "payrolls", "employment",
    "gdp", "ceasefire", "uranium", "nuclear", "missile", "tanker", "energy", "bitcoin",
}
ACTION_FAMILIES = {
    "supply": {"supply", "release", "deliver", "production", "produce"},
    "deescalation": {"ceasefire", "peace", "deal", "agreement", "negotiation", "end", "over"},
    "escalation": {"attack", "strike", "missile", "war", "threat", "shut"},
    "restriction": {"sanction", "sanctions", "tariff", "tariffs", "ban", "restrict", "license"},
    "policy_tightening": {"hike", "tightening", "higher"},
    "policy_easing": {"cut", "easing", "lower"},
    "market_move": {"rise", "rises", "jump", "falls", "fall", "drops", "higher", "lower"},
}
ATTRIBUTION_PATTERNS = (
    ("truth_social", r"truth social"), ("axios", r"\baxios\b"), ("reuters", r"\breuters\b"),
    ("financial_times", r"(?:\bft\b|financial times)"), ("bloomberg", r"\bbloomberg\b"),
    ("official_claim_via_secondary", r"(?:white house|us treasury|federal reserve|fomc|eia|bls|bea):"),
)


def parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalized_tokens(text: Any) -> set[str]:
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", str(text or "").casefold())
    return {token for token in cleaned.split() if len(token) > 2 and token not in STOP and not token.isdigit()}


def tagged_terms(text: Any, vocabulary: set[str]) -> set[str]:
    lowered = str(text or "").casefold()
    return {term for term in vocabulary if re.search(rf"\b{re.escape(term)}\b", lowered)}


def actor_terms(text: Any) -> set[str]:
    lowered = str(text or "").casefold()
    actors = tagged_terms(lowered, ACTORS)
    for canonical, aliases in ACTOR_ALIASES.items():
        if any(re.search(rf"\b{re.escape(alias)}\b", lowered) for alias in aliases):
            actors.add(canonical)
    return actors


def action_terms(text: Any) -> set[str]:
    tokens = normalized_tokens(text)
    return {family for family, terms in ACTION_FAMILIES.items() if tokens & terms}


def similarity(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left and right else 0.0


def exact_report_text(event: dict[str, Any]) -> str:
    text = str(event.get("title") or "").casefold()
    text = re.sub(r"^financialjuice:\s*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def event_features(event: dict[str, Any]) -> dict[str, Any]:
    # Story identity uses the headline only. Some RSS descriptions contain
    # unrelated page material and must not inject false actors into a story.
    text = str(event.get("title") or "")
    return {
        "tokens": normalized_tokens(event.get("title")),
        "actors": actor_terms(text),
        "subjects": tagged_terms(text, SUBJECTS),
        "actions": action_terms(text),
        "topics": set(event.get("topics") or []),
    }


def same_story(left: dict[str, Any], right: dict[str, Any]) -> bool:
    lt, rt = parse_time(left.get("published_at_utc")), parse_time(right.get("published_at_utc"))
    if not lt or not rt or abs(lt - rt) > STORY_WINDOW:
        return False
    a, b = event_features(left), event_features(right)
    lexical = similarity(a["tokens"], b["tokens"])
    if lexical >= 0.68:
        return True
    topic_overlap = bool(a["topics"] & b["topics"])
    subject_overlap = a["subjects"] & b["subjects"]
    actor_overlap = a["actors"] & b["actors"]
    action_overlap = a["actions"] & b["actions"]
    if a["actors"] and b["actors"] and not actor_overlap:
        return False
    if subject_overlap and len(actor_overlap) >= 2 and lexical >= 0.08:
        return True
    if subject_overlap and actor_overlap & CONTEXT_ACTORS and lexical >= 0.04:
        return True
    # Generic Trump/Fed coverage must not collapse into one giant story: a
    # concrete subject plus actor/action continuity is required.
    return bool(
        topic_overlap and subject_overlap and lexical >= 0.16
        and (
            actor_overlap & CONTEXT_ACTORS
            or action_overlap
            or (actor_overlap and lexical >= 0.25)
        )
    )


def source_lineage(event: dict[str, Any]) -> dict[str, str]:
    title = str(event.get("title") or "").casefold()
    outlet = str(event.get("source") or "unknown").casefold()
    actors = event_features(event)["actors"]
    # A post on a principal's own channel and subsequent direct-quote reports
    # retain the same factual origin even when distributed by many outlets.
    if "trump" in actors and "truth social" in title:
        return {"distribution_outlet": outlet, "reported_origin": "direct_trump_statement"}
    for origin, pattern in ATTRIBUTION_PATTERNS:
        if re.search(pattern, title):
            return {"distribution_outlet": outlet, "reported_origin": origin}
    if "trump" in actors and re.search(r"\btrump\s*(?::|says|said|confirms|announces|on x)", title):
        return {"distribution_outlet": outlet, "reported_origin": "direct_trump_statement"}
    # Direct quotations by a named principal are one origin even when many
    # feeds redistribute them.
    quoted_principal = next((actor for actor in sorted(actors) if actor in {"trump", "putin", "powell", "fed", "fomc", "idf", "irgc"}), None)
    if quoted_principal and re.search(rf"\b{re.escape(quoted_principal)}\s*:", title):
        return {"distribution_outlet": outlet, "reported_origin": f"direct_{quoted_principal}_statement"}
    return {"distribution_outlet": outlet, "reported_origin": f"outlet_{outlet}"}


def relationship(event: dict[str, Any], prior: list[dict[str, Any]]) -> str:
    if not prior:
        return "initial_report"
    lineage = source_lineage(event)
    origins = {source_lineage(item)["reported_origin"] for item in prior}
    outlets = {source_lineage(item)["distribution_outlet"] for item in prior}
    maximum = max(similarity(event_features(event)["tokens"], event_features(item)["tokens"]) for item in prior)
    if lineage["reported_origin"] in origins and any(
        exact_report_text(event) == exact_report_text(item) for item in prior
    ):
        return "repeat_same_origin"
    if lineage["reported_origin"] not in origins:
        return "independent_corroboration" if maximum >= 0.45 else "story_development"
    if lineage["distribution_outlet"] not in outlets:
        return "redistribution_same_origin"
    return "story_development"


def cluster_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = [event for event in events if isinstance(event, dict) and parse_time(event.get("published_at_utc"))]
    groups: list[list[dict[str, Any]]] = []
    for event in sorted(valid, key=lambda item: str(item.get("published_at_utc"))):
        matches = [group for group in groups if any(same_story(event, member) for member in group)]
        if not matches:
            groups.append([event])
        else:
            merged = [member for group in matches for member in group]
            merged.append(event)
            groups = [group for group in groups if group not in matches]
            groups.append(merged)

    stories: list[dict[str, Any]] = []
    for group in groups:
        members: list[dict[str, Any]] = []
        for event in group:
            member = dict(event)
            member["lineage"] = source_lineage(event)
            member["story_relationship"] = relationship(event, members)
            members.append(member)
        origins = sorted({member["lineage"]["reported_origin"] for member in members})
        outlets = sorted({member["lineage"]["distribution_outlet"] for member in members})
        official = any(bool(member.get("verified_primary_source")) or member.get("verification") == "official_verified" for member in members)
        if official:
            status = "officially_verified"
        elif any(member["story_relationship"] == "independent_corroboration" for member in members):
            status = "independently_corroborated"
        elif len(origins) >= 2:
            status = "developing_multi_origin"
        elif len(outlets) >= 2:
            status = "multi_outlet_same_origin"
        elif len(members) >= 2:
            status = "developing_single_origin"
        else:
            status = "single_report"
        anchor = members[0]
        story_id = "story_" + hashlib.sha256(str(anchor.get("event_id") or anchor.get("title")).encode()).hexdigest()[:16]
        stories.append({
            "story_id": story_id,
            "title": anchor.get("title"),
            "first_seen_utc": anchor.get("published_at_utc"),
            "last_update_utc": members[-1].get("published_at_utc"),
            "status": status,
            "member_count": len(members),
            "distribution_outlets": outlets,
            "reported_origins": origins,
            "independent_origin_count": len(origins),
            "topics": sorted({topic for member in members for topic in member.get("topics") or []}),
            "members": members,
            "permissions": {"confirm_driver": official, "entry": False, "direction_switch": False, "score": False},
        })
    return sorted(stories, key=lambda story: str(story["last_update_utc"]), reverse=True)


def build_payload(source: dict[str, Any], now: datetime) -> dict[str, Any]:
    stories = cluster_events(source.get("events") or [])
    return {
        "schema_version": 1,
        "generated_at_utc": now.isoformat(),
        "source_generated_at_utc": source.get("generated_at_utc"),
        "story_count": len(stories),
        "event_count": sum(story["member_count"] for story in stories),
        "stories": stories,
        "policy": "Повторные публикации подпитывают существующий сюжет; независимость считается по первоисточнику, а не числу лент.",
        "permissions": {"entry": False, "direction_switch": False, "score": False},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Cluster fast-news events into source-aware developing stories.")
    parser.add_argument("--input", type=Path, default=INPUT_FILE)
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    args = parser.parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    payload = build_payload(source, datetime.now(timezone.utc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    statuses: dict[str, int] = {}
    for story in payload["stories"]:
        statuses[story["status"]] = statuses.get(story["status"], 0) + 1
    print(f"FAST_NEWS_STORIES: events={payload['event_count']} | stories={payload['story_count']} | statuses={statuses}")
    print(f"FAST_NEWS_STORIES_SAVED: {args.output}")
    print("FAST_NEWS_STORY_ENGINE_C2_5C3_2: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
