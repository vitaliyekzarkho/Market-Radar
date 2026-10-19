from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATA = Path("data")
SCENARIO_FILE = DATA / "scenario_states" / "GOLD_pro.json"
STATE_FILE = DATA / "states" / "GOLD_pro.json"
RANGE_FILE = DATA / "ranges" / "GOLD_pro.json"
DXY_FILE = DATA / "market_context" / "DXY.json"


def load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def objects(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("objects")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def tf(item: dict[str, Any]) -> str:
    value = str(item.get("timeframe") or item.get("tf") or "").upper()
    if value:
        return value
    return str(item.get("name") or "").upper().split()[0]


def opposite(direction: str) -> str:
    return "bullish" if direction == "bearish" else "bearish"


def zone_candidates(
    state_objects: list[dict[str, Any]], price: float, counter_direction: str,
) -> list[dict[str, Any]]:
    # A bullish pullback seeks resistance above price; a bearish pullback seeks
    # support below price.  Only structural H1/H4 objects are eligible.
    wanted_role = "resistance" if counter_direction == "bullish" else "support"
    output: list[dict[str, Any]] = []
    for item in state_objects:
        if item.get("kind") not in {"fvg", "order_block", "volume_imbalance", "gap"}:
            continue
        timeframe = tf(item)
        if timeframe not in {"H1", "H4"} or item.get("directional_role") != wanted_role:
            continue
        low, high = finite(item.get("low")), finite(item.get("high"))
        if low is None or high is None:
            continue
        low, high = sorted((low, high))
        ahead = low > price if counter_direction == "bullish" else high < price
        if not ahead:
            continue
        state = str(((item.get("observation_h1") or {}).get("state")) or item.get("state") or "unknown")
        if state in {"accepted_above", "accepted_below", "invalidated", "failed"}:
            continue
        output.append({
            "name": item.get("name"), "kind": item.get("kind"), "timeframe": timeframe,
            "low": low, "high": high, "price": low if counter_direction == "bullish" else high,
            "state": state, "distance_percent": round(min(abs(low / price - 1), abs(high / price - 1)) * 100, 3),
            "source": "price_action",
        })
    output.sort(key=lambda item: (item["distance_percent"], 0 if item["timeframe"] == "H1" else 1))
    return output


def h1_half_candidates(range_data: dict[str, Any], price: float, counter_direction: str) -> list[dict[str, Any]]:
    frames = range_data.get("timeframes") if isinstance(range_data.get("timeframes"), dict) else {}
    h1 = frames.get("H1") if isinstance(frames.get("H1"), dict) else {}
    ranges = h1.get("selected_ranges") if isinstance(h1.get("selected_ranges"), list) else []
    output: list[dict[str, Any]] = []
    for item in ranges:
        if not isinstance(item, dict):
            continue
        fib = item.get("fibonacci") if isinstance(item.get("fibonacci"), dict) else {}
        level = finite(fib.get("0.5"))
        if level is None:
            continue
        ahead = level > price if counter_direction == "bullish" else level < price
        if not ahead:
            continue
        end = item.get("end") if isinstance(item.get("end"), dict) else {}
        output.append({
            "name": "0.5 H1 dealing range", "kind": "dealing_range_half", "timeframe": "H1",
            "price": level, "low": level, "high": level,
            "state": str(item.get("status") or "candidate"),
            "distance_percent": round(abs(level / price - 1) * 100, 3),
            "source": "dealing_range", "range_end_time": end.get("time"),
        })
    output.sort(key=lambda item: (item["distance_percent"], str(item.get("range_end_time") or "")), reverse=False)
    return output


def merge_targets(items: list[dict[str, Any]], price: float, limit: int = 3) -> list[dict[str, Any]]:
    items = sorted(items, key=lambda item: item.get("distance_percent", 999.0))
    selected: list[dict[str, Any]] = []
    for item in items:
        level = finite(item.get("price"))
        if level is None:
            continue
        if any(abs(level - float(old["price"])) <= price * 0.0012 for old in selected):
            continue
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def usd_relation(usd: dict[str, Any], gold_direction: str, dxy: dict[str, Any] | None = None) -> dict[str, Any]:
    dxy = dxy or {}
    if dxy.get("available") and isinstance(dxy.get("current_zone"), dict):
        zone = dxy["current_zone"]
        dxy_direction = str(zone.get("direction") or "mixed")
        implied_gold = opposite(dxy_direction) if dxy_direction in {"bullish", "bearish"} else "unknown"
        role = "supports" if implied_gold == gold_direction else "conflicts"
        return {
            "role": role, "usd_direction": dxy_direction, "source_type": dxy.get("source_type"),
            "direct_dxy_zone_read": True, "zone": zone,
            "explanation": f"DXY is inside {zone.get('name')} {zone.get('low')}-{zone.get('high')}; its {dxy_direction} reaction hypothesis {role} Gold {gold_direction}.",
        }
    if not usd.get("available"):
        return {"role": "unavailable", "explanation": "USD breadth unavailable; no cross-asset inference."}
    direction = str(usd.get("direction") or "mixed")
    if direction == "mixed":
        role = "neutral"
    else:
        implied_gold = opposite(direction)
        role = "supports" if implied_gold == gold_direction else "conflicts"
    return {
        "role": role, "usd_direction": direction, "strength": usd.get("strength"),
        "agreement": usd.get("agreement"),
        "source_type": "FX breadth proxy",
        "direct_dxy_zone_read": False,
        "explanation": f"USD {direction} {role} Gold {gold_direction}; this is breadth, not a direct DXY zone read.",
    }


def evidence_quality(reasons: list[str], targets: list[dict[str, Any]]) -> str:
    count = len([item for item in reasons if item]) + (1 if targets else 0)
    return "strong" if count >= 4 else "moderate" if count >= 2 else "weak"


def build_ideas(scenario: dict[str, Any], state_data: dict[str, Any], range_data: dict[str, Any], dxy_data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    market = scenario.get("market_state") if isinstance(scenario.get("market_state"), dict) else {}
    primary = market.get("primary") if isinstance(market.get("primary"), dict) else {}
    alternative = market.get("alternative") if isinstance(market.get("alternative"), dict) else {}
    opportunity = market.get("opportunity") if isinstance(market.get("opportunity"), dict) else {}
    structural_leg = market.get("primary_structural_leg") if isinstance(market.get("primary_structural_leg"), dict) else {}
    counter_leg = market.get("active_counter_leg") if isinstance(market.get("active_counter_leg"), dict) else {}
    counter_candidate = market.get("counter_candidate") if isinstance(market.get("counter_candidate"), dict) else {}
    continuation_setup = market.get("continuation_setup") if isinstance(market.get("continuation_setup"), dict) else {}
    volume = market.get("volume") if isinstance(market.get("volume"), dict) else {}
    news = market.get("news") if isinstance(market.get("news"), dict) else {}
    usd = scenario.get("usd_context") if isinstance(scenario.get("usd_context"), dict) else {}
    price = finite(scenario.get("current_price")) or 0.0
    direction = str(primary.get("direction") or "unknown")
    counter = opposite(direction) if direction in {"bullish", "bearish"} else "unknown"
    point_b = primary.get("point_b") if isinstance(primary.get("point_b"), dict) else {}
    dxy = (dxy_data or {}).get("context") if isinstance((dxy_data or {}).get("context"), dict) else {}
    primary_usd = usd_relation(usd, direction, dxy)
    counter_usd = usd_relation(usd, counter, dxy)

    continuation_reasons = [
        f"Primary stateful branch remains {direction}",
        str(structural_leg.get("confirmation") or primary.get("confirmation") or ""),
        primary_usd["explanation"],
        str(volume.get("explanation") or ""),
    ]
    ideas: list[dict[str, Any]] = [{
        "idea_id": f"primary_{direction}_continuation",
        "source_leg_id": continuation_setup.get("lifecycle_id") or structural_leg.get("lifecycle_id"),
        "type": "primary_continuation", "direction": direction,
        "state": str(opportunity.get("state") or "observe"),
        "thesis": f"Continue {direction} delivery toward {point_b.get('name', 'Point B')} while closed H1 structure holds.",
        "path": primary.get("route") or [], "objectives": [point_b] if point_b else [],
        "confirmation": primary.get("confirmation"), "invalidation": primary.get("invalidation"),
        "because": continuation_reasons, "cross_asset": primary_usd,
        "volume_role": volume.get("relation_to_primary", "unconfirmed"),
        "news_role": news.get("role", "none"),
        "quality": evidence_quality(continuation_reasons, [point_b] if point_b else []),
        "execution": "manual",
    }]

    targets = merge_targets(
        zone_candidates(objects(state_data), price, counter)
        + h1_half_candidates(range_data, price, counter), price,
    )
    location = market.get("current_location") if isinstance(market.get("current_location"), dict) else {}
    reasons = [
        f"Primary opportunity is {opportunity.get('state', 'unknown')}",
        f"Price is {location.get('interaction', 'near')} {location.get('name', 'current HTF area')}",
        counter_usd["explanation"],
    ]
    plausible = (
        opportunity.get("state") in {"late", "missed"}
        or counter_usd.get("role") == "supports"
        or bool(location)
    )
    ideas.append({
        "idea_id": f"nested_{counter}_pullback",
        "source_leg_id": counter_leg.get("lifecycle_id") or counter_candidate.get("lifecycle_id"),
        "type": "nested_counter_move", "direction": counter,
        "state": "forming" if plausible and targets else "watch",
        "thesis": f"A {counter} pullback may rebalance the current {direction} delivery without changing the primary branch.",
        "objectives": (counter_leg.get("route") or counter_candidate.get("route") or targets),
        "path": (counter_leg.get("route") or counter_candidate.get("route") or targets),
        "confirmation": counter_leg.get("confirmation") or counter_candidate.get("confirmation") or f"Require closed H1 {counter} reaction/displacement from the current decision area.",
        "invalidation": counter_leg.get("invalidation") or counter_candidate.get("invalidation") or f"Immediate {direction} acceptance through local support/resistance cancels the pullback idea.",
        "because": reasons, "cross_asset": counter_usd,
        "volume_role": "context_only", "news_role": news.get("role", "none"),
        "quality": evidence_quality(reasons, targets), "execution": "manual",
    })

    ideas.append({
        "idea_id": f"conditional_{counter}_reversal",
        "type": "conditional_reversal", "direction": counter,
        "state": "conditional",
        "thesis": f"Promote {counter} only after the primary invalidates and the opposite H1 branch confirms.",
        "objectives": [alternative.get("point_b")] if alternative.get("point_b") else [],
        "path": [], "confirmation": alternative.get("trigger"),
        "invalidation": f"No reversal while the {direction} primary remains structurally valid.",
        "because": ["Counter-move is not the same as structural reversal"],
        "cross_asset": counter_usd, "volume_role": "confirmation_required",
        "news_role": news.get("role", "none"), "quality": "conditional", "execution": "manual",
    })
    return ideas


def main() -> int:
    scenario = load(SCENARIO_FILE)
    scenario["trade_ideas"] = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": "algorithmic scenarios, not forecasts",
        "ideas": build_ideas(scenario, load(STATE_FILE), load(RANGE_FILE), load(DXY_FILE)),
    }
    write(SCENARIO_FILE, scenario)
    print("TRADE_IDEA_ENGINE: PASSED | ideas=" + str(len(scenario["trade_ideas"]["ideas"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
