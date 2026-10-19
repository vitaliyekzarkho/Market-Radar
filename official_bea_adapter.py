from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from source_provenance import enrich_item


OUTPUT_FILE = Path("data/macro/official_bea_facts.json")
CACHE_FILE = Path("data/macro/official_bea_facts_cache.json")
CURRENT_RELEASES_URL = "https://www.bea.gov/news/current-releases"
BEA_ROOT = "https://www.bea.gov"
TIMEOUT_SECONDS = 20
MAX_CACHE_AGE = timedelta(hours=24)
NEW_YORK = ZoneInfo("America/New_York")

MONTHS = {name.lower(): index for index, name in enumerate((
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
), start=1)}
MONTH_PATTERN = "|".join(name.title() for name in MONTHS)
ESTIMATE_STAGES = {"advance": "advance", "second": "second", "third": "third"}


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def visible_text(document: str) -> str:
    without_scripts = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", document, flags=re.I | re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", without_scripts))).strip()


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "MarketRadar/1.0", "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="replace")


def discover_release_urls(document: str) -> dict[str, str]:
    candidates: dict[str, set[str]] = {"personal_outlays": set(), "gdp": set()}
    for href, label in re.findall(r'<a\b[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', document, re.I | re.S):
        title = visible_text(label).lower()
        path = html.unescape(href)
        if "personal income and outlays" in title and "/news/" in path:
            candidates["personal_outlays"].add(urljoin(BEA_ROOT, path))
        if re.search(r"\bgdp\s*\((?:advance|second|third) estimate\)", title) and "/news/" in path:
            candidates["gdp"].add(urljoin(BEA_ROOT, path))
    return {name: next(iter(urls)) for name, urls in candidates.items() if len(urls) == 1}


def publication_time(text: str) -> str | None:
    match = re.search(
        rf"EMBARGOED UNTIL RELEASE AT\s+(\d{{1,2}}):(\d{{2}})\s*(a\.m\.|p\.m\.)\s+"
        rf"(?:EST|EDT),\s+\w+,\s+({MONTH_PATTERN})\s+(\d{{1,2}}),\s+(20\d{{2}})",
        text, re.I,
    )
    if not match:
        return None
    hour = int(match.group(1)) % 12 + (12 if match.group(3).lower().startswith("p") else 0)
    local = datetime(
        int(match.group(6)), MONTHS[match.group(4).lower()], int(match.group(5)),
        hour, int(match.group(2)), tzinfo=NEW_YORK,
    )
    return local.astimezone(timezone.utc).isoformat()


def signed_percent(verb: str, value: str) -> str:
    number = float(value)
    if verb.lower() in {"decreased", "declined", "fell", "dropped"}:
        number = -number
    return f"{number:g}%"


def personal_outlays_period(text: str) -> str | None:
    match = re.search(rf"Personal Income and Outlays,\s+({MONTH_PATTERN})\s+(20\d{{2}})", text, re.I)
    return f"{int(match.group(2)):04d}-{MONTHS[match.group(1).lower()]:02d}" if match else None


def personal_outlays_components(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    patterns = {
        "personal_income_mom": r"Personal income\s+(?P<verb>increased|rose|decreased|declined|fell).*?\((?P<value>\d+(?:\.\d+)?)\s+percent at a monthly rate\)",
        "personal_spending_mom": r"personal consumption expenditures \(PCE\)\s+(?P<verb>increased|rose|decreased|declined|fell).*?\((?P<value>\d+(?:\.\d+)?)\s+percent\)",
        "pce_price_index_mom": r"From the preceding month, the PCE price index.*?(?P<verb>increased|rose|decreased|declined|fell)\s+(?P<value>\d+(?:\.\d+)?)\s+percent",
        "core_pce_price_index_mom": r"From the preceding month, the PCE price index.*?Excluding food and energy, the PCE price index\s+(?P<verb>increased|rose|decreased|declined|fell)\s+(?P<value>\d+(?:\.\d+)?)\s+percent",
        "pce_price_index_yoy": r"From the same month one year ago, the PCE price index.*?(?P<verb>increased|rose|decreased|declined|fell)\s+(?P<value>\d+(?:\.\d+)?)\s+percent",
        "core_pce_price_index_yoy": r"From the same month one year ago, the PCE price index.*?Excluding food and energy, the PCE price index\s+(?P<verb>increased|rose|decreased|declined|fell)\s+(?P<value>\d+(?:\.\d+)?)\s+percent",
    }
    for indicator, pattern in patterns.items():
        match = re.search(pattern, text, re.I)
        if match:
            result[indicator] = signed_percent(match.group("verb"), match.group("value"))
    return result


def gdp_metadata(text: str) -> tuple[str | None, str | None]:
    match = re.search(
        r"GDP\s*\((Advance|Second|Third) Estimate\).*?([1-4])(?:st|nd|rd|th) Quarter\s+(20\d{2})",
        text, re.I,
    )
    if not match:
        return None, None
    return f"{int(match.group(3)):04d}-Q{int(match.group(2))}", ESTIMATE_STAGES[match.group(1).lower()]


def gdp_components(text: str) -> dict[str, str]:
    match = re.search(
        r"Real gross domestic product \(GDP\)\s+(?P<verb>increased|rose|decreased|declined|fell)\s+at an annual rate of\s+(?P<value>\d+(?:\.\d+)?)\s+percent",
        text, re.I,
    )
    return {"real_gdp_qoq_annualized": signed_percent(match.group("verb"), match.group("value"))} if match else {}


def make_facts(
    components: dict[str, str], report_period: str, published: str,
    source_url: str, observed_at: datetime, estimate_stage: str | None = None,
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for indicator, actual in components.items():
        fact_id = f"{indicator}:{report_period}" + (f":{estimate_stage}" if estimate_stage else "")
        fact = enrich_item({
            "fact_id": fact_id, "title": indicator.replace("_", " ").title(),
            "indicator": indicator, "report_period": report_period, "actual": actual,
            "estimate_stage": estimate_stage, "published_at_utc": published,
            "publication_time_status": "official_embargo_timestamp",
            "observed_at_utc": observed_at.astimezone(timezone.utc).isoformat(),
            "source": "U.S. Bureau of Economic Analysis", "source_url": source_url,
            "provenance": "official_primary", "verified_primary_source": True,
        }, observed_at, "official_release", "live")
        fact.update({
            "primary_source": True, "authoritative_fact": True,
            "source_transport": "official_primary_release",
        })
        facts.append(fact)
    return facts


def parse_release(document: str, release: str, source_url: str, observed_at: datetime) -> list[dict[str, Any]]:
    text = visible_text(document)
    published = publication_time(text)
    if not published:
        return []
    if release == "personal_outlays":
        period = personal_outlays_period(text)
        components = personal_outlays_components(text)
        stage = None
    else:
        period, stage = gdp_metadata(text)
        components = gdp_components(text)
    return make_facts(components, period, published, source_url, observed_at, stage) if period and components else []


def parse_diagnostic(document: str, release: str) -> dict[str, Any]:
    text = visible_text(document)
    period, stage = (gdp_metadata(text) if release == "gdp" else (personal_outlays_period(text), None))
    return {
        "document_chars": len(document), "visible_text_chars": len(text),
        "report_period_detected": period, "estimate_stage_detected": stage,
        "publication_time_detected": publication_time(text),
        "contains_embargo": "embargoed until release at" in text.lower(),
    }


def cache_usable(payload: dict[str, Any], now: datetime) -> bool:
    try:
        fetched = datetime.fromisoformat(str(payload.get("fetched_at_utc") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    releases = payload.get("releases") if isinstance(payload.get("releases"), dict) else {}
    return (
        all(isinstance(releases.get(name), dict) and releases[name].get("available") for name in ("personal_outlays", "gdp"))
        and now - fetched.astimezone(timezone.utc) <= MAX_CACHE_AGE
    )


def build_live(now: datetime) -> dict[str, Any]:
    facts: list[dict[str, Any]] = []
    statuses: dict[str, Any] = {}
    try:
        index = fetch_text(CURRENT_RELEASES_URL)
        urls = discover_release_urls(index)
    except (OSError, TimeoutError, urllib.error.URLError, ValueError) as exc:
        return {
            "schema_version": 1, "mode": "live", "fetched_at_utc": now.isoformat(),
            "facts": [], "releases": {}, "discovery_error": type(exc).__name__,
        }
    for release in ("personal_outlays", "gdp"):
        url = urls.get(release)
        if not url:
            statuses[release] = {"available": False, "status": "unique_release_not_found"}
            continue
        try:
            document = fetch_text(url)
            parsed = parse_release(document, release, url, now)
            statuses[release] = {
                "available": bool(parsed), "fact_count": len(parsed), "source_url": url,
                "status": "parsed" if parsed else "parse_incomplete",
                "diagnostic": parse_diagnostic(document, release),
            }
            facts.extend(parsed)
        except (OSError, TimeoutError, urllib.error.URLError, ValueError) as exc:
            statuses[release] = {"available": False, "reason": type(exc).__name__, "source_url": url}
    return {
        "schema_version": 1, "mode": "live", "fetched_at_utc": now.isoformat(),
        "facts": facts, "releases": statuses,
    }


def main() -> int:
    now = datetime.now(timezone.utc)
    live = build_live(now)
    complete = all(item.get("available") for item in live.get("releases", {}).values()) and len(live.get("releases", {})) == 2
    if complete:
        payload = live
        atomic_json(CACHE_FILE, live)
    else:
        cache = load_json(CACHE_FILE)
        payload = {**cache, "mode": "cache"} if cache_usable(cache, now) else live
    atomic_json(OUTPUT_FILE, payload)
    print(f"OFFICIAL_BEA: facts={len(payload.get('facts') or [])} | mode={payload.get('mode')}")
    print("OFFICIAL_BEA_ADAPTER: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
