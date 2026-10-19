from __future__ import annotations

from datetime import timedelta
from typing import Any

from reaction_window_engine import parse_time


CLUSTER_TOLERANCE = timedelta(minutes=2)

INFLATION_TERMS = ("cpi", "ppi", "pce", "inflation", "consumer price", "producer price")
INVERSE_LABOR_TERMS = ("unemployment rate", "unemployment claims", "jobless claims", "continuing claims")
LABOR_GROWTH_TERMS = (
    "payroll", "employment change", "average hourly earnings", "gdp", "retail sales",
    "pmi", "consumer confidence", "consumer sentiment", "industrial production",
)


def number(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "").replace("%", "")
    if not text:
        return None
    multiplier = 1.0
    if text[-1:].lower() == "k":
        multiplier, text = 1_000.0, text[:-1]
    elif text[-1:].lower() == "m":
        multiplier, text = 1_000_000.0, text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def component_surprise(title: str, actual: Any, forecast: Any) -> int | None:
    actual_number, forecast_number = number(actual), number(forecast)
    if actual_number is None or forecast_number is None:
        return None
    raw = (actual_number > forecast_number) - (actual_number < forecast_number)
    lowered = title.lower()
    return -raw if any(term in lowered for term in INVERSE_LABOR_TERMS) else raw


def release_group(title: str) -> str | None:
    lowered = title.lower()
    if any(term in lowered for term in INFLATION_TERMS):
        return "inflation"
    if any(term in lowered for term in INVERSE_LABOR_TERMS + LABOR_GROWTH_TERMS):
        return "growth_labor"
    return None


def released(event: dict[str, Any]) -> bool:
    return event.get("actual") not in (None, "") or str(event.get("status") or "").lower() in {"released", "actual"}


def cluster_release(selected: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine simultaneous macro components without inventing a direction.

    Only components from the same macro group and a two-minute publication
    window are combined. The result remains a reported calendar fact; source
    verification is handled by a separate provenance gate.
    """
    result = dict(selected)
    selected_time = parse_time(selected.get("event_time_utc") or selected.get("date"))
    group = release_group(str(selected.get("title") or ""))
    if not selected_time or not group:
        return result

    components: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict) or not released(event):
            continue
        currencies = event.get("currencies") if isinstance(event.get("currencies"), list) else [event.get("currencies")]
        event_time = parse_time(event.get("event_time_utc") or event.get("date"))
        title = str(event.get("title") or "")
        if "USD" not in {str(item).upper() for item in currencies}:
            continue
        if not event_time or abs(event_time - selected_time) > CLUSTER_TOLERANCE or release_group(title) != group:
            continue
        surprise = component_surprise(title, event.get("actual"), event.get("forecast"))
        components.append({
            "name": title, "actual": event.get("actual"), "forecast": event.get("forecast"),
            "previous": event.get("previous"), "directional_surprise": surprise,
        })

    if not components:
        return result
    directional = [item["directional_surprise"] for item in components if item["directional_surprise"] is not None]
    if directional and all(value == 0 for value in directional):
        suffix = "in_line"
    elif directional and all(value >= 0 for value in directional) and any(value > 0 for value in directional):
        suffix = "upside_surprise"
    elif directional and all(value <= 0 for value in directional) and any(value < 0 for value in directional):
        suffix = "downside_surprise"
    else:
        suffix = "mixed_release"

    result["release_components"] = components
    result["release_group"] = group
    result["release_status"] = suffix
    result["event_family"] = f"{group}_{suffix}" if suffix != "in_line" else f"{group}_in_line"
    result["title"] = " + ".join(item["name"] for item in components)
    return result
