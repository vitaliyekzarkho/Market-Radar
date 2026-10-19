from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATA_DIR = Path("data")
RATES_FILE = DATA_DIR / "macro" / "rates_context.json"
INPUT_FILE = DATA_DIR / "macro" / "intermarket_inputs.json"
OIL_FILE = DATA_DIR / "macro" / "oil_context.json"
HEADLINES_FILE = DATA_DIR / "news" / "market_headlines.json"
ORDER_FLOW_FILE = DATA_DIR / "order_flow" / "GOLD_pro.json"
OUTPUT_FILE = DATA_DIR / "macro" / "intermarket_context.json"


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def build_context(rates_payload: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    """Build auditable intermarket hypotheses without claiming false causality."""
    rates = rates_payload.get("context") if isinstance(rates_payload.get("context"), dict) else {}
    gold_reaction = str(inputs.get("gold_reaction") or "unknown").lower()
    drivers: list[dict[str, Any]] = []
    rates_direction = str(rates.get("direction") or "unknown").lower()
    if rates.get("available") and rates_direction in {"rising", "falling"}:
        gold_effect = "bearish" if rates_direction == "rising" else "bullish"
        aligned = gold_reaction == gold_effect
        drivers.append({
            "driver_id": f"rates:{rates_direction}",
            "channel": "real_yield_opportunity_cost",
            "title": f"US yields {rates_direction}",
            "effect_xauusd": gold_effect,
            "causal_status": "correlated" if aligned else "inferred",
            "mechanism": (
                "Higher yields increase the opportunity cost of holding non-yielding gold."
                if rates_direction == "rising"
                else "Lower yields reduce the opportunity cost of holding non-yielding gold."
            ),
            "evidence": rates.get("observations") or {},
            "source_type": "market_data",
            "verified": False,
        })

    oil = inputs.get("oil") if isinstance(inputs.get("oil"), dict) else {}
    headlines = inputs.get("headlines") if isinstance(inputs.get("headlines"), list) else []
    deescalation = next((
        item for item in headlines if isinstance(item, dict)
        and str(item.get("theme") or "").lower() in {"geopolitical_deescalation", "iran_deescalation"}
        and str(item.get("source_url") or "").startswith(("https://", "http://"))
    ), None)
    oil_direction = str(oil.get("direction") or "unknown").lower()
    if deescalation and oil_direction == "falling":
        drivers.append({
            "driver_id": f"geo-oil:{deescalation.get('published_at_utc') or 'current'}",
            "channel": "geopolitical_risk_and_capital_rotation",
            "title": str(deescalation.get("title") or "Geopolitical de-escalation with falling oil"),
            "effect_xauusd": "bullish" if gold_reaction == "bullish" else "neutral",
            "causal_status": "correlated" if gold_reaction == "bullish" else "inferred",
            "mechanism": "De-escalation pressures oil; rotation into rebalanced gold is a hypothesis requiring price confirmation.",
            "evidence": {"oil": oil, "headline": deescalation},
            "source_type": "verified_headline_plus_market_data",
            "verified": False,
            "source_url": deescalation.get("source_url"),
        })

    effects = [item["effect_xauusd"] for item in drivers if item.get("causal_status") == "correlated"]
    net_effect = "bullish" if effects.count("bullish") > effects.count("bearish") else "bearish" if effects.count("bearish") > effects.count("bullish") else "mixed"
    return {
        "available": bool(drivers),
        "drivers": drivers,
        "net_effect_xauusd": net_effect,
        "gold_reaction": gold_reaction,
        "causality_policy": "verified fact != verified cause; inferred and correlated drivers require PA confirmation",
        "scoring_applied": False,
    }


def main() -> int:
    inputs = load_json(INPUT_FILE)
    oil_payload = load_json(OIL_FILE)
    headline_payload = load_json(HEADLINES_FILE)
    if isinstance(oil_payload.get("context"), dict):
        inputs["oil"] = oil_payload["context"]
    if isinstance(headline_payload.get("headlines"), list):
        inputs["headlines"] = headline_payload["headlines"]
    order_flow = load_json(ORDER_FLOW_FILE)
    frames = order_flow.get("timeframes") if isinstance(order_flow.get("timeframes"), dict) else {}
    h1 = frames.get("H1") if isinstance(frames.get("H1"), dict) else {}
    if str(h1.get("delivery_direction") or "") in {"bullish", "bearish"}:
        inputs["gold_reaction"] = h1["delivery_direction"]
    context = build_context(load_json(RATES_FILE), inputs)
    payload = {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "context": context}
    atomic_write_json(OUTPUT_FILE, payload)
    print(f"INTERMARKET_CONTEXT: drivers={len(context['drivers'])} | net={context['net_effect_xauusd']} | score_impact=0")
    print("INTERMARKET_CONTEXT_ENGINE: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
