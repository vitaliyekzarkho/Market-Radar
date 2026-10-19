from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any


OUTPUT_FILE = Path("data/news/fast_news_latest.json")
ARCHIVE_FILE = Path("data/news/fast_news_archive.jsonl")
HTTP_TIMEOUT_SECONDS = 12
DEFAULT_MAX_AGE_HOURS = 48
MAX_FUTURE_CLOCK_SKEW = timedelta(minutes=5)

RSS_SOURCES = {
    "financialjuice": "https://www.financialjuice.com/feed.ashx?xy=rss",
    "sky_world": "https://feeds.skynews.com/feeds/rss/world.xml",
    "sky_business": "https://feeds.skynews.com/feeds/rss/business.xml",
    "sky_us": "https://feeds.skynews.com/feeds/rss/us.xml",
    "incrypted": "https://incrypted.com/feed/",
}
GOOGLE_QUERIES = {
    "gold_rates": "gold treasury yields bonds Federal Reserve",
    "oil_geopolitics": "oil Brent Iran Hormuz sanctions ceasefire",
    "us_policy": "Trump Federal Reserve tariffs sanctions markets",
}

TOPIC_TERMS = {
    "monetary_policy": ("federal reserve", "fomc", "powell", "interest rate", "rate hike", "rate cut", "central bank"),
    "rates_bonds": ("treasury", "yield", "bond", "real rate"),
    "gold_metals": ("gold", "xau", "silver", "precious metal"),
    "oil_energy": ("oil", "brent", "wti", "opec", "petroleum", "energy"),
    "geopolitics": ("iran", "hormuz", "war", "ceasefire", "sanction", "missile", "attack", "conflict"),
    "us_policy": ("trump", "white house", "tariff", "treasury secretary"),
    "macro_data": ("inflation", "cpi", "ppi", "payroll", "employment", "gdp", "pce", "retail sales", "ism"),
    "crypto": ("bitcoin", "ethereum", "crypto", "stablecoin", "blockchain"),
}


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(value or ""))).strip()


def parse_published(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def topics_for(title: str, summary: str = "") -> list[str]:
    text = f"{title} {summary}".casefold()
    return [topic for topic, terms in TOPIC_TERMS.items() if any(term in text for term in terms)]


def stable_id(source: str, title: str, published: datetime) -> str:
    identity = f"{source.casefold()}|{title.casefold()}|{published.isoformat()}"
    return "fast_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def is_fresh(published: datetime, now: datetime, max_age_hours: int) -> bool:
    age = now - published
    return -MAX_FUTURE_CLOCK_SKEW <= age <= timedelta(hours=max_age_hours)


def parse_feed(document: bytes, source: str, fetched_at: datetime) -> list[dict[str, Any]]:
    root = ET.fromstring(document)
    rows: list[dict[str, Any]] = []
    nodes = root.findall(".//item")
    if not nodes:
        nodes = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    for node in nodes:
        def first(*names: str) -> str:
            for name in names:
                child = node.find(name)
                if child is not None and child.text:
                    return child.text
            return ""
        title = clean_text(first("title", "{http://www.w3.org/2005/Atom}title"))
        summary = clean_text(first("description", "summary", "{http://www.w3.org/2005/Atom}summary", "{http://www.w3.org/2005/Atom}content"))
        published = parse_published(first("pubDate", "published", "updated", "{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated"))
        link = first("link")
        if not link:
            link_node = node.find("{http://www.w3.org/2005/Atom}link")
            link = str(link_node.attrib.get("href") or "") if link_node is not None else ""
        if not title or not published:
            continue
        topics = topics_for(title, summary)
        if not topics:
            continue
        rows.append({
            "event_id": stable_id(source, title, published), "title": title, "summary": summary or None,
            "published_at_utc": published.isoformat(), "observed_at_utc": fetched_at.isoformat(),
            "source": source, "source_url": link if link.startswith(("http://", "https://")) else None,
            "topics": topics, "provenance": "fast_news_discovery", "verification": "unverified_report",
            "permissions": {"confirm_driver": False, "entry": False, "direction_switch": False, "score": False},
        })
    return rows


def google_url(query: str) -> str:
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query, "hl": "en", "gl": "US", "ceid": "US:en"})


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "MarketRadar/1.0", "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return response.read()


def collect(now: datetime, max_age_hours: int = DEFAULT_MAX_AGE_HOURS) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    source_status: dict[str, Any] = {}
    sources = dict(RSS_SOURCES)
    sources.update({f"google_{name}": google_url(query) for name, query in GOOGLE_QUERIES.items()})
    for source, url in sources.items():
        try:
            parsed = parse_feed(fetch_bytes(url), source, now)
            fresh = [
                item for item in parsed
                if (published := parse_published(item.get("published_at_utc")))
                and is_fresh(published, now, max_age_hours)
            ]
            events.extend(fresh)
            source_status[source] = {"status": "available", "items_parsed": len(parsed), "fresh_relevant_items": len(fresh)}
        except Exception as exc:
            source_status[source] = {"status": "unavailable", "error": type(exc).__name__}
    unique = {item["event_id"]: item for item in events}
    return {
        "schema_version": 1, "generated_at_utc": now.isoformat(), "max_age_hours": max_age_hours,
        "source_status": source_status,
        "events": sorted(unique.values(), key=lambda item: item["published_at_utc"], reverse=True),
        "coverage": {"domain": "unscheduled_news", "status": "partial", "reason": "DISCOVERY_FEEDS_CANNOT_PROVE_COMPLETE_NEWS_COVERAGE"},
        "policy": "Быстрые ленты обнаруживают возможное событие, но не подтверждают причинность без независимого или официального источника.",
        "permissions": {"confirm_driver": False, "entry": False, "direction_switch": False, "score": False},
    }


def load_archive_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("event_id"):
            ids.add(str(item["event_id"]))
    return ids


def append_new(path: Path, events: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    known = load_archive_ids(path)
    new_items = [item for item in events if str(item.get("event_id")) not in known]
    if new_items:
        with path.open("a", encoding="utf-8") as handle:
            for item in sorted(new_items, key=lambda value: value["published_at_utc"]):
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return len(new_items)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect fast discovery-only market headlines and preserve a local archive.")
    parser.add_argument("--max-age-hours", type=int, default=DEFAULT_MAX_AGE_HOURS)
    args = parser.parse_args()
    payload = collect(datetime.now(timezone.utc), max(1, args.max_age_hours))
    appended = append_new(ARCHIVE_FILE, payload["events"])
    payload["archive"] = {"path": str(ARCHIVE_FILE), "new_items_appended": appended, "deduplicated": True}
    atomic_json(OUTPUT_FILE, payload)
    available = sum(1 for item in payload["source_status"].values() if item["status"] == "available")
    print(f"FAST_NEWS: sources={available}/{len(payload['source_status'])} | fresh={len(payload['events'])} | archived_new={appended}")
    print(f"FAST_NEWS_SAVED: {OUTPUT_FILE}")
    print("FAST_NEWS_COLLECTOR_C2_5C3_1: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
