from __future__ import annotations

import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from source_provenance import cluster_items, enrich_item


OUTPUT_FILE = Path("data") / "news" / "market_headlines.json"
CACHE_FILE = Path("data") / "news" / "market_headlines_cache.json"
QUERIES = {
    "rates_gold": "gold treasury yields bonds",
    "iran_deescalation": "Iran Hormuz sanctions oil agreement",
    "oil_geopolitics": "Brent oil geopolitical risk",
}


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def parse_feed(data: bytes, theme: str, now: datetime) -> list[dict[str, Any]]:
    root = ET.fromstring(data)
    output = []
    for item in root.findall(".//item"):
        title = str(item.findtext("title") or "").strip()
        link = str(item.findtext("link") or "").strip()
        publisher_node = item.find("source")
        reported_by = str(publisher_node.text or "").strip() if publisher_node is not None else ""
        publisher_url = str(publisher_node.attrib.get("url") or "").strip() if publisher_node is not None else ""
        try:
            published = parsedate_to_datetime(str(item.findtext("pubDate") or "")).astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        if now - published > timedelta(hours=36) or not link.startswith(("http://", "https://")):
            continue
        output.append({
            "title": title, "theme": theme, "published_at_utc": published.isoformat(),
            "source_url": link, "source": "Google News RSS",
            "provenance": "aggregated_headline", "verified_primary_source": False,
            "reported_by": reported_by or None,
            "reported_by_url": publisher_url if publisher_url.startswith(("http://", "https://")) else None,
        })
    return output


def fetch(now: datetime) -> dict[str, Any]:
    headlines: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for theme, query in QUERIES.items():
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query, "hl": "en", "gl": "US", "ceid": "US:en"})
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "MarketRadar/1.0"})
            with urllib.request.urlopen(request, timeout=12) as response:
                headlines.extend(parse_feed(response.read(), theme, now))
        except Exception as exc:
            errors[theme] = type(exc).__name__
    return {"generated_at_utc": now.isoformat(), "mode": "live", "headlines": headlines, "errors": errors}


def enrich_payload(payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    mode = str(payload.get("mode") or "live")
    headlines = payload.get("headlines") if isinstance(payload.get("headlines"), list) else []
    enriched = [enrich_item(item, now, "headline", mode) for item in headlines if isinstance(item, dict)]
    clusters = cluster_items(headlines, now, "headline", mode)
    return {**payload, "schema_version": 2, "headlines": enriched, "clusters": clusters}


def main() -> int:
    now = datetime.now(timezone.utc)
    payload = fetch(now)
    if payload["headlines"]:
        atomic_write(CACHE_FILE, payload)
    else:
        cached = load(CACHE_FILE)
        try:
            age = now - datetime.fromisoformat(str(cached.get("generated_at_utc")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            age = timedelta.max
        if cached.get("headlines") and age <= timedelta(hours=6):
            payload = {**cached, "mode": "cache", "live_errors": payload["errors"]}
    payload = enrich_payload(payload, now)
    atomic_write(OUTPUT_FILE, payload)
    print(f"MARKET_NEWS: headlines={len(payload.get('headlines') or [])} | mode={payload.get('mode', 'live')}")
    print("MARKET_NEWS_ADAPTER: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
