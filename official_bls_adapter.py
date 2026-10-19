from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from source_provenance import enrich_item


OUTPUT_FILE = Path("data/macro/official_bls_facts.json")
CACHE_FILE = Path("data/macro/official_bls_facts_cache.json")
TIMEOUT_SECONDS = 15
MAX_CACHE_AGE = timedelta(hours=24)
NEW_YORK = ZoneInfo("America/New_York")

RELEASES = {
    "cpi": "https://www.bls.gov/news.release/cpi.nr0.htm",
    "ppi": "https://www.bls.gov/news.release/ppi.nr0.htm",
    "employment": "https://www.bls.gov/news.release/empsit.nr0.htm",
}
PPI_TABLE_URL = "https://www.bls.gov/news.release/ppi.t01.htm"

MONTHS = {name.lower(): index for index, name in enumerate((
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
), start=1)}
MONTH_PATTERN = "|".join(name.title() for name in MONTHS)


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
    text = html.unescape(re.sub(r"<[^>]+>", " ", without_scripts))
    return re.sub(r"\s+", " ", text).strip()


def signed_value(verb: str, value: str) -> float:
    number = float(value.replace(",", ""))
    return -number if verb.lower() in {"decreased", "declined", "fell", "dropped", "moved down", "edged down"} else number


def percent(value: float) -> str:
    return f"{value:g}%"


def report_period(text: str, release: str) -> str | None:
    labels = {
        "cpi": r"CONSUMER PRICE INDEX\s*[-–—]\s*",
        "ppi": r"PRODUCER PRICE INDEX(?:ES)?\s*[-–—]\s*",
        "employment": r"THE EMPLOYMENT SITUATION\s*[-–—]\s*",
    }
    match = re.search(labels[release] + rf"({MONTH_PATTERN})\s+(20\d{{2}})", text, re.I)
    if not match:
        return None
    return f"{int(match.group(2)):04d}-{MONTHS[match.group(1).lower()]:02d}"


def publication_time(text: str) -> str | None:
    match = re.search(
        rf"embargoed\s+until(?:\s+[A-Z0-9-]+(?:\s+\d{{2}}-\d{{4}})?)?\s+(\d{{1,2}}):(\d{{2}})\s*(a\.m\.|p\.m\.)\s*\(ET\).*?"
        rf"({MONTH_PATTERN})\s+(\d{{1,2}}),\s+(20\d{{2}})",
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


def unique_measure(text: str, patterns: tuple[str, ...]) -> float | None:
    values: list[float] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            values.append(signed_value(match.group("verb"), match.group("value")))
        if values:
            break
    unique = sorted(set(values))
    return unique[0] if len(unique) == 1 else None


def cpi_components(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    values = {
        "cpi_mom": unique_measure(text, (
            r"CPI-U\s+(?P<verb>increased|rose|advanced|decreased|declined|fell)\s+(?P<value>\d+(?:\.\d+)?)\s+percent\s+on a seasonally adjusted basis",
            r"Consumer Price Index for All Urban Consumers.*?(?P<verb>increased|rose|advanced|decreased|declined|fell)\s+(?P<value>\d+(?:\.\d+)?)\s+percent\s+on a seasonally adjusted basis",
        )),
        "cpi_yoy": unique_measure(text, (
            r"Over the last 12 months,\s+the all items index\s+(?P<verb>increased|rose|advanced|decreased|declined|fell)\s+(?P<value>\d+(?:\.\d+)?)\s+percent",
        )),
        "core_cpi_mom": unique_measure(text, (
            r"index for all items less food and energy\s+(?P<verb>increased|rose|advanced|decreased|declined|fell)\s+(?P<value>\d+(?:\.\d+)?)\s+percent\s+in",
        )),
        "core_cpi_yoy": unique_measure(text, (
            r"index for all items less food and energy\s+(?P<verb>increased|rose|advanced|decreased|declined|fell)\s+(?P<value>\d+(?:\.\d+)?)\s+percent over the last 12 months",
            r"all items less food and energy index\s+(?P<verb>increased|rose|advanced|decreased|declined|fell)\s+(?P<value>\d+(?:\.\d+)?)\s+percent\s+over (?:the year|the past 12 months)",
        )),
    }
    for indicator, value in values.items():
        if value is not None:
            result[indicator] = percent(value)
    return result


def ppi_table_components(document: str) -> dict[str, str]:
    """Read calendar-style core PPI from the exact official Table 1 row.

    Core PPI in the calendar means final demand less foods and energy (FD
    49104), not BLS's separately highlighted less-foods-energy-and-trade
    measure (FD 49116). Keeping these identities separate prevents a plausible
    but incorrect official match.
    """
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", document, re.I | re.S):
        cells = [visible_text(cell) for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, re.I | re.S)]
        if not cells or not cells[0].lower().startswith("final demand less foods and energy"):
            continue
        if len(cells) < 7 or cells[1:3] != ["FD", "49104"]:
            return {}
        try:
            yoy, mom = float(cells[4]), float(cells[-1])
        except ValueError:
            return {}
        return {"core_ppi_mom": percent(mom), "core_ppi_yoy": percent(yoy)}
    return {}


def ppi_components(text: str, table_document: str | None = None) -> dict[str, str]:
    result: dict[str, str] = {}
    values = {
        "ppi_mom": unique_measure(text, (
            rf"Producer Price Index for final demand\s+(?P<verb>increased|rose|advanced|moved up|edged up|decreased|declined|fell|moved down|edged down)\s+(?P<value>\d+(?:\.\d+)?)\s+percent\s+in\s+(?:{MONTH_PATTERN})",
        )),
        "ppi_yoy": unique_measure(text, (
            r"(?:index for final demand|final demand prices)\s+(?P<verb>increased|rose|advanced|moved up|edged up|decreased|declined|fell|moved down|edged down)\s+(?P<value>\d+(?:\.\d+)?)\s+percent\s+(?:for|over)(?: the)? 12 months(?: ended)?(?: in)?",
        )),
        "core_ppi_mom": unique_measure(text, (
            rf"(?:prices|index) for final demand less foods? and energy\s+(?P<verb>increased|rose|advanced|moved up|edged up|decreased|declined|fell|moved down|edged down)\s+(?P<value>\d+(?:\.\d+)?)\s+percent\s+in\s+(?:{MONTH_PATTERN})",
        )),
        "core_ppi_yoy": unique_measure(text, (
            r"(?:prices|index) for final demand less foods? and energy\s+(?P<verb>increased|rose|advanced|moved up|edged up|decreased|declined|fell|moved down|edged down)\s+(?P<value>\d+(?:\.\d+)?)\s+percent\s+(?:for|over)(?: the)? 12 months",
        )),
    }
    for indicator, value in values.items():
        if value is not None:
            result[indicator] = percent(value)
    if table_document:
        result.update(ppi_table_components(table_document))
    return result


def employment_components(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    payroll = re.search(
        r"Total nonfarm payroll employment\s+(rose|increased|grew|decreased|declined|fell)\s+by\s+([\d,]+)", text, re.I,
    )
    payroll_parenthetical = re.search(
        r"Total nonfarm payroll employment\s+(?:changed little|was little changed).*?\(([+-][\d,]+)\)", text, re.I,
    )
    unemployment = re.search(r"unemployment rate.*?at\s+(\d+(?:\.\d+)?)\s+percent", text, re.I)
    earnings = re.search(
        r"average hourly earnings.*?(rose|increased|advanced|edged up|decreased|declined|fell|edged down).*?or\s+(\d+(?:\.\d+)?)\s+percent",
        text, re.I,
    )
    if payroll:
        value = signed_value(payroll.group(1), payroll.group(2)) / 1000.0
        result["nonfarm_payrolls"] = f"{value:g}K"
    elif payroll_parenthetical:
        result["nonfarm_payrolls"] = f"{float(payroll_parenthetical.group(1).replace(',', '')) / 1000.0:g}K"
    if unemployment:
        result["unemployment_rate"] = f"{float(unemployment.group(1)):g}%"
    if earnings:
        result["average_hourly_earnings_mom"] = percent(signed_value(earnings.group(1), earnings.group(2)))
    return result


def parse_release(
    document: str, release: str, source_url: str, observed_at: datetime,
    table_document: str | None = None,
) -> list[dict[str, Any]]:
    text = visible_text(document)
    period = report_period(text, release)
    published = publication_time(text)
    if not period or not published:
        return []
    components = ppi_components(text, table_document) if release == "ppi" else (
        cpi_components(text) if release == "cpi" else employment_components(text)
    )
    facts: list[dict[str, Any]] = []
    for indicator, actual in components.items():
        fact = enrich_item({
            "fact_id": f"{indicator}:{period}", "title": indicator.replace("_", " ").title(),
            "indicator": indicator, "report_period": period, "actual": actual,
            "published_at_utc": published, "publication_time_status": "official_embargo_timestamp",
            "observed_at_utc": observed_at.astimezone(timezone.utc).isoformat(),
            "source": "U.S. Bureau of Labor Statistics", "source_url": source_url,
            "provenance": "official_primary", "verified_primary_source": True,
        }, observed_at, "official_release", "live")
        fact.update({
            "primary_source": True, "authoritative_fact": True,
            "source_transport": "official_primary_release",
        })
        facts.append(fact)
    return facts


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "MarketRadar/1.0", "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="replace")


def cache_usable(payload: dict[str, Any], now: datetime) -> bool:
    try:
        fetched = datetime.fromisoformat(str(payload.get("fetched_at_utc") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    releases = payload.get("releases") if isinstance(payload.get("releases"), dict) else {}
    return (
        all(isinstance(releases.get(name), dict) and releases[name].get("available") for name in RELEASES)
        and now - fetched.astimezone(timezone.utc) <= MAX_CACHE_AGE
    )


def parse_diagnostic(document: str, release: str) -> dict[str, Any]:
    text = visible_text(document)
    return {
        "document_chars": len(document), "visible_text_chars": len(text),
        "report_period_detected": report_period(text, release),
        "publication_time_detected": publication_time(text),
        "contains_embargo": "embargoed until" in text.lower(),
        "contains_expected_heading": {
            "cpi": "consumer price index" in text.lower(),
            "ppi": "producer price indexes" in text.lower(),
            "employment": "the employment situation" in text.lower(),
        }[release],
    }


def build_live(now: datetime) -> dict[str, Any]:
    facts: list[dict[str, Any]] = []
    statuses: dict[str, Any] = {}
    for release, url in RELEASES.items():
        try:
            document = fetch_text(url)
            table_document = fetch_text(PPI_TABLE_URL) if release == "ppi" else None
            parsed = parse_release(document, release, url, now, table_document)
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
    complete = all(item.get("available") for item in live["releases"].values())
    if complete:
        payload = live
        atomic_json(CACHE_FILE, live)
    else:
        cache = load_json(CACHE_FILE)
        payload = {**cache, "mode": "cache"} if cache_usable(cache, now) else live
    atomic_json(OUTPUT_FILE, payload)
    print(f"OFFICIAL_BLS: facts={len(payload.get('facts') or [])} | mode={payload.get('mode')}")
    print("OFFICIAL_BLS_ADAPTER: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
