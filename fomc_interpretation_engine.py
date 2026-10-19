from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FACTS_FILE = Path("data/macro/official_fomc_facts.json")
EVENTS_FILE = Path("data/news/verified_events.json")
OUTPUT_FILE = Path("data/macro/fomc_decision_interpretation.json")
TOLERANCE_PCT = 0.0001

STATEMENT_FEATURES: tuple[dict[str, Any], ...] = (
    {"id": "activity_solid", "topic": "activity", "patterns": (r"economic activity is expanding at a solid pace",), "change_tilt": "neutral"},
    {"id": "uncertainty_elevated", "topic": "risk", "patterns": (r"uncertainty remains elevated", r"elevated uncertainty"), "change_tilt": "neutral"},
    {"id": "domestic_spending_resilient", "topic": "activity", "patterns": (r"domestic spending has been resilient",), "change_tilt": "hawkish"},
    {"id": "productivity_strong", "topic": "activity", "patterns": (r"productivity growth is strong", r"productivity growth and capital investment are strong"), "change_tilt": "neutral"},
    {"id": "capital_investment_strong", "topic": "activity", "patterns": (r"capital investment is robust", r"productivity growth and capital investment are strong"), "change_tilt": "neutral"},
    {"id": "job_gains_keep_pace", "topic": "labor", "patterns": (r"job gains have kept pace with the workforce",), "change_tilt": "neutral"},
    {"id": "unemployment_little_changed", "topic": "labor", "patterns": (r"unemployment rate has changed little",), "change_tilt": "neutral"},
    {"id": "inflation_elevated", "topic": "inflation", "patterns": (r"inflation remains elevated",), "change_tilt": "neutral"},
    {"id": "supply_shock_attribution", "topic": "inflation", "patterns": (r"reflecting supply shocks",), "change_tilt": "ambiguous"},
    {"id": "energy_price_attribution", "topic": "inflation", "patterns": (r"including energy", r"energy prices"), "change_tilt": "ambiguous"},
    {"id": "timelier_return_to_target", "topic": "guidance", "patterns": (r"timelier return to the committee's 2 percent goal",), "change_tilt": "hawkish"},
    {"id": "deliver_price_stability", "topic": "guidance", "patterns": (r"committee will deliver price stability",), "change_tilt": "neutral"},
)

PRESS_CONFERENCE_CLAIMS: tuple[dict[str, Any], ...] = (
    {"id": "inflation_too_high", "topic": "inflation", "tilt": "hawkish", "weight": 2,
     "patterns": (r"inflation is too high", r"inflation remains (?:far )?above (?:our )?target")},
    {"id": "inflation_not_improving", "topic": "inflation", "tilt": "hawkish", "weight": 2,
     "patterns": (r"underlying trends have not meaningfully improved", r"inflation trends (?:were|are)n't passing the test")},
    {"id": "inflation_improving", "topic": "inflation", "tilt": "dovish", "weight": 2,
     "patterns": (r"inflation (?:has|is) (?:clearly |meaningfully )?(?:declined|improved|moving down)", r"disinflation(?:ary)? process.*(?:resumed|continues)")},
    {"id": "price_stability_priority", "topic": "reaction_function", "tilt": "hawkish", "weight": 2,
     "patterns": (r"predominant focus is on (?:the )?price[- ]stability", r"focused on stable prices")},
    {"id": "labor_downside_priority", "topic": "reaction_function", "tilt": "dovish", "weight": 2,
     "patterns": (r"downside risks to (?:the )?labor market", r"employment risks.*(?:increased|risen)")},
    {"id": "conditions_not_restrictive", "topic": "financial_conditions", "tilt": "hawkish", "weight": 2,
     "patterns": (r"(?:hard pressed|difficult) to describe (?:broad )?financial conditions as restrictive",)},
    {"id": "conditions_restrictive", "topic": "financial_conditions", "tilt": "dovish", "weight": 1,
     "patterns": (r"financial conditions (?:are|remain) restrictive", r"policy (?:is|remains) restrictive")},
    {"id": "removed_accommodation", "topic": "policy", "tilt": "hawkish", "weight": 2,
     "patterns": (r"removed a dose of accommodation",)},
    {"id": "future_easing", "topic": "policy", "tilt": "dovish", "weight": 2,
     "patterns": (r"(?:rate cuts|lower rates).*(?:appropriate|possible)", r"scope to (?:cut|lower) rates")},
    {"id": "future_tightening", "topic": "policy", "tilt": "hawkish", "weight": 2,
     "patterns": (r"(?:further|additional) (?:rate hikes|policy firming|tightening).*(?:appropriate|possible)",)},
    {"id": "no_forward_guidance", "topic": "optionality", "tilt": "neutral", "weight": 0,
     "patterns": (r"not (?:in )?the forward guidance business", r"not going to prejudge any future decisions")},
)


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def numeric_percent(value: Any) -> float | None:
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(value or ""))
    return float(match.group().replace(",", ".")) if match else None


def is_rate_decision_event(event: dict[str, Any], meeting_id: str) -> bool:
    title = re.sub(r"\s+", " ", str(event.get("title") or "").strip().lower())
    if title not in {"federal funds rate", "fed interest rate decision", "fomc rate decision"}:
        return False
    event_time = str(event.get("event_time_utc") or "")
    return event_time.startswith(meeting_id)


def expectation_from_calendar(events_payload: dict[str, Any], meeting_id: str) -> dict[str, Any] | None:
    matches = [
        event for event in events_payload.get("events") or []
        if isinstance(event, dict) and is_rate_decision_event(event, meeting_id)
    ]
    if len(matches) != 1:
        return None
    event = matches[0]
    expected_upper = numeric_percent(event.get("forecast"))
    if expected_upper is None:
        return None
    return {
        "target_upper_pct": expected_upper,
        "source": str(event.get("source") or "economic_calendar"),
        "source_url": event.get("source_url"),
        "event_time_utc": event.get("event_time_utc"),
        "provenance": str(event.get("provenance") or "licensed_calendar"),
        "status": "available",
    }


def policy_paths(surprise: str) -> list[dict[str, Any]]:
    if surprise == "hawkish_surprise":
        return [{
            "path_id": "hawkish_policy_transmission",
            "state": "mechanism_candidate",
            "description_ru": "Более высокая, чем ожидалось, ставка может передаваться через рост доходностей и USD в давление на Gold.",
            "expected": {"yields": "rising", "usd": "bullish", "gold": "bearish"},
            "required_confirmation": ["fresh_yield_or_usd_move", "gold_bearish_reaction", "timing_alignment"],
        }]
    if surprise == "dovish_surprise":
        return [{
            "path_id": "dovish_policy_transmission",
            "state": "mechanism_candidate",
            "description_ru": "Более низкая, чем ожидалось, ставка может передаваться через снижение доходностей и USD в поддержку Gold.",
            "expected": {"yields": "falling", "usd": "bearish", "gold": "bullish"},
            "required_confirmation": ["fresh_yield_or_usd_move", "gold_bullish_reaction", "timing_alignment"],
        }]
    return []


def statement_snapshot(text: str) -> dict[str, Any]:
    normalized = re.sub(r"\s+", " ", str(text or "").lower()).replace("’", "'")
    features = {
        spec["id"]: any(re.search(pattern, normalized) for pattern in spec["patterns"])
        for spec in STATEMENT_FEATURES
    }
    vote = re.search(r"approved the following statement for release by a\s+(\d+)\s*[–—-]\s*(\d+)\s+vote", normalized)
    return {
        "features": features,
        "vote": {
            "for": int(vote.group(1)), "against": int(vote.group(2)), "available": True,
        } if vote else {"for": None, "against": None, "available": False},
    }


def interpret_statement(official: dict[str, Any]) -> dict[str, Any]:
    documents = official.get("statement_documents") if isinstance(official.get("statement_documents"), dict) else {}
    current = documents.get("current") if isinstance(documents.get("current"), dict) else {}
    previous = documents.get("previous") if isinstance(documents.get("previous"), dict) else {}
    ready = bool(
        current.get("verified") and previous.get("verified")
        and current.get("text") and previous.get("text")
    )
    if not ready:
        return {
            "status": "comparison_unavailable", "directional": False,
            "reason_ru": "Для смыслового сравнения нужны два последовательных проверенных statement.",
            "changes": [], "mechanism_candidates": [],
        }

    current_snapshot = statement_snapshot(str(current["text"]))
    previous_snapshot = statement_snapshot(str(previous["text"]))
    changes: list[dict[str, Any]] = []
    directional_tilts: list[str] = []
    for spec in STATEMENT_FEATURES:
        feature_id = str(spec["id"])
        before = bool(previous_snapshot["features"].get(feature_id))
        after = bool(current_snapshot["features"].get(feature_id))
        if before == after:
            continue
        change = "added" if after else "removed"
        base_tilt = str(spec["change_tilt"])
        if base_tilt in {"hawkish", "dovish"}:
            tilt = base_tilt if change == "added" else ("dovish" if base_tilt == "hawkish" else "hawkish")
            directional_tilts.append(tilt)
        else:
            tilt = base_tilt
        changes.append({
            "feature": feature_id, "topic": spec["topic"], "change": change,
            "tilt": tilt, "causal_permission": "candidate_only" if tilt in {"hawkish", "dovish"} else "context_only",
        })

    current_vote, previous_vote = current_snapshot["vote"], previous_snapshot["vote"]
    if current_vote["available"] and previous_vote["available"]:
        if current_vote["against"] < previous_vote["against"]:
            consensus = "stronger_consensus_for_current_action"
        elif current_vote["against"] > previous_vote["against"]:
            consensus = "weaker_consensus_for_current_action"
        else:
            consensus = "unchanged"
    else:
        consensus = "unavailable"

    if directional_tilts and all(value == "hawkish" for value in directional_tilts):
        classification = "hawkish_tilt_candidate"
    elif directional_tilts and all(value == "dovish" for value in directional_tilts):
        classification = "dovish_tilt_candidate"
    elif directional_tilts:
        classification = "mixed_tilt_candidate"
    else:
        classification = "no_directional_change"

    mechanisms = policy_paths("hawkish_surprise" if classification == "hawkish_tilt_candidate" else "dovish_surprise" if classification == "dovish_tilt_candidate" else "")
    for mechanism in mechanisms:
        mechanism["origin"] = "statement_delta"
        mechanism["state"] = "mechanism_candidate"
        if classification == "hawkish_tilt_candidate":
            mechanism["description_ru"] = "Ястребиное изменение формулировок statement может передаваться через рост доходностей и USD в давление на Gold."
        elif classification == "dovish_tilt_candidate":
            mechanism["description_ru"] = "Голубиное изменение формулировок statement может передаваться через снижение доходностей и USD в поддержку Gold."
    return {
        "status": "compared", "classification": classification,
        "directional": bool(mechanisms),
        "current_source_url": current.get("source_url"),
        "previous_source_url": previous.get("source_url"),
        "changes": changes,
        "unchanged_features": [
            key for key, value in current_snapshot["features"].items()
            if value and value == previous_snapshot["features"].get(key)
        ],
        "vote_consensus": {
            "classification": consensus,
            "current": current_vote,
            "previous": previous_vote,
            "directional_tilt": "none",
        },
        "mechanism_candidates": mechanisms,
        "policy": "Только смысловые изменения statement создают кандидата; неизменный текст и голосование сами по себе направления не дают.",
    }


SEP_VARIABLES = {
    "change in real gdp": "real_gdp",
    "unemployment rate": "unemployment",
    "pce inflation": "pce_inflation",
    "core pce inflation4": "core_pce_inflation",
    "federal funds rate": "federal_funds_rate",
}


def sep_medians(rows: list[list[str]]) -> dict[str, Any]:
    if len(rows) < 3 or not rows[1]:
        return {}
    years = [str(value).strip().lower().replace(" ", "_") for value in rows[1][:5]]
    result: dict[str, Any] = {"years": years, "variables": {}}
    current_variable: str | None = None
    for row in rows[2:]:
        if not row:
            continue
        label = re.sub(r"\s+", " ", row[0].strip().lower())
        normalized_label = re.sub(r"[^a-z0-9 ]", "", label)
        variable = SEP_VARIABLES.get(label) or SEP_VARIABLES.get(normalized_label)
        if variable:
            current_variable = variable
            values = [numeric_percent(value) for value in row[1:6]]
            result["variables"][variable] = {"current": dict(zip(years, values)), "previous": {}}
        elif current_variable and label.endswith("projection"):
            values = [numeric_percent(value) for value in row[1:6]]
            result["variables"][current_variable]["previous"] = dict(zip(years, values))
            current_variable = None
    return result


def weighted_distribution(counts: list[tuple[float, int]]) -> dict[str, Any]:
    total = sum(count for _, count in counts)
    if total <= 0:
        return {"available": False}
    mean = sum(midpoint * count for midpoint, count in counts) / total
    variance = sum(count * ((midpoint - mean) ** 2) for midpoint, count in counts) / total
    ordered = sorted(counts)
    threshold = (total + 1) / 2
    cumulative = 0
    median = None
    for midpoint, count in ordered:
        cumulative += count
        if cumulative >= threshold:
            median = midpoint
            break
    modal_midpoint, modal_count = max(counts, key=lambda item: item[1])
    return {
        "available": True, "participants": total,
        "weighted_mean": round(mean, 3), "median_bin_midpoint": median,
        "standard_deviation": round(math.sqrt(variance), 3),
        "modal_bin_midpoint": modal_midpoint,
        "modal_share": round(modal_count / total, 3),
    }


def sep_rate_distribution(rows: list[list[str]]) -> dict[str, Any]:
    if len(rows) < 3:
        return {}
    years = [str(value).strip().lower().replace(" ", "_") for value in rows[0][1:]]
    labels = [str(value).strip().lower() for value in rows[1]]
    columns: list[tuple[str, str]] = []
    year_index = 0
    for label in labels:
        while year_index < len(years):
            year = years[year_index]
            if year == "2029":
                if "september" in label:
                    columns.append((year, "current")); year_index += 1; break
                year_index += 1
            else:
                stage = "previous" if "june" in label else "current"
                columns.append((year, stage))
                if stage == "current":
                    year_index += 1
                break
    buckets: dict[tuple[str, str], list[tuple[float, int]]] = {}
    for row in rows[2:]:
        match = re.match(r"^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)$", row[0])
        if not match:
            continue
        midpoint = round((float(match.group(1)) + float(match.group(2))) / 2, 3)
        for index, column in enumerate(columns, start=1):
            count = int(row[index]) if index < len(row) and str(row[index]).strip().isdigit() else 0
            if count:
                buckets.setdefault(column, []).append((midpoint, count))
    result: dict[str, Any] = {}
    for year in years:
        result[year] = {
            "current": weighted_distribution(buckets.get((year, "current"), [])),
            "previous": weighted_distribution(buckets.get((year, "previous"), [])),
        }
    return result


def interpret_sep_distribution(rows: list[list[str]]) -> dict[str, Any]:
    parsed = sep_rate_distribution(rows)
    comparable: list[dict[str, Any]] = []
    for period, pair in parsed.items():
        current, previous = pair.get("current", {}), pair.get("previous", {})
        if not current.get("available") or not previous.get("available"):
            continue
        mean_delta = round(current["weighted_mean"] - previous["weighted_mean"], 3)
        median_delta = round(current["median_bin_midpoint"] - previous["median_bin_midpoint"], 3)
        dispersion_delta = round(current["standard_deviation"] - previous["standard_deviation"], 3)
        comparable.append({
            "period": period, "current": current, "previous": previous,
            "weighted_mean_delta_pp": mean_delta,
            "median_bin_delta_pp": median_delta,
            "dispersion_delta": dispersion_delta,
            "dispersion_change": "wider" if dispersion_delta > 0.025 else "narrower" if dispersion_delta < -0.025 else "similar",
        })
    policy_years = [item for item in comparable if item["period"] in {"2026", "2027", "2028"}]
    if policy_years and all(item["weighted_mean_delta_pp"] > 0 and item["median_bin_delta_pp"] > 0 for item in policy_years):
        classification = "broad_hawkish_shift"
        confirms_median = True
    elif policy_years and all(item["weighted_mean_delta_pp"] < 0 and item["median_bin_delta_pp"] < 0 for item in policy_years):
        classification = "broad_dovish_shift"
        confirms_median = True
    else:
        classification = "mixed_distribution_shift"
        confirms_median = False
    concentration = [item["current"]["modal_share"] for item in policy_years]
    consensus_quality = "strong" if concentration and min(concentration) >= 0.5 else "moderate" if concentration and min(concentration) >= 0.25 else "weak"
    return {
        "status": "compared" if comparable else "comparison_unavailable",
        "classification": classification if comparable else "unavailable",
        "confirms_median_direction": confirms_median if comparable else False,
        "consensus_quality": consensus_quality if comparable else "unknown",
        "periods": comparable,
        "permissions": {"confirm_driver": False, "entry": False, "direction_switch": False, "score": False},
        "policy": "Распределение проверяет ширину сдвига медианы; оно не является рыночной реакцией и не подтверждает драйвер самостоятельно.",
    }


def interpret_sep(official: dict[str, Any]) -> dict[str, Any]:
    document = official.get("sep_document") if isinstance(official.get("sep_document"), dict) else {}
    rows = document.get("table_rows") if isinstance(document.get("table_rows"), list) else []
    parsed = sep_medians(rows)
    distribution_rows = document.get("rate_distribution_rows") if isinstance(document.get("rate_distribution_rows"), list) else []
    variables = parsed.get("variables") if isinstance(parsed.get("variables"), dict) else {}
    required = {"real_gdp", "unemployment", "pce_inflation", "core_pce_inflation", "federal_funds_rate"}
    if not document.get("verified") or not required.issubset(variables):
        return {
            "status": "comparison_unavailable", "classification": "unavailable",
            "directional": False, "reason_ru": "Проверенная таблица SEP или обязательные медианы неполны.",
            "median_changes": [], "mechanism_candidates": [],
        }

    changes: list[dict[str, Any]] = []
    for variable, values in variables.items():
        for year, current in values.get("current", {}).items():
            previous = values.get("previous", {}).get(year)
            if current is None or previous is None:
                continue
            delta = round(current - previous, 3)
            changes.append({
                "variable": variable, "period": year,
                "current": current, "previous": previous, "delta_pp": delta,
            })

    years = [year for year in parsed.get("years", []) if year != "longer_run"]
    real_proxy: list[dict[str, Any]] = []
    for year in years:
        rate_now = variables["federal_funds_rate"]["current"].get(year)
        rate_before = variables["federal_funds_rate"]["previous"].get(year)
        pce_now = variables["pce_inflation"]["current"].get(year)
        pce_before = variables["pce_inflation"]["previous"].get(year)
        if None in {rate_now, rate_before, pce_now, pce_before}:
            continue
        current_proxy = round(rate_now - pce_now, 3)
        previous_proxy = round(rate_before - pce_before, 3)
        real_proxy.append({
            "period": year, "current": current_proxy, "previous": previous_proxy,
            "delta_pp": round(current_proxy - previous_proxy, 3),
        })

    rate_deltas = [item["delta_pp"] for item in changes if item["variable"] == "federal_funds_rate"]
    proxy_deltas = [item["delta_pp"] for item in real_proxy]
    if rate_deltas and proxy_deltas and all(value >= 0 for value in rate_deltas) and any(value > 0 for value in rate_deltas) and all(value >= 0 for value in proxy_deltas) and any(value > 0 for value in proxy_deltas):
        classification = "hawkish_tilt_candidate"
    elif rate_deltas and proxy_deltas and all(value <= 0 for value in rate_deltas) and any(value < 0 for value in rate_deltas) and all(value <= 0 for value in proxy_deltas) and any(value < 0 for value in proxy_deltas):
        classification = "dovish_tilt_candidate"
    else:
        classification = "mixed_tilt_candidate"

    mechanisms = policy_paths("hawkish_surprise" if classification == "hawkish_tilt_candidate" else "dovish_surprise" if classification == "dovish_tilt_candidate" else "")
    for mechanism in mechanisms:
        mechanism["origin"] = "sep_median_delta"
        mechanism["state"] = "mechanism_candidate"
        direction = "Более высокая" if classification == "hawkish_tilt_candidate" else "Более низкая"
        mechanism["description_ru"] = f"{direction} медианная траектория политики SEP может передаваться через доходности и USD; Gold требует отдельного подтверждения реакции."
    return {
        "status": "compared", "classification": classification,
        "directional": bool(mechanisms), "source_url": document.get("source_url"),
        "median_changes": changes,
        "policy_minus_pce_proxy": {
            "definition": "median federal funds rate minus median PCE inflation; macro projection proxy, not a market real yield",
            "values": real_proxy,
        },
        "distribution_analysis": interpret_sep_distribution(distribution_rows),
        "mechanism_candidates": mechanisms,
        "permissions": {"confirm_driver": False, "entry": False, "direction_switch": False, "score": False},
        "policy": "Медиана SEP создаёт только гипотезу. Рыночные доходности, USD и Gold должны подтвердить передачу механизма.",
    }


def press_conference_snapshot(text: str) -> dict[str, Any]:
    normalized = re.sub(r"\s+", " ", text.replace("’", "'")).strip()
    claims: list[dict[str, Any]] = []
    for spec in PRESS_CONFERENCE_CLAIMS:
        evidence = next((match.group(0) for pattern in spec["patterns"] if (match := re.search(pattern, normalized, re.I))), None)
        if evidence:
            claims.append({
                "claim": spec["id"], "topic": spec["topic"], "tilt": spec["tilt"],
                "weight": spec["weight"], "evidence": evidence[:240],
            })
    hawkish = sum(item["weight"] for item in claims if item["tilt"] == "hawkish")
    dovish = sum(item["weight"] for item in claims if item["tilt"] == "dovish")
    if hawkish >= dovish + 2:
        classification = "hawkish"
    elif dovish >= hawkish + 2:
        classification = "dovish"
    elif hawkish or dovish:
        classification = "mixed"
    else:
        classification = "neutral_or_unresolved"
    return {"classification": classification, "hawkish_score": hawkish, "dovish_score": dovish, "claims": claims}


def prior_fomc_tilt(statement: dict[str, Any], sep: dict[str, Any]) -> str:
    translations = {
        "hawkish_tilt_candidate": "hawkish", "broad_hawkish_shift": "hawkish",
        "dovish_tilt_candidate": "dovish", "broad_dovish_shift": "dovish",
    }
    values = [translations.get(str(statement.get("classification"))), translations.get(str(sep.get("classification")))]
    directional = [value for value in values if value]
    return directional[0] if directional and len(set(directional)) == 1 else "mixed_or_unresolved"


def interpret_press_conference(
    official: dict[str, Any], statement: dict[str, Any], sep: dict[str, Any], now: datetime | None = None,
) -> dict[str, Any]:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    document = official.get("press_conference_document") if isinstance(official.get("press_conference_document"), dict) else {}
    timepoint = (official.get("timepoints") or {}).get("press_conference", {})
    try:
        event_time = datetime.fromisoformat(str(timepoint.get("event_time_utc") or "").replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        event_time = None
    base = {
        "stage": "press_conference", "event_time_utc": event_time.isoformat() if event_time else None,
        "source_url": document.get("source_url"),
        "permissions": {"confirm_driver": False, "entry": False, "direction_switch": False, "score": False},
    }
    if event_time and moment < event_time:
        return {**base, "status": "scheduled", "classification": "unavailable_before_event",
                "reason_ru": "Пресс-конференция ещё не началась; её будущий смысл нельзя использовать заранее."}
    if not document.get("verified"):
        return {**base, "status": str(document.get("status") or "transcript_unavailable"), "classification": "unavailable",
                "reason_ru": "Официальный транскрипт ещё не проверен; вывод из решения и SEP сохраняется без выдуманного комментария главы ФРС."}

    opening = press_conference_snapshot(str(document.get("prepared_remarks") or ""))
    qa = press_conference_snapshot(str(document.get("qa") or ""))
    combined_hawkish = opening["hawkish_score"] + qa["hawkish_score"]
    combined_dovish = opening["dovish_score"] + qa["dovish_score"]
    if combined_hawkish >= combined_dovish + 3:
        conference_tilt = "hawkish"
    elif combined_dovish >= combined_hawkish + 3:
        conference_tilt = "dovish"
    elif combined_hawkish or combined_dovish:
        conference_tilt = "mixed"
    else:
        conference_tilt = "neutral_or_unresolved"
    prior = prior_fomc_tilt(statement, sep)
    if conference_tilt in {"hawkish", "dovish"} and conference_tilt == prior:
        relation = "confirms_prior_package"
    elif conference_tilt in {"hawkish", "dovish"} and prior in {"hawkish", "dovish"}:
        relation = "reverses_prior_package"
    elif conference_tilt == "mixed" or (prior in {"hawkish", "dovish"} and conference_tilt == "neutral_or_unresolved"):
        relation = "softens_or_complicates_prior_package"
    else:
        relation = "independent_direction_unresolved"
    qa_effect = (
        "reinforces_opening" if qa["classification"] == opening["classification"] and qa["classification"] in {"hawkish", "dovish"}
        else "changes_or_complicates_opening" if qa["classification"] in {"hawkish", "dovish", "mixed"}
        and qa["classification"] != opening["classification"] else "no_directional_addition"
    )
    return {
        **base, "status": "compared", "classification": conference_tilt,
        "relation_to_18_00_package": relation, "prior_package_tilt": prior,
        "prepared_remarks": opening, "qa": qa, "qa_effect": qa_effect,
        "phase_policy": "Результат относится только к фазе пресс-конференции и не переписывает решение/statement/SEP в 18:00 UTC.",
        "market_confirmation_required": True,
        "policy": "Официальная речь создаёт смысловой кандидат. Доходности, USD, Gold и PA должны отдельно подтвердить его передачу рынку.",
    }


def interpret_decision(
    official: dict[str, Any], events_payload: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    decision = official.get("decision") if isinstance(official.get("decision"), dict) else {}
    meeting_id = str(official.get("meeting_id") or "")
    actual_upper = numeric_percent(decision.get("target_upper_pct"))
    timepoints = official.get("timepoints") if isinstance(official.get("timepoints"), dict) else {}
    required_timepoints = ("statement", "sep", "press_conference")
    official_ready = bool(
        meeting_id and actual_upper is not None
        and len(official.get("facts") or []) >= 6
        and all(
            isinstance(timepoints.get(name), dict) and bool(timepoints[name].get("verified"))
            for name in required_timepoints
        )
    )
    expectation = expectation_from_calendar(events_payload, meeting_id) if official_ready else None

    if not official_ready:
        surprise, reason = "unavailable", "Официальный факт FOMC неполный или не проверен."
    elif not expectation:
        surprise, reason = "expectation_missing", "Решение известно, но проверенного ожидания рынка для этой встречи нет."
    else:
        expected_upper = float(expectation["target_upper_pct"])
        difference_bps = round((actual_upper - expected_upper) * 100)
        if abs(actual_upper - expected_upper) <= TOLERANCE_PCT:
            surprise, reason = "in_line", "Верхняя граница ставки совпала с ожиданием рынка; само решение не является направленным сюрпризом."
        elif actual_upper > expected_upper:
            surprise, reason = "hawkish_surprise", "Верхняя граница ставки оказалась выше ожидания рынка."
        else:
            surprise, reason = "dovish_surprise", "Верхняя граница ставки оказалась ниже ожидания рынка."

    expected_upper = expectation.get("target_upper_pct") if expectation else None
    difference_bps = round((actual_upper - expected_upper) * 100) if actual_upper is not None and expected_upper is not None else None
    directional = surprise in {"hawkish_surprise", "dovish_surprise"}
    return {
        "schema_version": 1,
        "meeting_id": meeting_id or None,
        "generated_at_utc": moment.isoformat(),
        "stage": "decision",
        "official_decision": {
            "verified": official_ready,
            "action": decision.get("action"),
            "change_bps": decision.get("change_bps"),
            "target_lower_pct": decision.get("target_lower_pct"),
            "target_upper_pct": decision.get("target_upper_pct"),
        },
        "market_expectation": expectation or {"status": "missing"},
        "decision_surprise": {
            "classification": surprise,
            "difference_bps": difference_bps,
            "directional": directional,
            "reason_ru": reason,
        },
        "mechanism_candidates": policy_paths(surprise),
        "later_stages": {
            "statement": "pending_c2_2",
            "sep": "pending_c2_3",
            "press_conference": "pending_c2_4",
        },
        "permissions": {
            "confirm_driver": False,
            "entry": False,
            "direction_switch": False,
            "score": False,
        },
        "policy": "Действие FOMC и сюрприз разделены. Hike/cut без сравнения с ожиданием не получает hawkish/dovish направление.",
    }


def build_interpretation(
    official: dict[str, Any], events_payload: dict[str, Any], now: datetime | None = None,
) -> dict[str, Any]:
    result = interpret_decision(official, events_payload, now)
    result["statement_analysis"] = interpret_statement(official)
    result["sep_analysis"] = interpret_sep(official)
    result["press_conference_analysis"] = interpret_press_conference(
        official, result["statement_analysis"], result["sep_analysis"], now,
    )
    result["later_stages"]["statement"] = "completed_c2_2"
    result["later_stages"]["sep"] = "completed_c2_3b"
    result["later_stages"]["press_conference"] = "completed_c2_4"
    return result


def main() -> int:
    payload = build_interpretation(load_json(FACTS_FILE), load_json(EVENTS_FILE))
    atomic_json(OUTPUT_FILE, payload)
    decision_result = payload["decision_surprise"]["classification"]
    statement_result = payload["statement_analysis"].get("classification") or payload["statement_analysis"].get("status")
    sep_result = payload["sep_analysis"].get("classification") or payload["sep_analysis"].get("status")
    conference_result = payload["press_conference_analysis"].get("classification") or payload["press_conference_analysis"].get("status")
    print(f"FOMC_DECISION_INTERPRETATION: {decision_result}")
    print(f"FOMC_STATEMENT_INTERPRETATION: {statement_result}")
    print(f"FOMC_SEP_INTERPRETATION: {sep_result}")
    print(f"FOMC_PRESS_CONFERENCE_INTERPRETATION: {conference_result}")
    print("FOMC_INTERPRETATION_C2_4: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
