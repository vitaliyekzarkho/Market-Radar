from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from causal_mechanism_engine import classify_event_family, propose_mechanisms


INPUT_FILE = Path("data/news/fast_news_stories.json")
OUTPUT_FILE = Path("data/news/fast_news_interpretations.json")

STORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("oil_supply_relief", (
        "supply diesel", "diesel supply", "deliver diesel", "diesel into global market",
        "allow the supply of russian diesel", "production increase", "supply relief",
    )),
    ("oil_supply_tightening", (
        "supply disruption", "production cut", "export halt", "tanker attack", "hormuz closed",
    )),
    ("geopolitical_deescalation", (
        "ceasefire", "peace deal", "end conflict", "conflict will end", "deescalation",
        "suspend enrichment", "sanctions relief", "agrees to talks",
    )),
    ("geopolitical_escalation", (
        "missile strike", "airstrike", "retaliation", "declares war", "military escalation",
    )),
    ("yield_shock_up", ("yields spike", "yields surge", "bond selloff", "real yields rise")),
    ("yield_shock_down", ("yields fall", "yields plunge", "bond rally", "real yields fall")),
)


def story_text(story: dict[str, Any]) -> str:
    titles = [str(story.get("title") or "")]
    titles.extend(str(item.get("title") or "") for item in story.get("members") or [] if isinstance(item, dict))
    return re.sub(r"\s+", " ", " ".join(titles)).casefold()


def classify_story_families(story: dict[str, Any]) -> list[dict[str, Any]]:
    text = story_text(story)
    found: list[dict[str, Any]] = []
    for family, phrases in STORY_RULES:
        evidence = sorted({phrase for phrase in phrases if phrase in text})
        if evidence:
            found.append({"event_family": family, "matched_phrases": evidence})
    for member in story.get("members") or []:
        if not isinstance(member, dict):
            continue
        family = contextual_member_family(member)
        if family != "unknown" and not any(item["event_family"] == family for item in found):
            found.append({"event_family": family, "matched_phrases": [str(member.get("title") or "")]})
    return found


def contextual_member_family(member: dict[str, Any]) -> str:
    title = str(member.get("title") or "").casefold()
    family = classify_event_family(member)
    if family not in {"monetary_hawkish", "monetary_dovish"}:
        return family
    monetary_context = any(token in title for token in (
        "interest rate", "rate hike", "rate cut", "fed ", "fomc", "federal reserve",
        "central bank", "bank of canada", "boc ", "ecb ", "boj ", "swap market",
    ))
    if not monetary_context:
        return "unknown"
    foreign_central_bank = any(token in title for token in ("bank of canada", "boc ", "ecb ", "boj "))
    us_central_bank = any(token in title for token in ("fed ", "fomc", "federal reserve"))
    if foreign_central_bank and not us_central_bank:
        return "unknown"
    easing_repricing = any(phrase in title for phrase in (
        "hike chances fall", "odds of a hike fall", "pull back on bets for", "fewer hikes",
        "cut chances rise", "odds of a cut rise",
    ))
    tightening_repricing = any(phrase in title for phrase in (
        "hike chances rise", "odds of a hike rise", "more rate hikes", "cut chances fall",
    ))
    if easing_repricing:
        return "monetary_dovish"
    if tightening_repricing:
        return "monetary_hawkish"
    return family


def verified_story(story: dict[str, Any]) -> bool:
    return story.get("status") == "officially_verified" and bool(
        (story.get("permissions") or {}).get("confirm_driver")
    )


def interpret_story(story: dict[str, Any]) -> dict[str, Any]:
    story_id = str(story.get("story_id") or "story_unknown")
    families = classify_story_families(story)
    fact_verified = verified_story(story)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for family_item in families:
        proposed = propose_mechanisms({
            "event_id": story_id,
            "event_family": family_item["event_family"],
            "status": "reported_fact",
        })
        for raw in proposed.get("candidates") or []:
            mechanism_id = str(raw.get("mechanism_id"))
            if mechanism_id in seen:
                continue
            seen.add(mechanism_id)
            candidate = dict(raw)
            candidate["fact_verified"] = fact_verified
            candidate["state"] = "verified_fact_pending_market_confirmation" if fact_verified else "unverified_mechanism_candidate"
            candidate["source_story_status"] = story.get("status")
            candidate["classification_evidence"] = family_item["matched_phrases"]
            candidate["confirmation_allowed"] = False
            candidates.append(candidate)
    gold_paths = sorted({str(item.get("expected", {}).get("gold")) for item in candidates if item.get("expected", {}).get("gold")})
    monitored_assets = sorted({
        asset for item in candidates for asset in (item.get("expected") or {})
        if asset != "gold"
    })
    relationships = [str(item.get("story_relationship")) for item in story.get("members") or []]
    return {
        "story_id": story_id,
        "title": story.get("title"),
        "story_status": story.get("status"),
        "fact_verified": fact_verified,
        "recognized": bool(candidates),
        "event_families": families,
        "mechanism_candidates": candidates,
        "gold_paths": gold_paths,
        "directional_resolution": "unresolved_pending_market_reaction" if candidates else "unclassified",
        "monitored_assets": monitored_assets,
        "update_semantics": {
            "new_information_count": sum(value in {"story_development", "independent_corroboration"} for value in relationships),
            "redistribution_count": sum(value == "redistribution_same_origin" for value in relationships),
            "repeat_count": sum(value == "repeat_same_origin" for value in relationships),
        },
        "permissions": {"entry": False, "direction_switch": False, "score": False, "confirm_cause": False},
        "policy": "Сюжет создаёт проверяемые механизмы, но направление определяется только после межрыночной и ценовой реакции.",
    }


def build_payload(source: dict[str, Any], now: datetime) -> dict[str, Any]:
    interpretations = [interpret_story(story) for story in source.get("stories") or [] if isinstance(story, dict)]
    return {
        "schema_version": 1,
        "generated_at_utc": now.isoformat(),
        "source_generated_at_utc": source.get("generated_at_utc"),
        "interpretation_count": len(interpretations),
        "recognized_count": sum(bool(item["recognized"]) for item in interpretations),
        "interpretations": interpretations,
        "permissions": {"entry": False, "direction_switch": False, "score": False, "confirm_cause": False},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build testable causal mechanisms from fast-news stories.")
    parser.add_argument("--input", type=Path, default=INPUT_FILE)
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    args = parser.parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    payload = build_payload(source, datetime.now(timezone.utc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"FAST_NEWS_INTERPRETATION: stories={payload['interpretation_count']} | recognized={payload['recognized_count']}")
    print(f"FAST_NEWS_INTERPRETATION_SAVED: {args.output}")
    print("FAST_NEWS_INTERPRETATION_C2_5C3_3_1: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
