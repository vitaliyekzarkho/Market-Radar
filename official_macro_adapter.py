from __future__ import annotations

import json
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from source_provenance import enrich_item


DATA_DIR = Path("data")
OUTPUT_FILE = DATA_DIR / "macro" / "official_releases.json"
CACHE_FILE = DATA_DIR / "macro" / "official_releases_cache.json"
TIMEOUT_SECONDS = 15
MAX_CACHE_AGE = timedelta(hours=12)
REFRESH_INTERVAL = timedelta(minutes=15)
RECENT_WINDOW = timedelta(days=7)

SOURCES = {
    "FOMC": {
        "url": "https://www.federalreserve.gov/feeds/press_monetary.xml",
        "home": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        "keywords": ("federal open market committee", "fomc", "monetary policy", "federal funds"),
    },
    "BLS": {
        "url": "https://www.bls.gov/feed/bls_latest.rss",
        "home": "https://www.bls.gov/schedule/news_release/",
        "keywords": ("consumer price", "producer price", "employment situation", "job openings", "earnings"),
    },
    "BEA": {
        "url": "https://apps.bea.gov/rss/rss.xml",
        "home": "https://www.bea.gov/news/schedule",
        "keywords": ("personal income", "personal consumption", "gross domestic product", "gdp", "pce"),
    },
}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "MarketRadar/1.0", "Accept": "application/rss+xml, application/xml, text/xml"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read()


def item_text(item: ET.Element, name: str) -> str:
    node = item.find(name)
    return str(node.text or "").strip() if node is not None else ""


def parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        result = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def parse_feed(source: str, xml_data: bytes, now: datetime) -> list[dict[str, Any]]:
    config = SOURCES[source]
    root = ET.fromstring(xml_data)
    releases: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        title = item_text(item, "title")
        description = item_text(item, "description")
        combined = f"{title} {description}".lower()
        if not any(keyword in combined for keyword in config["keywords"]):
            continue
        published = parse_date(item_text(item, "pubDate") or item_text(item, "date"))
        if published and now - published > RECENT_WINDOW:
            continue
        releases.append({
            "source": source,
            "title": title,
            "published_at_utc": published.isoformat() if published else None,
            "source_url": item_text(item, "link") or config["home"],
            "verified_primary_source": True,
        })
    return releases


def cache_usable(cache: dict[str, Any], now: datetime) -> bool:
    try:
        fetched = datetime.fromisoformat(str(cache["fetched_at_utc"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return False
    return isinstance(cache.get("sources"), dict) and now - fetched.astimezone(timezone.utc) <= MAX_CACHE_AGE


def build_live(now: datetime) -> dict[str, Any]:
    statuses: dict[str, Any] = {}
    releases: list[dict[str, Any]] = []
    for source, config in SOURCES.items():
        try:
            source_releases = parse_feed(source, fetch_bytes(config["url"]), now)
            statuses[source] = {"available": True, "release_count": len(source_releases), "source_url": config["home"]}
            releases.extend(source_releases)
        except (OSError, TimeoutError, urllib.error.URLError, ET.ParseError, ValueError) as exc:
            statuses[source] = {"available": False, "reason": type(exc).__name__, "source_url": config["home"]}
    return {
        "schema_version": 1,
        "mode": "live",
        "fetched_at_utc": now.isoformat(),
        "sources": statuses,
        "recent_releases": releases,
    }


def enrich_payload(payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    mode = str(payload.get("mode") or "live")
    releases = payload.get("recent_releases") if isinstance(payload.get("recent_releases"), list) else []
    return {
        **payload,
        "schema_version": 2,
        "recent_releases": [enrich_item(item, now, "official_release", mode) for item in releases if isinstance(item, dict)],
    }


def main() -> int:
    now = datetime.now(timezone.utc)
    cache = load_json(CACHE_FILE)
    if cache_usable(cache, now):
        try:
            cached_at = datetime.fromisoformat(str(cache["fetched_at_utc"]).replace("Z", "+00:00"))
        except ValueError:
            cached_at = now - REFRESH_INTERVAL
        if now - cached_at.astimezone(timezone.utc) < REFRESH_INTERVAL:
            payload = dict(cache)
            payload["mode"] = "cache"
        else:
            payload = build_live(now)
    else:
        payload = build_live(now)
    if not any(item.get("available") for item in payload["sources"].values()):
        if cache_usable(cache, now):
            payload = dict(cache)
            payload["mode"] = "cache"
    else:
        atomic_write_json(CACHE_FILE, payload)
    payload = enrich_payload(payload, now)
    atomic_write_json(OUTPUT_FILE, payload)
    states = " | ".join(f"{name}={'OK' if item.get('available') else 'NO_DATA'}" for name, item in payload["sources"].items())
    print(f"OFFICIAL_MACRO: {states} | mode={payload.get('mode')}")
    print("OFFICIAL_MACRO_ADAPTER: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
