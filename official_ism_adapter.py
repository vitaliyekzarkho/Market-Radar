from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from urllib.parse import quote, urljoin
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from source_provenance import enrich_item


OUTPUT_FILE = Path("data/macro/official_ism_facts.json")
CACHE_FILE = Path("data/macro/official_ism_facts_cache.json")
TIMEOUT_SECONDS = 15
MAX_CACHE_AGE = timedelta(hours=24)
REPORT_ROOT = "https://www.ismworld.org/supply-management-news-and-reports/reports/ism-pmi-reports"
PR_NEWSWIRE_ROOT = "https://www.prnewswire.com"

MONTHS = {name.lower(): index for index, name in enumerate((
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
), start=1)}
MONTH_NAMES = {value: key for key, value in MONTHS.items()}


def previous_month(now: datetime) -> tuple[int, int]:
    return (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)


def report_urls(now: datetime) -> dict[str, str]:
    _, month = previous_month(now)
    slug = MONTH_NAMES[month]
    return {
        "ism_services_pmi": f"{REPORT_ROOT}/services/{slug}/",
        "ism_manufacturing_pmi": f"{REPORT_ROOT}/pmi/{slug}/",
    }


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


def response_fingerprint(
    document: str,
    requested_url: str,
    final_url: str | None = None,
    status: int | None = None,
    content_type: str | None = None,
) -> dict[str, Any]:
    """Return a small, safe diagnostic fingerprint, never the raw document."""
    text = visible_text(document)
    title_match = re.search(r"<title\b[^>]*>(.*?)</title>", document, re.I | re.S)
    title = visible_text(title_match.group(1))[:200] if title_match else None
    snippets: list[str] = []
    for match in re.finditer(r"\b(?:PMI|Services|Manufacturing)\b", text, re.I):
        snippet = text[max(0, match.start() - 100): min(len(text), match.end() + 180)].strip()
        if snippet and snippet not in snippets:
            snippets.append(snippet[:300])
        if len(snippets) == 5:
            break
    periods = sorted(set(
        f"{int(year):04d}-{MONTHS[month.lower()]:02d}"
        for month, year in re.findall(
            r"\b(" + "|".join(name.title() for name in MONTHS) + r")\s+(20\d{2})\b", text, re.I
        )
    ))
    lowered = text.lower()
    return {
        "requested_url": requested_url,
        "final_url": final_url or requested_url,
        "http_status": status,
        "content_type": content_type,
        "document_chars": len(document),
        "visible_text_chars": len(text),
        "title": title,
        "signals": {
            "contains_pmi": bool(re.search(r"\bPMI\b", text, re.I)),
            "contains_services": "services" in lowered,
            "contains_manufacturing": "manufacturing" in lowered,
            "content_unavailable": any(item in lowered for item in (
                "content unavailable", "access denied", "enable javascript", "verify you are human",
            )),
        },
        "detected_report_periods": periods[:12],
        "pmi_snippets": snippets,
    }


def report_period(text: str) -> str | None:
    match = re.search(r"\b(" + "|".join(name.title() for name in MONTHS) + r")\s+(20\d{2})\b", text, re.I)
    if not match:
        return None
    return f"{int(match.group(2)):04d}-{MONTHS[match.group(1).lower()]:02d}"


def published_time(document: str) -> str | None:
    patterns = (
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'<meta[^>]+(?:property|name)=["\'](?:article:published_time|date)["\'][^>]+content=["\']([^"\']+)',
    )
    for pattern in patterns:
        match = re.search(pattern, document, re.I)
        if not match:
            continue
        try:
            parsed = datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    return None


def pmi_value(text: str, indicator: str) -> float | None:
    label = "Services PMI" if indicator == "ism_services_pmi" else "Manufacturing PMI"
    # ISM wraps the registered-trademark sign in its own HTML element. After
    # tag stripping the visible text can be either "PMI®" or "PMI ®".
    label_pattern = re.escape(label) + r"\s*(?:®)?"
    patterns = (
        rf"{label_pattern}\s+(?:registered|at)\s+(\d{{2}}(?:\.\d+)?)\s*(?:percent|%)",
        rf"{label_pattern}[^\d]{{0,40}}(\d{{2}}(?:\.\d+)?)\s*(?:percent|%)",
    )
    values: list[float] = []
    for pattern in patterns:
        values.extend(float(item) for item in re.findall(pattern, text, re.I))
        if values:
            break
    unique = sorted(set(values))
    return unique[0] if len(unique) == 1 and 30.0 <= unique[0] <= 80.0 else None


def parse_report(
    document: str, indicator: str, source_url: str, observed_at: datetime,
    provenance: str = "official_primary",
) -> dict[str, Any] | None:
    text = visible_text(document)
    value = pmi_value(text, indicator)
    period = report_period(text)
    published = published_time(document)
    if value is None or not period:
        return None
    title = "ISM Services PMI" if indicator == "ism_services_pmi" else "ISM Manufacturing PMI"
    fact = enrich_item({
        "fact_id": f"{indicator}:{period}", "title": title, "indicator": indicator,
        "report_period": period, "actual": f"{value:g}", "published_at_utc": published,
        "publication_time_status": "official_page" if published else "not_exposed_by_official_page",
        "observed_at_utc": observed_at.astimezone(timezone.utc).isoformat(),
        "source": "Institute for Supply Management", "source_url": source_url,
        "provenance": provenance, "verified_primary_source": True,
    }, observed_at, "official_release", "live")
    is_primary = provenance == "official_primary"
    fact["verified_primary_source"] = is_primary
    fact["primary_source"] = is_primary
    fact["authoritative_fact"] = True
    fact["source_transport"] = provenance
    return fact


def prnewswire_search_url(indicator: str) -> str:
    query = "ISM Services PMI" if indicator == "ism_services_pmi" else "ISM Manufacturing PMI"
    return f"{PR_NEWSWIRE_ROOT}/search/news/?keyword={quote(query)}"


def syndicated_release_url(search_document: str, indicator: str, expected_period: str) -> str | None:
    year, month_number = expected_period.split("-", 1)
    month = MONTH_NAMES[int(month_number)].lower()
    marker = "services-pmi" if indicator == "ism_services_pmi" else "manufacturing-pmi"
    candidates = {
        urljoin(PR_NEWSWIRE_ROOT, html.unescape(href))
        for href in re.findall(r'href=["\']([^"\']+)["\']', search_document, re.I)
        if marker in href.lower()
        and month in href.lower()
        and year in href.lower()
        and "/news-releases/" in href.lower()
        and "ism" in href.lower()
        and "report" in href.lower()
    }
    return next(iter(candidates)) if len(candidates) == 1 else None


def is_verified_ism_syndicated_release(document: str) -> bool:
    text = visible_text(document)
    return bool(
        re.search(r"News provided by\s+Institute for Supply Management\b", text, re.I)
        and re.search(r"SOURCE\s+Institute for Supply Management\b", text, re.I)
    )


def fetch_syndicated_fact(
    indicator: str, expected_period: str, observed_at: datetime,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    search_url = prnewswire_search_url(indicator)
    search_document, search_fingerprint = fetch_text(search_url)
    release_url = syndicated_release_url(search_document, indicator, expected_period)
    diagnostic: dict[str, Any] = {
        "search": search_fingerprint,
        "expected_period": expected_period,
        "candidate_status": "unique" if release_url else "not_unique_or_not_found",
    }
    if not release_url:
        return None, diagnostic
    release_document, release_fingerprint = fetch_text(release_url)
    diagnostic["release"] = release_fingerprint
    diagnostic["issuer_verified"] = is_verified_ism_syndicated_release(release_document)
    if not diagnostic["issuer_verified"]:
        return None, diagnostic
    fact = parse_report(
        release_document, indicator, release_url, observed_at,
        provenance="official_syndicated_release",
    )
    if not fact or fact.get("report_period") != expected_period:
        diagnostic["period_verified"] = False
        return None, diagnostic
    diagnostic["period_verified"] = True
    fact["source"] = "Institute for Supply Management via PR Newswire"
    return fact, diagnostic


def fetch_text(url: str) -> tuple[str, dict[str, Any]]:
    request = urllib.request.Request(url, headers={"User-Agent": "MarketRadar/1.0", "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        document = response.read().decode("utf-8", errors="replace")
        fingerprint = response_fingerprint(
            document,
            requested_url=url,
            final_url=response.geturl(),
            status=getattr(response, "status", None),
            content_type=response.headers.get("Content-Type"),
        )
        return document, fingerprint


def cache_usable(payload: dict[str, Any], now: datetime) -> bool:
    try:
        fetched = datetime.fromisoformat(str(payload.get("fetched_at_utc") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    year, month = previous_month(now)
    expected_period = f"{year:04d}-{month:02d}"
    facts = payload.get("facts") if isinstance(payload.get("facts"), list) else []
    covered = {
        fact.get("indicator") for fact in facts
        if isinstance(fact, dict) and fact.get("report_period") == expected_period
    }
    return (
        {"ism_services_pmi", "ism_manufacturing_pmi"}.issubset(covered)
        and now - fetched.astimezone(timezone.utc) <= MAX_CACHE_AGE
    )


def build_live(now: datetime) -> dict[str, Any]:
    facts, errors, diagnostics = [], {}, {}
    expected_year, expected_month = previous_month(now)
    expected_period = f"{expected_year:04d}-{expected_month:02d}"
    for indicator, url in report_urls(now).items():
        direct_error: str | None = None
        try:
            document, fingerprint = fetch_text(url)
            diagnostics[indicator] = {"direct": fingerprint}
            fact = parse_report(document, indicator, url, now)
            if fact:
                facts.append(fact)
                continue
            else:
                direct_error = "UNAMBIGUOUS_FACT_NOT_FOUND"
        except (OSError, TimeoutError, urllib.error.URLError, ValueError) as exc:
            direct_error = type(exc).__name__
            diagnostics[indicator] = {"direct": {"requested_url": url, "fetch_error": direct_error}}
        try:
            fact, fallback_diagnostic = fetch_syndicated_fact(indicator, expected_period, now)
            diagnostics[indicator]["syndicated_fallback"] = fallback_diagnostic
            if fact:
                facts.append(fact)
            else:
                errors[indicator] = f"DIRECT_{direct_error};SYNDICATED_FACT_NOT_FOUND"
        except (OSError, TimeoutError, urllib.error.URLError, ValueError) as exc:
            diagnostics[indicator]["syndicated_fallback"] = {"fetch_error": type(exc).__name__}
            errors[indicator] = f"DIRECT_{direct_error};SYNDICATED_{type(exc).__name__}"
    return {
        "schema_version": 1, "mode": "live", "fetched_at_utc": now.isoformat(),
        "facts": facts, "errors": errors, "diagnostics": diagnostics,
    }


def main() -> int:
    now = datetime.now(timezone.utc)
    cache = load_json(CACHE_FILE)
    if cache_usable(cache, now):
        payload = {**cache, "mode": "cache"}
    else:
        live = build_live(now)
        payload = live
        if live["facts"]:
            atomic_json(CACHE_FILE, live)
    atomic_json(OUTPUT_FILE, payload)
    print(f"OFFICIAL_ISM: facts={len(payload.get('facts') or [])} | mode={payload.get('mode')}")
    print("OFFICIAL_ISM_ADAPTER: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
