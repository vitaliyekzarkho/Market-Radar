from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any


FAMILY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("geopolitical_deescalation", (
        "ceasefire", "peace talks", "peace deal", "end conflict", "conflict will end",
        "de-escalation", "deescalation", "suspend enrichment", "suspends enrichment",
        "sanctions relief", "hormuz agreement", "agrees to talks",
    )),
    ("geopolitical_escalation", (
        "airstrike", "missile strike", "retaliation", "attack on", "declares war",
        "blockade", "closes hormuz", "close hormuz", "sanctions escalation",
        "military escalation",
    )),
    ("yield_shock_up", ("yields spike", "yield spike", "yields surge", "real yields rise", "bond selloff")),
    ("yield_shock_down", ("yields plunge", "yield plunge", "yields fall", "real yields fall", "bond rally")),
    ("monetary_hawkish", ("hawkish", "higher for longer", "rate hike", "tightening")),
    ("monetary_dovish", ("dovish", "rate cut", "easing", "lower rates")),
    ("liquidity_stress", ("margin calls", "forced liquidation", "liquidity stress", "dash for cash", "deleveraging")),
    ("oil_supply_tightening", ("oil supply disruption", "production cut", "export halt", "tanker attack")),
    ("oil_supply_relief", (
        "oil supply restored", "production increase", "supply relief", "shipping resumes",
        "supply diesel", "diesel supply", "deliver diesel", "release diesel",
        "allow the supply", "supply into global market",
    )),
)


MECHANISM_LIBRARY: dict[str, list[dict[str, Any]]] = {
    "yield_shock_up": [{
        "mechanism_id": "real_yield_opportunity_cost_up",
        "name_ru": "Рост реальной доходности повышает стоимость удержания Gold",
        "horizon": "intraday_to_multisession",
        "primary": True,
        "expected": {"real_yields": "rising", "nominal_yields": "rising", "usd": "bullish_or_mixed", "gold": "bearish"},
        "required_evidence": ["fresh_rate_move", "gold_bearish_reaction", "timing_alignment"],
        "failure_conditions": ["real_yields_not_rising", "gold_accepts_higher", "reaction_precedes_rate_move"],
    }],
    "yield_shock_down": [{
        "mechanism_id": "real_yield_opportunity_cost_down",
        "name_ru": "Падение реальной доходности снижает стоимость удержания Gold",
        "horizon": "intraday_to_multisession",
        "primary": True,
        "expected": {"real_yields": "falling", "nominal_yields": "falling", "usd": "bearish_or_mixed", "gold": "bullish"},
        "required_evidence": ["fresh_rate_move", "gold_bullish_reaction", "timing_alignment"],
        "failure_conditions": ["real_yields_not_falling", "gold_accepts_lower", "reaction_precedes_rate_move"],
    }],
    "monetary_hawkish": [{
        "mechanism_id": "hawkish_policy_transmission",
        "name_ru": "Ужесточение денежной политики передаётся через доходности и USD",
        "horizon": "intraday_to_multisession",
        "primary": True,
        "expected": {"yields": "rising", "usd": "bullish", "gold": "bearish", "equities": "bearish_or_mixed"},
        "required_evidence": ["verified_policy_fact", "fresh_rate_or_usd_move", "gold_bearish_reaction"],
        "failure_conditions": ["no_policy_surprise", "rates_and_usd_do_not_confirm", "gold_accepts_higher"],
    }],
    "monetary_dovish": [{
        "mechanism_id": "dovish_policy_transmission",
        "name_ru": "Смягчение денежной политики передаётся через снижение доходностей и USD",
        "horizon": "intraday_to_multisession",
        "primary": True,
        "expected": {"yields": "falling", "usd": "bearish", "gold": "bullish", "equities": "bullish_or_mixed"},
        "required_evidence": ["verified_policy_fact", "fresh_rate_or_usd_move", "gold_bullish_reaction"],
        "failure_conditions": ["no_policy_surprise", "rates_and_usd_do_not_confirm", "gold_accepts_lower"],
    }],
    "geopolitical_escalation": [
        {
            "mechanism_id": "safe_haven_and_oil_risk_premium",
            "name_ru": "Эскалация повышает защитный спрос и премию риска в нефти",
            "horizon": "headline_to_multisession",
            "primary": True,
            "expected": {"oil": "bullish", "gold": "bullish", "usd": "bullish_or_mixed", "equities": "bearish"},
            "required_evidence": ["verified_escalation_fact", "oil_or_safe_haven_reaction", "gold_bullish_reaction"],
            "failure_conditions": ["headline_unverified", "oil_and_gold_do_not_react", "risk_reaction_fades"],
        },
        {
            "mechanism_id": "liquidity_sale_during_panic",
            "name_ru": "На острой фазе паники Gold продают ради долларовой ликвидности",
            "horizon": "immediate_headline",
            "primary": False,
            "expected": {"usd": "bullish", "gold": "bearish", "equities": "bearish", "oil": "mixed"},
            "required_evidence": ["broad_liquidation", "usd_bullish_reaction", "gold_bearish_reaction"],
            "failure_conditions": ["gold_holds_safe_haven_bid", "usd_not_bid"],
        },
    ],
    "geopolitical_deescalation": [
        {
            "mechanism_id": "safe_haven_premium_unwind",
            "name_ru": "Деэскалация снижает премию риска в нефти и защитный спрос",
            "horizon": "headline_to_multisession",
            "primary": True,
            "expected": {"oil": "bearish", "gold": "bearish_or_neutral", "usd": "bearish_or_mixed", "equities": "bullish"},
            "required_evidence": ["verified_deescalation_fact", "oil_bearish_reaction", "risk_on_confirmation"],
            "failure_conditions": ["headline_unverified", "oil_does_not_fall", "risk_on_reaction_fades"],
        },
        {
            "mechanism_id": "conditional_oil_to_gold_rotation",
            "name_ru": "После падения нефти капитал может перейти в перебалансированный Gold",
            "horizon": "intraday_to_multisession",
            "primary": False,
            "conditional": True,
            "expected": {"oil": "bearish", "gold": "bullish", "real_yields": "not_rising", "usd": "not_strongly_bullish"},
            "required_evidence": ["oil_bearish_reaction", "gold_at_htf_value", "gold_bullish_pa", "rates_and_usd_not_conflicting"],
            "failure_conditions": ["gold_no_bullish_acceptance", "real_yields_rising", "usd_strongly_bullish"],
        },
    ],
    "liquidity_stress": [{
        "mechanism_id": "forced_liquidity_deleveraging",
        "name_ru": "Дефицит долларовой ликвидности вызывает совместную распродажу рисковых активов и Gold",
        "horizon": "immediate_to_intraday",
        "primary": True,
        "expected": {"usd": "bullish", "gold": "bearish", "equities": "bearish", "volatility": "rising"},
        "required_evidence": ["broad_cross_asset_selloff", "usd_bullish_reaction", "gold_bearish_reaction"],
        "failure_conditions": ["gold_bullish_safe_haven_divergence", "usd_not_bid"],
    }],
    "oil_supply_tightening": [{
        "mechanism_id": "oil_inflation_risk_up",
        "name_ru": "Сокращение предложения нефти повышает нефтяную и инфляционную премию",
        "horizon": "intraday_to_multisession",
        "primary": True,
        "expected": {"oil": "bullish", "inflation_expectations": "rising", "gold": "mixed", "yields": "mixed_or_rising"},
        "required_evidence": ["verified_supply_fact", "oil_bullish_reaction", "gold_channel_confirmation"],
        "failure_conditions": ["oil_does_not_rise", "supply_fact_reversed"],
    }],
    "oil_supply_relief": [
        {
            "mechanism_id": "oil_inflation_risk_down",
            "name_ru": "Рост предложения снижает нефтяную и инфляционную премию",
            "horizon": "intraday_to_multisession",
            "primary": True,
            "expected": {"oil": "bearish", "inflation_expectations": "falling", "gold": "conditional", "yields": "mixed_or_falling"},
            "required_evidence": ["verified_supply_fact", "oil_bearish_reaction", "rates_and_gold_channel_confirmation"],
            "failure_conditions": ["oil_does_not_fall", "supply_fact_reversed", "yields_rise_despite_oil_drop"],
        },
        {
            "mechanism_id": "conditional_disinflation_support_for_gold",
            "name_ru": "Падение нефтяной инфляции может поддержать Gold через снижение доходностей",
            "horizon": "intraday_to_multisession",
            "primary": False,
            "conditional": True,
            "expected": {"oil": "bearish", "inflation_expectations": "falling", "yields": "falling", "usd": "bearish_or_mixed", "gold": "bullish"},
            "required_evidence": ["oil_bearish_reaction", "yields_falling", "usd_not_strongly_bullish", "gold_bullish_pa"],
            "failure_conditions": ["yields_rising", "usd_strongly_bullish", "gold_no_bullish_acceptance"],
        },
    ],
    "inflation_upside_surprise": [
        {
            "mechanism_id": "inflation_hawkish_repricing",
            "name_ru": "Высокая инфляция повышает доходности и поддерживает USD, создавая давление на Gold",
            "horizon": "immediate_to_intraday",
            "primary": True,
            "expected": {"yields": "rising", "real_yields": "rising_or_mixed", "usd": "bullish", "gold": "bearish"},
            "required_evidence": ["verified_upside_surprise", "rates_or_usd_confirmation", "gold_bearish_reaction"],
            "failure_conditions": ["real_yields_do_not_rise", "usd_rejects_higher", "gold_accepts_higher"],
        },
        {
            "mechanism_id": "conditional_inflation_hedge_bid",
            "name_ru": "На более длинном горизонте инфляционный риск может поддержать Gold",
            "horizon": "multisession",
            "primary": False,
            "conditional": True,
            "expected": {"inflation_expectations": "rising", "real_yields": "not_rising", "gold": "bullish"},
            "required_evidence": ["breakevens_rise_faster_than_nominal_yields", "real_yields_not_rising", "gold_bullish_pa"],
            "failure_conditions": ["real_yields_rising", "usd_strongly_bullish", "gold_no_bullish_acceptance"],
        },
    ],
    "inflation_downside_surprise": [{
        "mechanism_id": "disinflation_dovish_repricing",
        "name_ru": "Слабая инфляция снижает ожидания ставок и может поддержать Gold",
        "horizon": "immediate_to_intraday",
        "primary": True,
        "expected": {"yields": "falling", "usd": "bearish", "gold": "bullish"},
        "required_evidence": ["verified_downside_surprise", "rates_or_usd_confirmation", "gold_bullish_reaction"],
        "failure_conditions": ["rates_do_not_fall", "usd_accepts_higher", "gold_accepts_lower"],
    }],
    "inflation_mixed_release": [
        {
            "mechanism_id": "mixed_inflation_hawkish_readthrough",
            "name_ru": "Рынок трактует смешанный инфляционный релиз как ястребиный",
            "horizon": "immediate_to_intraday",
            "primary": True,
            "expected": {"yields": "rising_or_mixed", "usd": "bullish", "gold": "bearish"},
            "required_evidence": ["verified_mixed_release", "rates_or_usd_confirmation", "gold_bearish_reaction"],
            "failure_conditions": ["usd_rejects_higher", "gold_accepts_higher"],
        },
        {
            "mechanism_id": "mixed_inflation_dovish_readthrough",
            "name_ru": "Рынок трактует мягкий core-компонент как голубиный",
            "horizon": "immediate_to_intraday",
            "primary": True,
            "expected": {"yields": "falling_or_mixed", "usd": "bearish", "gold": "bullish"},
            "required_evidence": ["verified_mixed_release", "rates_or_usd_confirmation", "gold_bullish_reaction"],
            "failure_conditions": ["usd_accepts_higher", "gold_accepts_lower"],
        },
    ],
    "growth_labor_upside_surprise": [{
        "mechanism_id": "strong_growth_hawkish_repricing",
        "name_ru": "Сильные данные роста или труда поддерживают USD и ожидания более высоких ставок",
        "horizon": "immediate_to_intraday",
        "primary": True,
        "expected": {"yields": "rising_or_mixed", "usd": "bullish", "gold": "bearish_or_mixed"},
        "required_evidence": ["verified_upside_surprise", "usd_or_rates_confirmation", "gold_reaction"],
        "failure_conditions": ["usd_and_rates_do_not_confirm", "gold_safe_haven_bid"],
    }],
    "growth_labor_downside_surprise": [
        {
            "mechanism_id": "weak_growth_dovish_repricing",
            "name_ru": "Слабые данные снижают ожидания ставок и могут поддержать Gold",
            "horizon": "immediate_to_intraday",
            "primary": True,
            "expected": {"yields": "falling", "usd": "bearish_or_mixed", "gold": "bullish"},
            "required_evidence": ["verified_downside_surprise", "rates_or_usd_confirmation", "gold_bullish_reaction"],
            "failure_conditions": ["rates_do_not_fall", "usd_liquidity_bid", "gold_accepts_lower"],
        },
        {
            "mechanism_id": "recession_liquidity_stress",
            "name_ru": "Очень слабые данные могут вызвать ликвидацию и спрос на доллар",
            "horizon": "immediate_headline",
            "primary": False,
            "conditional": True,
            "expected": {"usd": "bullish", "equities": "bearish", "gold": "bearish_or_mixed"},
            "required_evidence": ["broad_liquidation", "usd_bullish_reaction", "risk_assets_bearish"],
            "failure_conditions": ["orderly_dovish_repricing", "usd_not_bid"],
        },
    ],
}


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def numeric_value(value: Any) -> float | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(match.group()) if match else None


def macro_release_status(event: dict[str, Any]) -> str:
    """Classify the release surprise without assigning a market direction.

    Composite releases (for example headline and core CPI) are in-line only
    when every supplied component matches its forecast. Mixed components must
    stay unresolved instead of being collapsed into a bullish/bearish label.
    """
    components = event.get("release_components")
    if not isinstance(components, list) or not components:
        components = [{"actual": event.get("actual"), "forecast": event.get("forecast")}]
    comparisons: list[int] = []
    for component in components:
        if not isinstance(component, dict):
            continue
        actual = numeric_value(component.get("actual"))
        forecast = numeric_value(component.get("forecast"))
        if actual is None or forecast is None:
            continue
        tolerance = max(1e-9, abs(forecast) * 1e-9)
        comparisons.append(0 if abs(actual - forecast) <= tolerance else (1 if actual > forecast else -1))
    if not comparisons:
        return "unavailable"
    if all(value == 0 for value in comparisons):
        return "in_line"
    if all(value >= 0 for value in comparisons) and any(value > 0 for value in comparisons):
        return "above_forecast"
    if all(value <= 0 for value in comparisons) and any(value < 0 for value in comparisons):
        return "below_forecast"
    return "mixed"


def classify_event_family(event: dict[str, Any]) -> str:
    explicit = normalize_text(event.get("event_family") or event.get("theme"))
    if explicit in MECHANISM_LIBRARY:
        return explicit
    text = " ".join((normalize_text(event.get("title")), normalize_text(event.get("fact_statement")), explicit))
    for family, phrases in FAMILY_RULES:
        if any(phrase in text for phrase in phrases):
            return family
    return "unknown"


def mechanism_candidate_id(event_id: str, mechanism_id: str) -> str:
    value = f"{event_id}|{mechanism_id}"
    return "mech_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def propose_mechanisms(event: dict[str, Any]) -> dict[str, Any]:
    family = classify_event_family(event)
    event_id = str(event.get("event_id") or event.get("driver_id") or "event_unknown")
    release_status = macro_release_status(event) if event.get("release_components") else "unavailable"
    if release_status == "in_line":
        return {
            "schema_version": 1,
            "event_id": event_id,
            "event_family": family,
            "recognized": True,
            "directional_surprise": False,
            "release_status": "in_line",
            "candidates": [],
            "policy": "Факт совпал с прогнозом: релиз является контекстом, а не направленным драйвером.",
            "confirmation_allowed": False,
            "scoring_applied": False,
        }
    lifecycle = normalize_text(event.get("lifecycle_state") or event.get("status") or "reported_fact")
    scheduled = lifecycle in {"scheduled", "pending", "scheduled_risk"}
    candidates: list[dict[str, Any]] = []
    for template in MECHANISM_LIBRARY.get(family, []):
        candidate = deepcopy(template)
        candidate.update({
            "candidate_id": mechanism_candidate_id(event_id, str(template["mechanism_id"])),
            "event_id": event_id,
            "event_family": family,
            "state": "risk_template" if scheduled else "mechanism_candidate",
            "confirmation_allowed": False,
            "fact_verified": False,
            "permissions": {
                "entry": False,
                "direction_switch": False,
                "score": False,
                "technical_invalidation_override": False,
            },
        })
        candidates.append(candidate)
    return {
        "schema_version": 1,
        "event_id": event_id,
        "event_family": family,
        "recognized": bool(candidates),
        "candidates": candidates,
        "policy": "A mechanism is a testable transmission hypothesis, not a verified cause.",
        "confirmation_allowed": False,
        "scoring_applied": False,
    }


def macro_surprise_templates(event: dict[str, Any]) -> dict[str, Any]:
    """Return symmetric paths before a scheduled macro actual is known."""
    event_id = str(event.get("event_id") or "macro_unknown")
    base = {
        "event_id": event_id,
        "event_family": "macro_surprise_pending",
        "state": "risk_template",
        "confirmation_allowed": False,
        "permissions": {"entry": False, "direction_switch": False, "score": False, "technical_invalidation_override": False},
    }
    upside = {
        **base,
        "candidate_id": mechanism_candidate_id(event_id, "usd_positive_surprise"),
        "mechanism_id": "usd_positive_surprise",
        "name_ru": "Сильный факт может поддержать USD и доходности и давить на Gold",
        "expected": {"usd": "bullish", "yields": "rising_or_mixed", "gold": "bearish"},
    }
    downside = {
        **base,
        "candidate_id": mechanism_candidate_id(event_id, "usd_negative_surprise"),
        "mechanism_id": "usd_negative_surprise",
        "name_ru": "Слабый факт может ослабить USD и доходности и поддержать Gold",
        "expected": {"usd": "bearish", "yields": "falling_or_mixed", "gold": "bullish"},
    }
    return {
        "schema_version": 1,
        "event_id": event_id,
        "event_family": "macro_surprise_pending",
        "recognized": True,
        "candidates": [upside, downside],
        "policy": "Scheduled macro creates symmetric risk templates until actual, forecast and reaction are known.",
        "confirmation_allowed": False,
        "scoring_applied": False,
    }
