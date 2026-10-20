"""Read-only private archive assessment. Never recreates original forecasts."""
import csv
import hashlib
import io
import json
import math
from pathlib import Path

from offline_validation import BASE_SHA, CONTRACT
from news_notification_policy import utc

SOURCES = {
    "news_archive": "news/fast_news_archive.jsonl",
    "stories": "news/fast_news_stories.json",
    "interpretations": "news/fast_news_interpretations.json",
    "event_history": "events/GOLD_pro_history.jsonl",
    "usd": "market_context/DXY.json",
    "yields": "macro/rates_context.json",
    "volume": "market_context/GOLD_pro_volume.json",
    "telegram_result": "telegram/GOLD_pro.json",
    "telegram_state": "runtime/GOLD_pro_telegram_state.json",
}
for timeframe in ("W1", "D1", "H4", "H1", "M15"):
    SOURCES["ohlcv_" + timeframe] = f"raw/GOLD_pro/{timeframe}.csv"
for pack in ("iran-trump-20260309-very-soon", "us-ppi-20260910-mixed",
             "fomc-20260916-hawkish-guidance", "ism-services-20261005"):
    for filename in ("manifest.json", "historical_event.json"):
        SOURCES[pack + "_" + filename] = f"causal_replay/{pack}/{filename}"


def _read(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("OUTSIDE_ARCHIVE_ROOT")
    raw = path.read_bytes()
    if path.suffix == ".jsonl":
        value = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
    elif path.suffix == ".csv":
        value = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    else:
        value = json.loads(raw)
    if path.read_bytes() != raw:
        raise ValueError("ARCHIVE_CHANGED_DURING_READ")
    return value, hashlib.sha256(raw).hexdigest()


def _records(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("events", "stories", "interpretations"):
            if isinstance(value.get(key), list):
                return value[key]
        return [value]
    raise ValueError("INVALID_ARCHIVE_SHAPE")


def assess_source(root, relative):
    try:
        value, fingerprint = _read(root, relative)
        rows = _records(value)
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("INVALID_RECORD")
        missing = {key: sum(not row.get(key) for row in rows)
                   for key in ("published_at_utc", "observed_at_utc", "available_at_utc")}
        invalid = 0
        for row in rows:
            if all(row.get(k) for k in missing):
                try:
                    if not utc(row["published_at_utc"]) <= utc(row["observed_at_utc"]) <= utc(row["available_at_utc"]):
                        invalid += 1
                except (ValueError, TypeError):
                    invalid += 1
        # No trusted acquisition/version ledger exists in these legacy formats.
        # Even complete self-declared timestamps cannot authenticate timeliness.
        return {"quality": "UNTRUSTED" if invalid else "PARTIAL",
                "verdict": "FAIL" if invalid else "INSUFFICIENT_EVIDENCE",
                "reason": "INVALID_CHRONOLOGY" if invalid else "NO_AUTHENTICATED_VERSION_AVAILABILITY",
                "content_sha256": fingerprint, "record_count": len(rows),
                "missing_fields": missing,
                "properties": [{"property": "archive_parseable", "quality": "VERIFIED",
                                "verdict": "PASS", "scope": "Structure only, not source truth or timeliness."}]}, value
    except FileNotFoundError:
        return {"quality": "MISSING", "verdict": "INSUFFICIENT_EVIDENCE",
                "reason": "ARCHIVE_NOT_FOUND"}, None
    except (ValueError, UnicodeError, OSError, TypeError):
        return {"quality": "UNTRUSTED", "verdict": "FAIL",
                "reason": "UNREADABLE_INVALID_OR_CHANGING_ARCHIVE"}, None


def _price(value, expected):
    try:
        number = float(value)
        return math.isfinite(number) and abs(number - expected) < 0.011
    except (ValueError, TypeError):
        return False


def xau_case(history):
    rows = history if isinstance(history, list) else []
    anchors = []
    for prefix, price in (("2026-10-19T07:54:39", 4178.91),
                          ("2026-10-20T08:09:36", 4119.97)):
        found = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            snap = row.get("current_snapshot")
            snap = snap if isinstance(snap, dict) else {}
            primary = snap.get("primary")
            primary = primary if isinstance(primary, dict) else {}
            target = primary.get("point_b")
            target = target if isinstance(target, dict) else {}
            if (str(row.get("generated_at_utc", "")).startswith(prefix)
                    and _price(snap.get("current_price"), price)
                    and primary.get("direction") == "bearish"
                    and _price(target.get("price"), 4110.91)):
                found.append(row)
        anchors.append({"reference_time_prefix": prefix, "record_found": bool(found),
                        "property": "recorded_anchor_presence",
                        "verdict": "PASS" if found else "INSUFFICIENT_EVIDENCE",
                        "quality": "PARTIAL" if found else "MISSING"})
    snapshots = [(r, r["current_snapshot"]) for r in rows if isinstance(r, dict)
                 and isinstance(r.get("current_snapshot"), dict)]
    nested = any(isinstance(s.get("ideas"), dict) and
                 isinstance(s["ideas"].get("nested_bullish_pullback"), dict) for _, s in snapshots)
    later = any(str(r.get("generated_at_utc", "")).startswith("2026-10-20")
                and _price(s.get("current_price"), 4133.20) for r, s in snapshots)
    return {"case": "xauusd_2026_10_19_20", "verdict": "INSUFFICIENT_EVIDENCE",
            "reason": "ORIGINAL_DECISION_INPUT_AVAILABILITY_NOT_ESTABLISHED",
            "anchors": anchors, "nested_candidate_record_found": nested,
            "later_price_record_found": later, "pwl_reached": "NOT_ESTABLISHED",
            "bullish_recognition_before_move": "NOT_ESTABLISHED",
            "gaps": ["initial_hypothesis_formation_not_authenticated",
                     "per_version_technical_input_availability_missing",
                     "news_usd_yields_availability_lineage_missing",
                     "complete_original_scenario_versions_missing",
                     "notification_generation_delivery_history_missing"],
            "limitation": "Anchor presence is not evidence of a timely forecast. No OHLC reconstruction."}


def assess_archives(archive_root=None):
    """Explicit optional data root; never searches or writes production paths."""
    sources, values = {}, {}
    root = Path(archive_root).resolve() if archive_root is not None else None
    for label, relative in sorted(SOURCES.items()):
        if root is None:
            sources[label] = {"quality": "MISSING", "verdict": "INSUFFICIENT_EVIDENCE",
                              "reason": "EXPLICIT_ARCHIVE_PATH_NOT_SUPPLIED"}
        else:
            sources[label], values[label] = assess_source(root, relative)
    # Include pack CSV evidence without reading arbitrary unlisted archive contents.
    if root is not None:
        for pack in sorted((root / "causal_replay").glob("*/raw/*.csv")):
            if pack.is_file():
                relative = pack.relative_to(root).as_posix()
                sources["pack_csv_" + hashlib.sha256(relative.encode()).hexdigest()[:16]], _ = assess_source(root, relative)
    return {"base_sha": BASE_SHA, "contract_version": CONTRACT, "mode": "historical",
            "verdict": "INSUFFICIENT_EVIDENCE",
            "reason": "NO_AUTHENTICATED_HISTORICAL_DECISION_LEDGER",
            "sources": sources, "xauusd": xau_case(values.get("event_history")),
            "limitations": ["Read-only inventory, not reconstructed Radar decisions.",
                            "PASS properties establish only their named scope.",
                            "Source hashes do not authenticate historical availability.",
                            "Private report: review before sharing; no raw texts or recipient identifiers emitted."]}
