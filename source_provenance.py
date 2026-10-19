from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse


PRIMARY_DOMAINS = {
    "federalreserve.gov", "bls.gov", "bea.gov", "treasury.gov", "eia.gov",
    "whitehouse.gov", "state.gov", "cftc.gov", "defense.gov", "centcom.mil",
    # ISM is the first-party publisher of its own PMI reports. This permission
    # is limited downstream to ISM indicator facts and is not a general-news
    # trust grant.
    "ismworld.org",
}
OFFICIAL_SYNDICATION_DOMAINS = {"prnewswire.com"}
AGGREGATOR_DOMAINS = {"news.google.com"}
CALENDAR_DOMAINS = {"forexfactory.com", "faireconomy.media", "tradingeconomics.com"}

SOURCE_RANK = {
    "official_primary": 0,
    "official_syndicated_release": 1,
    "direct_statement": 1,
    "secondary_reporting": 2,
    "licensed_calendar": 3,
    "aggregated_headline": 4,
    "unknown": 5,
}

FRESHNESS_WINDOWS = {
    "headline": (timedelta(hours=2), timedelta(hours=12), timedelta(hours=36)),
    "official_release": (timedelta(hours=24), timedelta(days=3), timedelta(days=7)),
    "calendar": (timedelta(hours=2), timedelta(hours=6), timedelta(hours=12)),
}

STOP_WORDS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "is", "of",
    "on", "says", "said", "the", "to", "with", "after", "before", "amid",
}


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


def domain_from_url(url: Any) -> str:
    text = str(url or "").strip()
    if not text.startswith(("https://", "http://")):
        return ""
    host = (urlparse(text).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def domain_matches(domain: str, allowed: set[str]) -> bool:
    return any(domain == item or domain.endswith("." + item) for item in allowed)


def classify_source(
    source_url: Any,
    declared_provenance: Any = None,
    verified_primary_source: bool = False,
) -> dict[str, Any]:
    domain = domain_from_url(source_url)
    declared = str(declared_provenance or "").strip().lower()
    official_domain = domain_matches(domain, PRIMARY_DOMAINS)
    aggregator_domain = domain_matches(domain, AGGREGATOR_DOMAINS)
    calendar_domain = domain_matches(domain, CALENDAR_DOMAINS)
    official_syndication_domain = domain_matches(domain, OFFICIAL_SYNDICATION_DOMAINS)

    if official_domain:
        kind = "official_primary"
    elif official_syndication_domain and declared == "official_syndicated_release":
        kind = "official_syndicated_release"
    elif aggregator_domain or declared == "aggregated_headline":
        kind = "aggregated_headline"
    elif calendar_domain:
        kind = "licensed_calendar"
    elif declared in SOURCE_RANK:
        kind = declared
    else:
        kind = "unknown"

    primary_verified = bool(official_domain and verified_primary_source)
    if official_domain and declared == "official_primary":
        primary_verified = True
    syndicated_verified = bool(
        official_syndication_domain and declared == "official_syndicated_release" and verified_primary_source
    )
    return {
        "kind": kind,
        "domain": domain or None,
        "url_valid": bool(domain),
        "primary_verified": primary_verified,
        "fact_permission": primary_verified or syndicated_verified,
        "schedule_permission": kind in {"official_primary", "licensed_calendar"},
        "discovery_only": kind == "aggregated_headline",
    }


def freshness_status(
    timestamp: Any,
    now: datetime,
    content_type: str,
    cache_mode: str = "live",
) -> dict[str, Any]:
    observed = parse_time(timestamp)
    if not observed:
        return {"status": "unknown", "age_seconds": None, "cache_mode": cache_mode}
    age = max(timedelta(0), now.astimezone(timezone.utc) - observed)
    fresh, recent, usable = FRESHNESS_WINDOWS.get(content_type, FRESHNESS_WINDOWS["headline"])
    if age <= fresh and cache_mode == "live":
        status = "fresh"
    elif age <= recent:
        status = "recent"
    elif age <= usable:
        status = "stale"
    else:
        status = "expired"
    return {"status": status, "age_seconds": round(age.total_seconds()), "cache_mode": cache_mode}


def canonical_title(value: Any) -> str:
    title = str(value or "").strip().lower()
    # Google News commonly appends the publisher after a final separator.
    title = re.split(r"\s[-–—]\s", title, maxsplit=1)[0]
    tokens = re.findall(r"[a-z0-9]+", title)
    return " ".join(token for token in tokens if token not in STOP_WORDS)


def title_similarity(left: Any, right: Any) -> float:
    a = set(canonical_title(left).split())
    b = set(canonical_title(right).split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def enrich_item(item: dict[str, Any], now: datetime, content_type: str, cache_mode: str = "live") -> dict[str, Any]:
    enriched = dict(item)
    provenance = classify_source(
        item.get("source_url"), item.get("provenance"), bool(item.get("verified_primary_source")),
    )
    timestamp = (
        item.get("published_at_utc") or item.get("fetched_at_utc")
        or item.get("observed_at_utc") or item.get("event_time_utc")
    )
    enriched["provenance"] = provenance
    enriched["freshness"] = freshness_status(timestamp, now, content_type, cache_mode)
    enriched["canonical_title"] = canonical_title(item.get("title") or item.get("event"))
    return enriched


def cluster_items(
    items: list[dict[str, Any]],
    now: datetime,
    content_type: str = "headline",
    cache_mode: str = "live",
    time_window: timedelta = timedelta(hours=6),
    similarity_threshold: float = 0.72,
) -> list[dict[str, Any]]:
    enriched = [enrich_item(item, now, content_type, cache_mode) for item in items if isinstance(item, dict)]
    groups: list[list[dict[str, Any]]] = []
    for item in sorted(enriched, key=lambda value: str(value.get("published_at_utc") or value.get("event_time_utc") or "")):
        item_time = parse_time(item.get("published_at_utc") or item.get("event_time_utc"))
        matched: list[dict[str, Any]] | None = None
        for group in groups:
            anchor = group[0]
            anchor_time = parse_time(anchor.get("published_at_utc") or anchor.get("event_time_utc"))
            same_theme = not item.get("theme") or not anchor.get("theme") or item.get("theme") == anchor.get("theme")
            time_close = bool(item_time and anchor_time and abs(item_time - anchor_time) <= time_window)
            if same_theme and time_close and title_similarity(item.get("title"), anchor.get("title")) >= similarity_threshold:
                matched = group
                break
        if matched is not None:
            matched.append(item)
        else:
            groups.append([item])

    clusters: list[dict[str, Any]] = []
    for group in groups:
        members = sorted(group, key=lambda value: (
            SOURCE_RANK.get(value.get("provenance", {}).get("kind"), 99),
            str(value.get("published_at_utc") or value.get("event_time_utc") or ""),
        ))
        best = members[0]
        identity = "|".join((
            str(best.get("theme") or "unknown"),
            canonical_title(best.get("title")),
            str(best.get("published_at_utc") or best.get("event_time_utc") or "")[:13],
        ))
        cluster_id = "src_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        independent_domains = sorted({
            str(member.get("provenance", {}).get("domain"))
            for member in members if member.get("provenance", {}).get("domain")
        })
        clusters.append({
            "cluster_id": cluster_id,
            "title": best.get("title"),
            "theme": best.get("theme"),
            "published_at_utc": best.get("published_at_utc") or best.get("event_time_utc"),
            "best_source": best,
            "members": members,
            "member_count": len(members),
            "independent_domains": independent_domains,
            "primary_fact_verified": any(member.get("provenance", {}).get("fact_permission") for member in members),
        })
    return clusters
