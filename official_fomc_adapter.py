from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from io import BytesIO
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from source_provenance import enrich_item


OUTPUT_FILE = Path("data/macro/official_fomc_facts.json")
CACHE_FILE = Path("data/macro/official_fomc_facts_cache.json")
FEED_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"
FED_ROOT = "https://www.federalreserve.gov"
TIMEOUT_SECONDS = 20
MAX_CACHE_AGE = timedelta(days=35)
NEW_YORK = ZoneInfo("America/New_York")

MONTHS = {name.lower(): index for index, name in enumerate((
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
), start=1)}
MONTH_NAMES = {value: key for key, value in MONTHS.items()}
MONTH_PATTERN = "|".join(name.title() for name in MONTHS)


class TableRowParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_cell = False
        self.cell = ""
        self.row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"}:
            self.in_cell, self.cell = True, ""

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell += data

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.in_cell:
            self.row.append(re.sub(r"\s+", " ", self.cell).strip())
            self.in_cell = False
        elif tag == "tr" and self.row:
            self.rows.append(self.row)


def sep_table_rows(document: str) -> list[list[str]]:
    parser = TableRowParser()
    parser.feed(document)
    rows = parser.rows
    start = next((i for i, row in enumerate(rows) if row and row[0] == "Variable" and "Median" in " ".join(row)), None)
    if start is None:
        return []
    result: list[list[str]] = []
    for row in rows[start:]:
        result.append(row)
        if row and row[0].startswith("Federal funds rate"):
            continue
        if len(result) > 2 and result[-2][0].startswith("Federal funds rate") and row[0].lower().endswith("projection"):
            break
    return result


def sep_rate_distribution_rows(document: str) -> list[list[str]]:
    parser = TableRowParser()
    parser.feed(document)
    rows = parser.rows
    first_bin = next((i for i, row in enumerate(rows) if row and row[0].startswith("2.63-") and len(row) >= 10), None)
    if first_bin is None or first_bin < 2:
        return []
    result = [rows[first_bin - 2], rows[first_bin - 1]]
    range_pattern = re.compile(r"^\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?$")
    for row in rows[first_bin:]:
        if not row or not range_pattern.match(row[0]):
            break
        result.append(row)
    return result


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


def fetch_text(url: str, accept: str = "text/html") -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "MarketRadar/1.0", "Accept": accept})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8-sig", errors="replace")


def fetch_bytes(url: str, accept: str = "application/pdf") -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "MarketRadar/1.0", "Accept": accept})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read()


def statement_history(feed_document: str) -> list[tuple[str, datetime]]:
    root = ET.fromstring(feed_document)
    candidates: list[tuple[datetime, str]] = []
    for item in root.findall(".//item"):
        title = str(item.findtext("title") or "").strip().lower()
        link = str(item.findtext("link") or "").strip()
        published_text = str(item.findtext("pubDate") or "").strip()
        if title != "federal reserve issues fomc statement" or not link or not published_text:
            continue
        try:
            published = parsedate_to_datetime(published_text)
        except (TypeError, ValueError):
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        candidates.append((published.astimezone(timezone.utc), link))
    return [(link, published) for published, link in sorted(candidates, reverse=True)]


def latest_statement(feed_document: str) -> tuple[str, datetime] | None:
    history = statement_history(feed_document)
    return history[0] if history else None


def fraction_rate(value: str) -> float | None:
    text = value.strip().replace("–", "-").replace("—", "-")
    match = re.fullmatch(r"(\d+)(?:-(\d+)/(\d+))?", text)
    if not match:
        return None
    whole = float(match.group(1))
    return whole + (float(match.group(2)) / float(match.group(3)) if match.group(2) else 0.0)


def fraction_points(value: str) -> float | None:
    match = re.fullmatch(r"(\d+)/(\d+)", value.strip())
    return float(match.group(1)) / float(match.group(2)) if match else None


def statement_time(text: str) -> datetime | None:
    date_match = re.search(rf"({MONTH_PATTERN})\s+(\d{{1,2}}),\s+(20\d{{2}})", text, re.I)
    time_match = re.search(r"For release at\s+(\d{1,2}):(\d{2})\s*p\.m\.\s*(?:EST|EDT)", text, re.I)
    if not date_match or not time_match:
        return None
    local = datetime(
        int(date_match.group(3)), MONTHS[date_match.group(1).lower()], int(date_match.group(2)),
        int(time_match.group(1)) % 12 + 12, int(time_match.group(2)), tzinfo=NEW_YORK,
    )
    return local.astimezone(timezone.utc)


def decision_components(text: str) -> dict[str, Any] | None:
    changed = re.search(
        r"decided to\s+(raise|lower)\s+the target range for the federal funds rate by\s+"
        r"(\d+/\d+)\s+percentage point to\s+(\d+(?:-\d+/\d+)?)\s+to\s+(\d+(?:-\d+/\d+)?)\s+percent",
        text, re.I,
    )
    held = re.search(
        r"decided to\s+maintain\s+the target range for the federal funds rate at\s+"
        r"(\d+(?:-\d+/\d+)?)\s+to\s+(\d+(?:-\d+/\d+)?)\s+percent",
        text, re.I,
    )
    if changed:
        action = "hike" if changed.group(1).lower() == "raise" else "cut"
        points = fraction_points(changed.group(2))
        lower, upper = fraction_rate(changed.group(3)), fraction_rate(changed.group(4))
        if points is None or lower is None or upper is None:
            return None
        change_bps = round(points * 100) * (1 if action == "hike" else -1)
    elif held:
        action, change_bps = "hold", 0
        lower, upper = fraction_rate(held.group(1)), fraction_rate(held.group(2))
        if lower is None or upper is None:
            return None
    else:
        return None
    previous_lower = lower - change_bps / 100.0
    previous_upper = upper - change_bps / 100.0
    return {
        "action": action, "change_bps": change_bps,
        "target_lower_pct": lower, "target_upper_pct": upper,
        "previous_target_lower_pct": previous_lower,
        "previous_target_upper_pct": previous_upper,
    }


def projections_url(meeting_time: datetime) -> str:
    return f"{FED_ROOT}/monetarypolicy/fomcprojtabl{meeting_time:%Y%m%d}.htm"


def calendar_url(meeting_time: datetime) -> str:
    month = MONTH_NAMES[meeting_time.month].lower()
    return f"{FED_ROOT}/newsevents/{meeting_time.year:04d}-{month}.htm"


def press_conference_transcript_url(meeting_time: datetime) -> str:
    return f"{FED_ROOT}/mediacenter/files/FOMCpresconf{meeting_time:%Y%m%d}.pdf"


def extract_press_conference_pdf(payload: bytes, meeting_time: datetime) -> dict[str, Any]:
    """Extract an official Fed transcript without making it a hard pipeline dependency."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"verified": False, "status": "parser_dependency_missing", "text": ""}
    try:
        pages = [str(page.extract_text() or "") for page in PdfReader(BytesIO(payload)).pages]
    except Exception as exc:  # corrupt/changed official PDF must not stop other FOMC facts
        return {"verified": False, "status": "pdf_parse_failed", "error": type(exc).__name__, "text": ""}
    text = re.sub(r"\s+", " ", " ".join(pages)).strip()
    date_label = meeting_time.strftime("%B %d, %Y").replace(" 0", " ")
    verified = bool(
        text
        and "Transcript of" in text
        and "Press Conference" in text
        and date_label in text
        and re.search(r"\bCHAIR(?:MAN|WOMAN|)\s+[A-Z][A-Z'-]+\.", text, re.I)
    )
    return {
        "verified": verified,
        "status": "verified" if verified else "content_verification_failed",
        "text": text if verified else "",
        "page_count": len(pages),
    }


def split_press_conference(text: str) -> dict[str, str]:
    marker = re.search(r"(?:with that,\s*)?I(?:’|'|’)ll take (?:a few of )?your questions", text, re.I)
    if not marker:
        return {"prepared_remarks": text.strip(), "qa": ""}
    return {
        "prepared_remarks": text[:marker.end()].strip(),
        "qa": text[marker.end():].strip(),
    }


def projections_verified(document: str, meeting_time: datetime) -> bool:
    text = visible_text(document)
    return bool(
        "Summary of Economic Projections" in text
        and meeting_time.strftime("%B") in text
        and str(meeting_time.year) in text
        and "For release at 2:00 p.m." in text
    )


def press_conference_time(document: str, meeting_time: datetime) -> datetime | None:
    text = visible_text(document)
    pattern = rf"(\d{{1,2}}):(\d{{2}})\s*p\.m\.\s+FOMC Press Conference\s+{meeting_time.day}\b"
    match = re.search(pattern, text, re.I)
    if not match:
        return None
    local = datetime(
        meeting_time.year, meeting_time.month, meeting_time.day,
        int(match.group(1)) % 12 + 12, int(match.group(2)), tzinfo=NEW_YORK,
    )
    return local.astimezone(timezone.utc)


def make_fact(
    indicator: str, actual: str, meeting_id: str, published: datetime,
    source_url: str, observed_at: datetime, derivation: str = "direct_official_statement",
) -> dict[str, Any]:
    fact = enrich_item({
        "fact_id": f"{indicator}:{meeting_id}", "title": indicator.replace("_", " ").title(),
        "indicator": indicator, "report_period": meeting_id, "actual": actual,
        "published_at_utc": published.isoformat(), "publication_time_status": "official_release_timestamp",
        "observed_at_utc": observed_at.astimezone(timezone.utc).isoformat(),
        "source": "Federal Reserve Board", "source_url": source_url,
        "provenance": "official_primary", "verified_primary_source": True,
        "derivation": derivation,
    }, observed_at, "official_release", "live")
    fact.update({
        "primary_source": True, "authoritative_fact": True,
        "source_transport": "official_primary_release",
    })
    return fact


def parse_statement(document: str, source_url: str, observed_at: datetime) -> dict[str, Any] | None:
    text = visible_text(document)
    published = statement_time(text)
    decision = decision_components(text)
    if not published or not decision:
        return None
    meeting_id = published.date().isoformat()
    facts = [
        make_fact("fomc_target_upper_pct", f"{decision['target_upper_pct']:g}%", meeting_id, published, source_url, observed_at),
        make_fact("fomc_target_lower_pct", f"{decision['target_lower_pct']:g}%", meeting_id, published, source_url, observed_at),
        make_fact("fomc_rate_change_bps", f"{decision['change_bps']:+d}bp", meeting_id, published, source_url, observed_at),
        make_fact("fomc_policy_action", decision["action"], meeting_id, published, source_url, observed_at),
        make_fact(
            "fomc_previous_target_upper_pct", f"{decision['previous_target_upper_pct']:g}%",
            meeting_id, published, source_url, observed_at, "derived_from_official_change_and_new_range",
        ),
        make_fact(
            "fomc_previous_target_lower_pct", f"{decision['previous_target_lower_pct']:g}%",
            meeting_id, published, source_url, observed_at, "derived_from_official_change_and_new_range",
        ),
    ]
    return {"meeting_id": meeting_id, "published": published, "decision": decision, "facts": facts}


def cache_usable(payload: dict[str, Any], now: datetime) -> bool:
    try:
        fetched = datetime.fromisoformat(str(payload.get("fetched_at_utc") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    return (
        len(payload.get("facts") or []) >= 6
        and isinstance(payload.get("timepoints"), dict)
        and now - fetched.astimezone(timezone.utc) <= MAX_CACHE_AGE
    )


def build_live(now: datetime) -> dict[str, Any]:
    try:
        history = statement_history(fetch_text(FEED_URL, "application/rss+xml"))
        if not history:
            raise ValueError("STATEMENT_NOT_FOUND")
        statement_url, feed_time = history[0]
        statement_document = fetch_text(statement_url)
        parsed = parse_statement(statement_document, statement_url, now)
        if not parsed:
            raise ValueError("STATEMENT_PARSE_INCOMPLETE")
        previous_document: dict[str, Any] | None = None
        if len(history) >= 2:
            previous_url, previous_time = history[1]
            previous_document = {
                "source_url": previous_url,
                "published_at_utc": previous_time.isoformat(),
                "text": visible_text(fetch_text(previous_url)),
                "verified": True,
            }
        statement_at: datetime = parsed["published"]
        sep_url = projections_url(statement_at)
        sep_document = fetch_text(sep_url)
        sep = projections_verified(sep_document, statement_at)
        sep_rows = sep_table_rows(sep_document)
        sep_distribution = sep_rate_distribution_rows(sep_document)
        if len(sep_rows) < 13:
            raise ValueError("SEP_TABLE_INCOMPLETE")
        if len(sep_distribution) < 4:
            raise ValueError("SEP_RATE_DISTRIBUTION_INCOMPLETE")
        conference_url = calendar_url(statement_at)
        conference_at = press_conference_time(fetch_text(conference_url), statement_at)
        if not conference_at:
            raise ValueError("PRESS_CONFERENCE_TIME_NOT_FOUND")
        transcript_url = press_conference_transcript_url(statement_at)
        try:
            transcript = extract_press_conference_pdf(fetch_bytes(transcript_url), statement_at)
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            transcript = {"verified": False, "status": "not_published_or_unavailable", "error": type(exc).__name__, "text": ""}
        transcript["source_url"] = transcript_url
        transcript["published_at_utc"] = conference_at.isoformat()
        transcript.update(split_press_conference(str(transcript.get("text") or "")))
        transcript.pop("text", None)
        timepoints = {
            "statement": {"event_time_utc": statement_at.isoformat(), "source_url": statement_url, "verified": True},
            "sep": {"event_time_utc": statement_at.isoformat(), "source_url": sep_url, "verified": sep},
            "press_conference": {
                "event_time_utc": conference_at.isoformat(), "source_url": conference_url,
                "verified": True, "transcript_status": transcript["status"],
            },
        }
        return {
            "schema_version": 1, "mode": "live", "fetched_at_utc": now.isoformat(),
            "meeting_id": parsed["meeting_id"], "feed_published_at_utc": feed_time.isoformat(),
            "decision": parsed["decision"], "facts": parsed["facts"], "timepoints": timepoints,
            "statement_documents": {
                "current": {
                    "source_url": statement_url,
                    "published_at_utc": statement_at.isoformat(),
                    "text": visible_text(statement_document),
                    "verified": True,
                },
                "previous": previous_document,
            },
            "sep_document": {
                "source_url": sep_url,
                "published_at_utc": statement_at.isoformat(),
                "verified": sep,
                "table_rows": sep_rows,
                "rate_distribution_rows": sep_distribution,
            },
            "press_conference_document": transcript,
            "interpretation_status": "not_assigned_in_c1",
        }
    except (OSError, TimeoutError, urllib.error.URLError, ET.ParseError, ValueError) as exc:
        return {
            "schema_version": 1, "mode": "live", "fetched_at_utc": now.isoformat(),
            "facts": [], "timepoints": {}, "error": str(exc) or type(exc).__name__,
        }


def main() -> int:
    now = datetime.now(timezone.utc)
    live = build_live(now)
    complete = len(live.get("facts") or []) >= 6 and all(
        point.get("verified") for point in (live.get("timepoints") or {}).values()
    )
    if complete:
        payload = live
        atomic_json(CACHE_FILE, live)
    else:
        cache = load_json(CACHE_FILE)
        payload = {**cache, "mode": "cache"} if cache_usable(cache, now) else live
    atomic_json(OUTPUT_FILE, payload)
    print(f"OFFICIAL_FOMC: facts={len(payload.get('facts') or [])} | mode={payload.get('mode')}")
    print("OFFICIAL_FOMC_ADAPTER: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
