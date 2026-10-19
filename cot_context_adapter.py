from __future__ import annotations

import csv
import io
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


OUTPUT_FILE = Path("data") / "macro" / "cot_context.json"
CACHE_FILE = Path("data") / "macro" / "cot_context_cache.json"
SOURCE_URL = "https://www.cftc.gov/dea/newcot/f_disagg.txt"
CFTC_PAGE = "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm"
MAX_CACHE_AGE = timedelta(days=10)
REFRESH_INTERVAL = timedelta(hours=6)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def field(row: dict[str, str], fragment: str) -> str:
    for key, value in row.items():
        if fragment.lower() in str(key).lower():
            return str(value).strip()
    return ""


def integer(value: str) -> int | None:
    try:
        return int(value.replace(",", "").strip())
    except ValueError:
        return None


def parse_report(text: str) -> dict[str, Any]:
    raw_rows = list(csv.reader(io.StringIO(text)))
    if not raw_rows:
        return {"available": False, "reason": "GOLD_ROW_NOT_FOUND", "scoring_applied": False}
    first_name = str(raw_rows[0][0] if raw_rows[0] else "").strip()
    if "Market_and_Exchange_Names" in first_name:
        rows = list(csv.DictReader(io.StringIO(text)))
        gold = next((row for row in rows if field(row, "Market_and_Exchange_Names").upper().startswith("GOLD -")), None)
        if not gold:
            return {"available": False, "reason": "GOLD_ROW_NOT_FOUND", "scoring_applied": False}
        report_date = field(gold, "Report_Date_as_YYYY-MM-DD") or field(gold, "As_of_Date")
        long_value = integer(field(gold, "M_Money_Positions_Long_All"))
        short_value = integer(field(gold, "M_Money_Positions_Short_All"))
    else:
        gold_row = next((row for row in raw_rows if row and str(row[0]).strip().upper().startswith("GOLD -")), None)
        if not gold_row or len(gold_row) <= 14:
            return {"available": False, "reason": "GOLD_ROW_NOT_FOUND", "scoring_applied": False}
        # CFTC Disaggregated Futures-Only layout: date is column 2;
        # Managed Money long/short are columns 13 and 14.
        report_date = str(gold_row[2]).strip()
        long_value = integer(str(gold_row[13]))
        short_value = integer(str(gold_row[14]))
    if long_value is None or short_value is None:
        return {"available": False, "reason": "MANAGED_MONEY_FIELDS_NOT_FOUND", "scoring_applied": False}
    net = long_value - short_value
    direction = "bullish" if net > 0 else "bearish" if net < 0 else "neutral"
    return {
        "available": True,
        "report_date": report_date,
        "managed_money_long": long_value,
        "managed_money_short": short_value,
        "managed_money_net": net,
        "direction": direction,
        "source_url": CFTC_PAGE,
        "scoring_applied": False,
    }


def load_cache(max_age: timedelta = MAX_CACHE_AGE) -> dict[str, Any]:
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(str(payload["fetched_at_utc"]).replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - fetched.astimezone(timezone.utc) <= max_age:
            return payload
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        pass
    return {}


def main() -> int:
    now = datetime.now(timezone.utc)
    fresh = load_cache(REFRESH_INTERVAL)
    if fresh:
        payload = dict(fresh)
        payload["mode"] = "cache"
        atomic_write_json(OUTPUT_FILE, payload)
        context = payload["context"]
        print(f"COT_CONTEXT: {str(context.get('direction') or 'NO_DATA').upper()} | mode=cache")
        print("COT_CONTEXT_ADAPTER: PASSED")
        return 0
    try:
        request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "MarketRadar/1.0"})
        with urllib.request.urlopen(request, timeout=15) as response:
            context = parse_report(response.read().decode("utf-8-sig", errors="replace"))
        payload = {"schema_version": 1, "mode": "live", "fetched_at_utc": now.isoformat(), "context": context}
        if context.get("available"):
            atomic_write_json(CACHE_FILE, payload)
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        payload = load_cache() or {"schema_version": 1, "mode": "unavailable", "fetched_at_utc": now.isoformat(), "context": {"available": False, "reason": type(exc).__name__, "scoring_applied": False}}
        if payload.get("mode") != "unavailable":
            payload["mode"] = "cache"
    atomic_write_json(OUTPUT_FILE, payload)
    context = payload["context"]
    print(f"COT_CONTEXT: {str(context.get('direction') or 'NO_DATA').upper()} | mode={payload.get('mode')}")
    print("COT_CONTEXT_ADAPTER: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
