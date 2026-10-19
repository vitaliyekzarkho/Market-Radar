from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SYMBOL = "GOLD.pro"
SYMBOL_FILE = SYMBOL.replace(".", "_")
DATA_DIR = Path("data")

SCENARIO_FILE = DATA_DIR / "scenario_states" / f"{SYMBOL_FILE}.json"
EVENT_FILE = DATA_DIR / "events" / f"{SYMBOL_FILE}.json"
POLICY_FILE = DATA_DIR / "alert_policy" / f"{SYMBOL_FILE}.json"
OUTPUT_FILE = DATA_DIR / "notifications" / f"{SYMBOL_FILE}.json"
PREVIEW_FILE = DATA_DIR / "notifications" / f"{SYMBOL_FILE}_preview.txt"


def configure_console_utf8() -> None:
    """Windows PowerShell may expose cp1251 even when the file is UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def load_json(path: Path, required: bool = True) -> dict[str, Any]:
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"Missing required file: {path}")
        return {}
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    temporary.replace(path)


def format_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{number:.2f}"


def format_zone(zone: Any) -> str:
    if not isinstance(zone, dict) or not zone:
        return "none"
    name = str(zone.get("name") or "zone")
    low = format_number(zone.get("low"))
    high = format_number(zone.get("high"))
    return f"{name} {low}-{high}" if low and high else name


def format_target(target: Any) -> str:
    if not isinstance(target, dict) or not target:
        return "none"
    return f"{target.get('name', 'target')} {format_number(target.get('price'))}".strip()


def timeframe_name(item: dict[str, Any]) -> str:
    tf = str(item.get("timeframe") or "").strip()
    name = str(item.get("name") or "zone").strip()
    return name if not tf or name.upper().startswith(tf.upper() + " ") else f"{tf} {name}"


RU_TERMS = {
    "unknown": "неизвестно", "none": "нет", "observe": "наблюдение",
    "building": "формируется", "confirmed": "подтверждено", "active": "активно",
    "forming": "формируется", "conditional": "условно", "watch": "наблюдение",
    "late": "уже поздно", "missed": "упущено", "setup_forming": "сетап формируется",
    "early_attention": "раннее внимание", "dormant": "ожидает",
    "testing": "тестируется", "inside": "внутри", "approaching": "приближается",
    "pending": "ожидается", "scheduled": "запланировано", "unconfirmed": "не подтверждено",
    "supports": "поддерживает", "conflicts": "противоречит", "neutral": "нейтрально",
    "supports_candidate": "поддерживает наблюдение", "warning": "предупреждение",
    "weak": "слабое подтверждение", "not_applicable": "не применяется",
    "context_only": "только контекст", "supports_execution": "поддерживает исполнение",
    "primary_structural_leg": "основное структурное движение",
    "active_counter_leg": "активная вложенная реакция",
    "higher_timeframe_context": "контекст старшего ТФ",
    "timing_context": "контекст тайминга", "source_timeframe": "исходный ТФ",
    "execution_confirmation": "подтверждение исполнения", "timing_only": "только тайминг",
    "near_edge": "у ближней границы", "before_ce": "до 0.5 зоны",
    "beyond_ce": "за 0.5 зоны", "near_far_edge": "у дальней границы",
    "outside": "вне зоны", "passed": "пройдено", "ahead": "впереди",
    "protected_structural_bias": "защищённая структура",
    "active_delivery": "активная delivery",
    "delivery_broken": "delivery сломана",
    "pullback_or_transition": "pullback/переход",
    "continuation": "продолжение",
    "reaction_building": "реакция формируется", "reaction_testing": "реакция тестируется",
    "reclaimed_above": "возврат и закрепление выше", "reclaimed_below": "возврат и закрепление ниже",
    "accepted_above": "закрепление выше", "accepted_below": "закрепление ниже",
    "untouched": "не достигнуто", "swept": "ликвидность снята",
    "session_liquidity": "сессионная ликвидность",
    "balanced": "сбалансированно", "low_volume_pullback": "pullback на низком объёме",
}


def ru_term(value: Any) -> str:
    raw = str(value or "unknown")
    return RU_TERMS.get(raw.lower(), raw.replace("_", " "))


def ru_opportunity_reason(value: Any) -> str:
    text = str(value or "")
    translations = {
        "A material part of the route to the minimum objective has already been delivered.": "Значительная часть движения до минимальной цели уже пройдена.",
        "The minimum reaction target has already been delivered.": "Минимальная цель реакции уже достигнута.",
        "The minimum reaction target is reached or nearly reached.": "Минимальная цель реакции достигнута или почти достигнута.",
        "Price is approaching the decision cluster.": "Цена приближается к зоне решения.",
        "Reaction is developing; structural confirmation is pending.": "Реакция формируется; структурное подтверждение ещё ожидается.",
        "The leg is confirmed and meaningful space remains to FTA.": "Движение подтверждено, до FTA остаётся достаточное пространство.",
        "The route exists, but timing quality is not established.": "Маршрут существует, но качество момента входа не подтверждено.",
    }
    return translations.get(text, text or "Наблюдение; исполнение остаётся ручным.")


def ru_condition(value: Any) -> str:
    text = clean_condition(value)
    text = re.sub(r"^IF H1 accepts above ", "ЕСЛИ H1 закрепится выше ", text, flags=re.I)
    text = re.sub(r"^IF H1 accepts below ", "ЕСЛИ H1 закрепится ниже ", text, flags=re.I)
    text = text.replace(", THEN downgrade the bearish branch", ", ТО bearish-гипотеза ослабевает")
    text = text.replace(", THEN downgrade the bullish branch", ", ТО bullish-гипотеза ослабевает")
    text = text.replace(", THEN continuation toward ", ", ТО сохраняется движение к ")
    text = text.replace(" remains active", "")
    match = re.match(r"A confirmed H1 transition against (bullish|bearish) downgrades this branch", text, re.I)
    if match:
        return f"Подтверждённый переход H1 против {match.group(1)} направления ослабляет эту ветку"
    return text


def causality_summary_ru(causality: dict[str, Any]) -> str:
    source = causality.get("source") if isinstance(causality.get("source"), dict) else {}
    evidence = source.get("evidence") if isinstance(source.get("evidence"), dict) else {}
    reaction = causality.get("reaction") if isinstance(causality.get("reaction"), dict) else {}
    lower = causality.get("lower_tf_confirmation") if isinstance(causality.get("lower_tf_confirmation"), dict) else {}
    path = causality.get("path") if isinstance(causality.get("path"), dict) else {}
    objective = path.get("first_objective") if isinstance(path.get("first_objective"), dict) else {}
    if evidence:
        source_text = f"{timeframe_name(evidence)} @ {format_number(evidence.get('price'))}"
    else:
        source_text = "источник не подтверждён"
    reaction_status = {
        "observed": "реакция замечена", "pending": "реакция ожидается",
        "confirmed": "реакция подтверждена",
    }.get(str(reaction.get("status")), ru_term(reaction.get("status")))
    lower_status = {
        "pending": "подтверждение ожидается", "observed": "наблюдается",
        "confirmed": "подтверждено",
    }.get(str(lower.get("status")), ru_term(lower.get("status")))
    objective_text = format_target(objective) if objective else "цель не определена"
    return f"источник {source_text} → {reaction_status} → H1 {lower_status} → ближайшая цель {objective_text}"


def conflict_text(items: Any) -> str:
    if not isinstance(items, list) or not items:
        return "нет"
    translations = {
        "volume=unconfirmed": "объём не подтверждает",
        "news=scheduled_risk": "есть запланированный новостной риск",
        "news=none": "новостного подтверждения нет",
    }
    return ", ".join(translations.get(str(item), str(item).replace("_", " ")) for item in items)


def ru_explanation(value: Any) -> str:
    text = str(value or "")
    exact = {
        "Closed volume is present but does not yet confirm either hypothesis.": "Закрытые объёмы доступны, но пока не подтверждают ни одну из гипотез.",
        "No closed H1/H4 volume observation is available.": "Наблюдения по закрытым объёмам H1/H4 отсутствуют.",
    }
    if text in exact:
        return exact[text]
    match = re.match(r"H1 (bullish|bearish) pullback has low participation; (bullish|bearish) hypothesis retains control\.", text)
    if match:
        return f"H1 {match.group(1)} pullback проходит на низком объёме; {match.group(2)} гипотеза сохраняет контроль."
    text = text.replace("effort and result align with the", "усилие и результат согласуются с")
    text = text.replace("hypothesis", "гипотезой")
    text = text.replace("supports execution", "поддерживает исполнение")
    return text


def ru_thesis(value: Any) -> str:
    text = str(value or "")
    match = re.match(r"Continue (bullish|bearish) delivery toward (.+) while closed H1 structure holds\.", text)
    if match:
        return f"Продолжение {match.group(1)} delivery к {match.group(2)}, пока закрытая структура H1 сохраняется."
    match = re.match(r"A (bullish|bearish) pullback may rebalance the current (bullish|bearish) delivery without changing the primary branch\.", text)
    if match:
        return f"{match.group(1).capitalize()} pullback может ребалансировать текущую {match.group(2)} delivery без смены основной ветки."
    match = re.match(r"Promote (bullish|bearish) only after the primary invalidates and the opposite H1 branch confirms\.", text)
    if match:
        return f"Повышать {match.group(1)} гипотезу только после инвалидации primary и подтверждения противоположной ветки на H1."
    return text


def clean_condition(value: Any) -> str:
    text = str(value or "none")
    return re.sub(
        r"(?<![A-Za-z])(-?\d+\.\d{3,})(?![A-Za-z])",
        lambda match: f"{float(match.group(1)):.2f}",
        text,
    )


def structure_context_lines(scenario: dict[str, Any]) -> list[str]:
    market = scenario.get("market_state") if isinstance(scenario.get("market_state"), dict) else {}
    hierarchy = market.get("timeframe_hierarchy") if isinstance(market.get("timeframe_hierarchy"), dict) else {}
    frames = hierarchy.get("frames") if isinstance(hierarchy.get("frames"), dict) else {}
    if frames:
        lines = [
            f"Внешний W1-нарратив: {hierarchy.get('external_narrative', 'unknown')} | ведущая delivery: {hierarchy.get('dominant_delivery', 'unknown')}",
        ]
        for tf in ("W1", "D1", "H4", "H1"):
            frame = frames.get(tf) if isinstance(frames.get(tf), dict) else {}
            lines.append(
            f"{tf}: контроль {frame.get('control_direction', frame.get('structural_narrative', 'unknown'))} "
                f"({ru_term(frame.get('control_source', 'legacy'))}) | "
                f"delivery {frame.get('delivery_direction', 'unknown')} [{ru_term(frame.get('delivery_state'))}] | "
                f"фаза {ru_term(frame.get('phase'))}"
            )
        return lines
    context = scenario.get("order_flow_context")
    if not isinstance(context, dict) or not context.get("available"):
        return []
    phases = context.get("phases") if isinstance(context.get("phases"), dict) else {}
    def readable(value: Any) -> str:
        return str(value or "unknown").replace("_or_", "/").replace("_", " ")

    phase_text = " | ".join(f"{tf} {readable(phases.get(tf, {}).get('phase'))}" for tf in ("D1", "H4", "H1"))
    return [
        f"W1 {str(context.get('weekly_narrative') or 'unknown')} narrative",
        f"Delivery {readable(context.get('delivery_status'))}",
        phase_text,
    ]


def driver_text(scenario: dict[str, Any]) -> str:
    driver = scenario.get("driver_context")
    if not isinstance(driver, dict):
        driver = scenario.get("news_context")
    if not isinstance(driver, dict) or not driver.get("available"):
        return "Подтверждённый драйвер пока не найден."
    title = str(driver.get("title") or driver.get("headline") or "Market driver").strip()
    effect = str(driver.get("effect") or driver.get("interpretation") or "").strip()
    confidence = str(driver.get("confidence") or "").strip()
    actual = driver.get("actual")
    forecast = driver.get("forecast")
    comparison = f"факт {actual} | прогноз {forecast}" if actual not in (None, "") else "запланировано"
    reaction = str(driver.get("reaction_status") or "").strip()
    details = " — ".join(part for part in (title, effect, comparison, reaction) if part)
    return f"{details} [{confidence}]" if confidence else details


def usd_context_text(scenario: dict[str, Any], gold_direction: str) -> str:
    context = scenario.get("usd_context")
    if not isinstance(context, dict) or not context.get("available"):
        return "USD breadth пока недоступен."
    usd_direction = str(context.get("direction") or "mixed").lower()
    strength = int(context.get("strength") or 0)
    agreement = context.get("agreement") if isinstance(context.get("agreement"), dict) else {}
    ratio = f"{agreement.get('agreeing', 0)}/{agreement.get('total', 0)}"
    if usd_direction == "mixed" or gold_direction not in {"bullish", "bearish"}:
        relation = "смешанный фон — подтверждения маршрута Gold нет"
    elif usd_direction != gold_direction:
        relation = f"подтверждает {gold_direction} маршрут Gold"
    else:
        relation = f"противоречит {gold_direction} маршруту Gold"
    result = f"USD {usd_direction} | сила {strength:+d} | согласованность {ratio} — {relation}."
    excluded = context.get("excluded_components") if isinstance(context.get("excluded_components"), list) else []
    if excluded:
        names = ", ".join(str(item.get("pair")) for item in excluded if item.get("pair"))
        if names:
            result += f" Исключены из-за локальных новостей: {names}."
    return result


def macro_context_text(scenario: dict[str, Any]) -> list[str]:
    context = scenario.get("macro_context")
    if not isinstance(context, dict):
        return ["Источники макро пока недоступны."]
    sources = context.get("official_sources_monitored") if isinstance(context.get("official_sources_monitored"), list) else []
    lines = [f"Официальные источники: {', '.join(str(item) for item in sources) if sources else 'нет активного источника'}"]
    rates = context.get("rates") if isinstance(context.get("rates"), dict) else {}
    lines.append(f"Доходности US: {str(rates.get('direction') or 'нет данных')}")
    positioning = context.get("positioning") if isinstance(context.get("positioning"), dict) else {}
    if positioning.get("available"):
        lines.append(f"COT Gold: {positioning.get('direction')} | чистая позиция managed money {positioning.get('managed_money_net')}")
    else:
        lines.append("COT Gold: нет данных")
    expectations = context.get("fed_expectations") if isinstance(context.get("fed_expectations"), dict) else {}
    if not expectations.get("available") and expectations.get("manual_reference_available"):
        lines.append("FedWatch: только ручная проверка")
    intermarket = context.get("intermarket") if isinstance(context.get("intermarket"), dict) else {}
    drivers = intermarket.get("drivers") if isinstance(intermarket.get("drivers"), list) else []
    if drivers:
        lines.append("Межрыночные драйверы:")
        for item in drivers[:3]:
            if isinstance(item, dict):
                lines.append(
                    f"- {item.get('title')} → Gold {item.get('effect_xauusd')} | "
                    f"{item.get('causal_status')} | {item.get('mechanism')}"
                )
    else:
        lines.append("Межрыночные драйверы: подтверждённых данных нет")
    return lines


def notification_type(policy_payload: dict[str, Any]) -> str:
    return str(policy_payload.get("notification_type") or "NONE").upper()


def visual_marker(kind: str, direction: str) -> str:
    if kind == "SCENARIO_PAUSED":
        return "⚪"
    if kind in {"DECISION_EVENT", "EARLY_ATTENTION", "SETUP_FORMING", "TARGET_CHANGED", "ROUTE_UPDATED", "NEWS_DRIVER", "NEWS_RISK"}:
        return "🟡"
    if kind == "SCENARIO_INVALIDATED":
        return "🔴"
    return "🔴" if direction == "bearish" else "🟢"


def event_title(kind: str, direction: str, status: str, reaction_direction: str | None = None) -> str:
    direction_word = direction.upper()
    reaction_word = str(reaction_direction or direction).upper()
    titles = {
        "NEW_SCENARIO": f"НОВЫЙ {direction_word} СЦЕНАРИЙ",
        "SCENARIO_RESUMED": f"{direction_word} СЦЕНАРИЙ ВОЗОБНОВЛЁН",
        "DIRECTION_CHANGED": f"{direction_word} ВЕТКА ПОДТВЕРЖДЕНА",
        "STATUS_PROMOTED": f"{direction_word} | {ru_term(status).upper()}",
        "DECISION_EVENT": "ТЕСТ ЗОНЫ РЕШЕНИЯ",
        "REACTION_EVENT": f"{reaction_word} РЕАКЦИЯ",
        "EARLY_ATTENTION": f"{reaction_word} ВОЗМОЖНОСТЬ ФОРМИРУЕТСЯ",
        "SETUP_FORMING": f"{direction_word} СЕТАП ФОРМИРУЕТСЯ",
        "TARGET_CHANGED": "ЦЕЛЬ ИЗМЕНИЛАСЬ",
        "ROUTE_UPDATED": "МАРШРУТ ОБНОВЛЁН",
        "SCENARIO_INVALIDATED": f"{direction_word} СЦЕНАРИЙ ОТМЕНЁН",
        "SCENARIO_PAUSED": "СЦЕНАРИЙ НА ПАУЗЕ",
        "NEWS_DRIVER": "ОБНОВЛЕНИЕ НОВОСТНОГО ДРАЙВЕРА",
        "NEWS_RISK": "ПРЕДСТОЯЩИЙ НОВОСТНОЙ РИСК",
    }
    return titles.get(kind, f"{direction_word} | {ru_term(status).upper()}")


def route_parts(primary: dict[str, Any]) -> list[str]:
    reaction = primary.get("current_reaction_zone")
    pullback = primary.get("pullback_zone")
    origin = reaction if isinstance(reaction, dict) and reaction else pullback
    parts: list[str] = []
    if isinstance(origin, dict) and origin:
        parts.append(format_zone(origin))
    checkpoints = primary.get("route_checkpoints")
    if isinstance(checkpoints, list):
        for checkpoint in checkpoints:
            if not isinstance(checkpoint, dict):
                continue
            name = str(checkpoint.get("name") or "checkpoint")
            low = checkpoint.get("low")
            high = checkpoint.get("high")
            price = (
                f"{format_number(low)}-{format_number(high)}"
                if low is not None and high is not None and float(low) != float(high)
                else format_number(checkpoint.get("price"))
            )
            state = str(checkpoint.get("state") or "unknown")
            role = str(checkpoint.get("route_role") or "TA")
            parts.append(f"{role}: {name} {price} | {state}")
    target = primary.get("point_b")
    if isinstance(target, dict) and target:
        target_text = format_target(target)
        if target_text not in parts:
            parts.append(target_text)
    return parts


def delivery_lines(scenario: dict[str, Any]) -> list[str]:
    market_state = scenario.get("market_state")
    market_state = market_state if isinstance(market_state, dict) else {}
    active_leg = market_state.get("active_leg")
    active_leg = active_leg if isinstance(active_leg, dict) else {}
    delivery = scenario.get("current_delivery")
    if not isinstance(delivery, dict):
        return []
    direction = str(delivery.get("direction") or "unknown")
    state = str(delivery.get("state") or "unknown")
    structure = str(delivery.get("structure") or "unknown").replace("_", " ").lower()
    leg_direction = str(active_leg.get("direction") or direction)
    phase = str(active_leg.get("phase") or "unknown")
    lines = [f"Active H1 leg: {leg_direction} | phase: {phase} | structure: {structure}"]
    pulse = delivery.get("pulse")
    if isinstance(pulse, dict) and pulse.get("available"):
        pulse_direction = str(pulse.get("direction") or "mixed")
        lines.append(f"LTF pulse: {pulse_direction} | {state}")
    checkpoints = delivery.get("route_checkpoints")
    if isinstance(checkpoints, list):
        for checkpoint in checkpoints[:6]:
            if not isinstance(checkpoint, dict):
                continue
            label = "FTA" if checkpoint.get("route_role") == "FTA" else "TA"
            lines.append(f"→ {label}: {checkpoint.get('name')} {format_number(checkpoint.get('price'))}")
    target = delivery.get("point_b")
    if isinstance(target, dict) and target:
        lines.append(f"→ Structural Point B: {format_target(target)}")
    return lines


def current_decision_text(scenario: dict[str, Any]) -> str:
    market_state = scenario.get("market_state")
    market_state = market_state if isinstance(market_state, dict) else {}
    location = market_state.get("current_location")
    if isinstance(location, dict) and location:
        low, high = location.get("low"), location.get("high")
        area = (
            f"{format_number(low)}-{format_number(high)}"
            if low is not None and high is not None and float(low) != float(high)
            else format_number(low)
        )
        zone_position = location.get("zone_position") if isinstance(location.get("zone_position"), dict) else {}
        depth = ""
        if zone_position:
            penetration = zone_position.get("penetration")
            penetration_text = f"{float(penetration) * 100:.0f}%" if penetration is not None else "неизвестно"
            depth = (
                f" | положение={ru_term(zone_position.get('depth_state'))} ({penetration_text})"
                f" | CE={format_number(zone_position.get('ce_0_5'))}"
                f" | дальняя граница={format_number(zone_position.get('far_edge'))}"
            )
        zone_stack = location.get("zone_stack") if isinstance(location.get("zone_stack"), list) else []
        nested = ""
        if len(zone_stack) > 1:
            nested = " | вложенные: " + "; ".join(
                f"{timeframe_name(item)} дальняя={format_number(item.get('far_edge'))}"
                for item in zone_stack[1:3] if isinstance(item, dict)
            )
        return (
            f"{location.get('name')} {area} | {ru_term(location.get('interaction', 'nearby'))} | "
            f"состояние={ru_term(location.get('state'))}{depth}{nested}"
        )
    delivery = scenario.get("current_delivery")
    if not isinstance(delivery, dict):
        return "none"
    checkpoints = delivery.get("route_checkpoints")
    checkpoints = checkpoints if isinstance(checkpoints, list) else []
    first = next((item for item in checkpoints if isinstance(item, dict) and item.get("route_role") == "FTA"), None)
    if not isinstance(first, dict):
        return "none"
    low, high = first.get("low"), first.get("high")
    area = (
        f"{format_number(low)}-{format_number(high)}"
        if low is not None and high is not None and float(low) != float(high)
        else format_number(first.get("price"))
    )
    return f"{first.get('name')} {area} | {first.get('state', 'unknown')}"


def decision_geometry_lines(market: dict[str, Any]) -> list[str]:
    location = market.get("current_location") if isinstance(market.get("current_location"), dict) else {}
    geometry = location.get("decision_geometry") if isinstance(location.get("decision_geometry"), dict) else {}
    if not geometry:
        return []
    execution = geometry.get("active_execution_zone") if isinstance(geometry.get("active_execution_zone"), dict) else {}
    next_boundary = geometry.get("next_boundary") if isinstance(geometry.get("next_boundary"), dict) else {}
    backstops = geometry.get("htf_backstops") if isinstance(geometry.get("htf_backstops"), list) else []
    lines = [
        f"Зона исполнения: {timeframe_name(execution)} | положение: {ru_term(execution.get('depth_state'))}",
    ]
    if next_boundary:
        lines.append(
            f"Следующая граница: {timeframe_name(next_boundary)} "
            f"{str(next_boundary.get('boundary_type')).replace('_', ' ')} {format_number(next_boundary.get('price'))}"
        )
    if backstops:
        lines.append("Опорные HTF-границы: " + " → ".join(
            f"{item.get('timeframe')} {str(item.get('boundary_type')).replace('_', ' ')} {format_number(item.get('price'))}"
            for item in backstops[:3] if isinstance(item, dict)
        ))
    lines.append("Само нахождение в зоне не подтверждает разворот; нужна закрытая реакция младшего ТФ.")
    return lines


def market_summary(scenario: dict[str, Any]) -> str:
    market_state = scenario.get("market_state")
    market_state = market_state if isinstance(market_state, dict) else {}
    active_leg = market_state.get("active_leg")
    active_leg = active_leg if isinstance(active_leg, dict) else {}
    location = market_state.get("current_location")
    location = location if isinstance(location, dict) else {}
    transition = market_state.get("hypothesis_transition")
    transition = transition if isinstance(transition, dict) else {}
    leg_direction = str(active_leg.get("direction") or "unknown")
    phase = str(active_leg.get("phase") or "unknown")
    location_name = str(location.get("name") or "nearest structural zone")
    interaction = str(location.get("interaction") or "nearby")
    transition_state = str(transition.get("state") or "active")
    if location:
        return (
            f"H1 active leg — {leg_direction}, phase — {phase}. Цена {interaction} {location_name}. "
            f"Hypothesis state — {transition_state}; смена primary разрешена только после closed H1 confirmation."
        )
    delivery = scenario.get("current_delivery")
    delivery = delivery if isinstance(delivery, dict) else {}
    direction = str(delivery.get("direction") or "unknown")
    pulse = delivery.get("pulse")
    pulse = pulse if isinstance(pulse, dict) else {}
    pulse_direction = str(pulse.get("direction") or "mixed")
    pulse_state = str(pulse.get("state") or "unknown")
    checkpoints = delivery.get("route_checkpoints")
    checkpoints = checkpoints if isinstance(checkpoints, list) else []
    first = next((item for item in checkpoints if isinstance(item, dict) and item.get("route_role") == "FTA"), {})
    location = str(first.get("name") or "ближайшей structural zone")
    if pulse_direction != direction and pulse_direction in {"bullish", "bearish"}:
        return (
            f"{direction.capitalize()} delivery подошло к {location}. На LTF появился "
            f"{pulse_direction} {pulse_state} pulse — это ранняя реакция, но H1 reversal ещё не подтверждён."
        )
    return f"{direction.capitalize()} delivery остаётся активным в направлении {location}; LTF pulse — {pulse_direction} {pulse_state}."


def decision_zone_text(primary: dict[str, Any]) -> str:
    zone = primary.get("next_decision_zone")
    if not isinstance(zone, dict) or not zone:
        return "none"
    state = str(primary.get("next_decision_state") or "unknown")
    return f"{format_zone(zone)} | {state}"


def volume_reasoning_text(scenario: dict[str, Any]) -> str:
    market_state = scenario.get("market_state")
    market_state = market_state if isinstance(market_state, dict) else {}
    volume = market_state.get("volume_context")
    volume = volume if isinstance(volume, dict) else {}
    if not volume.get("available"):
        stale = volume.get("stale_timeframes") if isinstance(volume.get("stale_timeframes"), list) else []
        if stale:
            return f"Объёмные данные просрочены ({', '.join(stale)}) и исключены из анализа."
        return "Данные закрытых объёмов W1/D1/H4/H1 недоступны."
    evaluations = volume.get("leg_evaluations") if isinstance(volume.get("leg_evaluations"), dict) else {}
    structural = evaluations.get("primary_structural_leg") if isinstance(evaluations.get("primary_structural_leg"), dict) else {}
    counter = evaluations.get("active_counter_leg") if isinstance(evaluations.get("active_counter_leg"), dict) else {}
    relation = str(structural.get("relation") or volume.get("relation_to_primary") or "unconfirmed")
    explanation = ru_explanation(structural.get("reason") or volume.get("explanation") or "Объём не дал подтверждения.")
    observations = volume.get("observations") if isinstance(volume.get("observations"), list) else []
    source_tf = str(structural.get("source_timeframe") or volume.get("source_timeframe") or "")
    relevant_observations = [
        item for item in observations
        if isinstance(item, dict) and item.get("timeframe") in {source_tf, "H1"}
    ] or observations
    attribution = "; ".join(
        f"{item.get('timeframe')} {ru_term(item.get('state'))} {item.get('direction')} "
        f"[{ru_term(item.get('leg_attribution'))}/{ru_term(item.get('confirmation_scope'))}]"
        for item in relevant_observations[:3] if isinstance(item, dict)
    )
    lines = [f"Структурное движение: {ru_term(relation)} — {explanation}"]
    if counter and counter.get("direction") in {"bullish", "bearish"}:
        lines.append(
            f"Вложенная реакция: {ru_term(counter.get('relation'))} — "
            f"{ru_explanation(counter.get('reason') or 'H1 объём не дал подтверждения реакции.')}"
        )
    if attribution:
        lines.append(f"Наблюдения: {attribution}.")
    stale = volume.get("stale_timeframes") if isinstance(volume.get("stale_timeframes"), list) else []
    if stale:
        lines.append(f"Качество данных: просроченные ТФ исключены — {', '.join(stale)}.")
    lines.append("Ограничение: тиковый объём не создаёт вход и не меняет направление самостоятельно.")
    return "\n".join(lines)


def trade_idea_lines(scenario: dict[str, Any]) -> list[str]:
    payload = scenario.get("trade_ideas") if isinstance(scenario.get("trade_ideas"), dict) else {}
    ideas = payload.get("ideas") if isinstance(payload.get("ideas"), list) else []
    lines: list[str] = []
    labels = {
        "primary_continuation": "A. Продолжение primary",
        "nested_counter_move": "B. Вложенный pullback",
        "conditional_reversal": "C. Условный разворот",
    }
    for idea in ideas[:3]:
        if not isinstance(idea, dict):
            continue
        objectives = idea.get("objectives") if isinstance(idea.get("objectives"), list) else []
        targets = ", ".join(
            f"{item.get('name', 'target')} {format_number(item.get('price'))}"
            for item in objectives[:3] if isinstance(item, dict)
        ) or "сбалансированная цель не найдена"
        cross = idea.get("cross_asset") if isinstance(idea.get("cross_asset"), dict) else {}
        lines.extend([
            f"{labels.get(str(idea.get('type')), 'Идея')} | {str(idea.get('direction') or 'unknown').upper()} | {ru_term(idea.get('state') or 'watch').upper()}",
            ru_thesis(idea.get("thesis")),
            f"Цели: {targets} | USD: {ru_term(cross.get('role'))} ({str(cross.get('source_type') or 'источник отсутствует').replace('_', ' ')})",
        ])
    return lines


def happened_text(kind: str, direction: str, status: str, decision_state: str, reaction_state: str) -> str:
    texts = {
        "NEW_SCENARIO": f"Новый {direction} сценарий перешёл в активное наблюдение.",
        "SCENARIO_RESUMED": f"{direction.capitalize()} сценарий снова активен с обновлённым маршрутом.",
        "DIRECTION_CHANGED": f"{direction.capitalize()} ветка подтверждена закрытым структурным переходом H1; предыдущая ветка отменена.",
        "STATUS_PROMOTED": f"Статус сценария повышен до {ru_term(status)}; набор подтверждений усилился.",
        "DECISION_EVENT": f"Цена тестирует зону решения; текущее состояние — {ru_term(decision_state)}.",
        "REACTION_EVENT": f"В ожидаемой зоне появилась реакция; состояние — {reaction_state}.",
        "EARLY_ATTENTION": f"Цена приближается к зоне, где может сформироваться {direction} реакция.",
        "SETUP_FORMING": f"Формируется новая возможность по направлению {direction}; подтверждение ещё не получено.",
        "TARGET_CHANGED": "Point B изменился, поэтому маршрут пересчитан.",
        "ROUTE_UPDATED": "Промежуточные PA-уровни изменились; маршрут до Point B пересчитан.",
        "SCENARIO_INVALIDATED": f"{direction.capitalize()} маршрут нарушен подтверждённым закреплением за границей инвалидации.",
        "SCENARIO_PAUSED": "Предыдущий маршрут временно не соответствует условиям публикации.",
        "NEWS_DRIVER": "Вышла макроновость; её отклонение от прогноза сопоставлено с реакцией USD.",
        "NEWS_RISK": "В ближайшие 24 часа ожидается значимая новость по USD.",
    }
    return texts.get(kind, "Контекст сценария существенно изменился.")


def action_text(kind: str, direction: str) -> str:
    actions = {
        "NEW_SCENARIO": "Observe route. Entry confirmation пока нет.",
        "SCENARIO_RESUMED": "Observe обновлённый route. Не использовать старые уровни вслепую.",
        "DIRECTION_CHANGED": f"Closed H1 confirmation получен; сопровождать подтверждённый {direction} route.",
        "STATUS_PROMOTED": "Повысить внимание, но дождаться подтверждения в decision zone.",
        "DECISION_EVENT": "Observe H1 acceptance или rejection. Entry confirmation пока нет.",
        "REACTION_EVENT": "Ждать H1 confirmation; сама реакция ещё не является entry trigger.",
        "EARLY_ATTENTION": "Подойти к графику и наблюдать тест зоны; вход до подтверждения не предлагать.",
        "SETUP_FORMING": "Подойти к графику и проверить формирование; вход только после заданного подтверждения.",
        "TARGET_CHANGED": "Обновить Point B и проверить препятствия на новом route.",
        "ROUTE_UPDATED": "Оценить acceptance/rejection на ближайшем checkpoint; Point B пока остаётся конечной целью.",
        "SCENARIO_INVALIDATED": "Прекратить сопровождение этой ветки. Opposite entry автоматически не подтверждён.",
        "SCENARIO_PAUSED": "No action. Ждать нового валидного scenario.",
        "NEWS_DRIVER": "Сверить actual, forecast и USD reaction. Новость сама по себе не является entry trigger.",
        "NEWS_RISK": "Не торговать forecast как факт; ждать actual и подтверждение market reaction.",
    }
    return actions.get(kind, "Observe. Entry confirmation пока нет.")


def build_message(scenario: dict[str, Any], policy_payload: dict[str, Any], preview: bool) -> str:
    primary = scenario.get("primary_scenario") or {}
    if not isinstance(primary, dict) or not primary:
        return "GOLD | ТИШИНА\nНет сценария, подходящего для публикации."

    direction = str(primary.get("direction") or "unknown").lower()
    status = str(primary.get("status") or "silent").lower()
    price = format_number(scenario.get("current_price"))
    conditions = primary.get("conditions") if isinstance(primary.get("conditions"), dict) else {}
    kind = notification_type(policy_payload)
    marker = visual_marker(kind, direction)
    title = event_title(kind, direction, status, policy_payload.get("reaction_direction"))
    market = scenario.get("market_state") if isinstance(scenario.get("market_state"), dict) else {}
    hypothesis = market.get("primary") if isinstance(market.get("primary"), dict) else {}
    alternative = market.get("alternative") if isinstance(market.get("alternative"), dict) else {}
    leg = market.get("active_leg") if isinstance(market.get("active_leg"), dict) else {}
    structural_leg = (
        market.get("primary_structural_leg")
        if isinstance(market.get("primary_structural_leg"), dict)
        else leg
    )
    counter_leg = market.get("active_counter_leg") if isinstance(market.get("active_counter_leg"), dict) else {}
    counter_candidate = market.get("counter_candidate") if isinstance(market.get("counter_candidate"), dict) else {}
    candidate_opportunity = market.get("counter_candidate_opportunity") if isinstance(market.get("counter_candidate_opportunity"), dict) else {}
    continuation_setup = market.get("continuation_setup") if isinstance(market.get("continuation_setup"), dict) else {}
    pa = market.get("pa_state") if isinstance(market.get("pa_state"), dict) else {}
    opportunity = market.get("opportunity") if isinstance(market.get("opportunity"), dict) else {}
    opportunity_leg = counter_leg or counter_candidate or continuation_setup or structural_leg
    opportunity_direction = str(opportunity_leg.get("direction") or direction).upper()
    point_a = structural_leg.get("point_a") if isinstance(structural_leg.get("point_a"), dict) else {}
    point_b = hypothesis.get("point_b") if isinstance(hypothesis.get("point_b"), dict) else {}
    minimum = hypothesis.get("minimum_target") if isinstance(hypothesis.get("minimum_target"), dict) else {}
    transition_band = hypothesis.get("transition_band") if isinstance(hypothesis.get("transition_band"), dict) else {}
    causality = hypothesis.get("causality") if isinstance(hypothesis.get("causality"), dict) else {}
    tf = market.get("timeframe_hierarchy") if isinstance(market.get("timeframe_hierarchy"), dict) else {}
    tf_frames = tf.get("frames") if isinstance(tf.get("frames"), dict) else {}
    route_items = hypothesis.get("route") if isinstance(hypothesis.get("route"), list) else []

    if kind in {"NEWS_RISK", "NEWS_DRIVER"}:
        # News is an independent event. Do not attach a stale technical
        # LATE/MISSED opportunity or replay the entire completed route.
        lines = [
            f"{marker} GOLD | {title}",
            f"Цена: {price}",
            "",
            "Драйверы:",
            driver_text(scenario),
            "",
            "Контекст USD:",
            usd_context_text(scenario, direction),
            "",
            "Макро/новости:",
            *macro_context_text(scenario),
            "",
            "Что делать:",
            action_text(kind, direction),
        ]
        return "\n".join(lines)

    def route_line(item: dict[str, Any]) -> str:
        level = item.get("price")
        if level is None:
            level = item.get("high") if direction == "bullish" else item.get("low")
        return (
            f"{item.get('route_role', 'TA')}: {item.get('name', item.get('kind', 'level'))} "
            f"{format_number(level)} | {ru_term(item.get('state', item.get('status', 'pending')))}"
        )

    route = "\n→ ".join(route_line(item) for item in route_items if isinstance(item, dict)) or "маршрут недоступен"

    lines = [
        f"{marker} GOLD | {title}",
        f"Цена: {price} | Возможность {opportunity_direction}: {ru_term(opportunity.get('state') or 'observe').upper()}",
        "",
        "Главная гипотеза:",
        f"{direction.upper()} A→B: {point_a.get('name', 'structural origin')} {format_number(point_a.get('price'))} → {point_b.get('name', 'target')} {format_number(point_b.get('price'))}",
        f"Причинная цепочка: {causality_summary_ru(causality)}",
        f"Статус гипотезы: {ru_term(causality.get('status', 'incomplete'))} | противоречия: {conflict_text(causality.get('conflicts'))}",
        "ТФ-контроль: " + " | ".join(
            f"{name} {str((tf_frames.get(name) or {}).get('control_direction') or 'unknown')}"
            for name in ("W1", "D1", "H4", "H1")
        ),
        f"MTF-лестница: {str(tf.get('interpretation', 'недоступна')).replace(' control', ' контроль').replace(' is counter to its parent', ' против родительского ТФ').replace(' is nested against the parent route', ' вложен против родительского маршрута')}",
        f"Внешний нарратив: W1 {tf.get('external_narrative', 'unknown')} [{ru_term(tf.get('external_narrative_source'))}] | основной маршрут: {tf.get('dominant_delivery', direction)}",
        "",
        "Где цена сейчас:",
        current_decision_text(scenario),
        f"Состояние PA: {ru_term(pa.get('stage'))} | Активное движение: {leg.get('direction', 'unknown')} {str(leg.get('kind', '')).replace('_', ' ')}",
    ]
    if kind == "EARLY_ATTENTION":
        lines.extend([
            "",
            "Почему сейчас:",
            happened_text(kind, direction, status, str(primary.get("next_decision_state") or "unknown"), str(pa.get("stage") or "unknown")),
            "",
            "Что делать:",
            action_text(kind, direction),
        ])
    geometry_lines = decision_geometry_lines(market)
    if geometry_lines:
        lines.extend(["", "Геометрия зоны решения:", *geometry_lines])
    if counter_leg:
        counter_a = counter_leg.get("point_a") if isinstance(counter_leg.get("point_a"), dict) else {}
        counter_target = counter_leg.get("minimum_target")
        lines.extend([
            "",
            "Активная вложенная реакция:",
            f"{str(counter_leg.get('direction') or 'unknown').upper()} A→FTA: "
            f"{counter_a.get('name', 'reaction origin')} {format_number(counter_a.get('price'))} → "
            f"{format_target(counter_target)}",
            f"Статус: {ru_term(counter_leg.get('state'))} | не меняет основную структурную гипотезу без отдельного подтверждения",
        ])
    elif counter_candidate:
        candidate_a = counter_candidate.get("point_a") if isinstance(counter_candidate.get("point_a"), dict) else {}
        candidate_state = str(candidate_opportunity.get("state") or "observe").lower()
        candidate_is_actionable = candidate_state not in {"late", "missed"}
        lines.extend([
            "",
            "Формируется вложенная реакция:" if candidate_is_actionable else "Вложенная реакция уже развилась:",
            f"{str(counter_candidate.get('direction') or 'unknown').upper()} кандидат A→FTA: "
            f"{candidate_a.get('name', 'reaction source')} {format_number(candidate_a.get('price'))} → "
            f"{format_target(counter_candidate.get('minimum_target'))}",
            (
                "Статус: предварительный — это приглашение наблюдать, а не подтверждённый вход и не смена основной гипотезы."
                if candidate_is_actionable
                else f"Статус: {ru_term(candidate_state).upper()} — новую точку входа по этой реакции не предлагать."
            ),
        ])
    elif continuation_setup:
        setup_a = continuation_setup.get("point_a") if isinstance(continuation_setup.get("point_a"), dict) else {}
        lines.extend([
            "",
            "Новая возможность продолжения после пулбэка:",
            f"{str(continuation_setup.get('direction') or 'unknown').upper()} A→FTA: "
            f"{setup_a.get('name', 'local continuation origin')} {format_number(setup_a.get('price'))} → "
            f"{format_target(continuation_setup.get('minimum_target'))}",
            "Структурный A→B сохранён; тайминг рассчитан заново от локального продолжения.",
        ])
    lines.extend([
        "",
        f"Минимальная реакция / FTA: {format_target(minimum)}",
        "",
        (
            "Отменённый маршрут:"
            if kind == "SCENARIO_INVALIDATED"
            else "Предыдущий маршрут:"
            if kind == "SCENARIO_PAUSED"
            else f"Полный {direction} маршрут:"
        ),
        route,
        "",
        "Альтернатива:",
        f"{str(alternative.get('direction') or 'none').upper()} — {ru_term(alternative.get('status', 'dormant'))}; условие: {ru_condition(alternative.get('trigger'))}",
        "",
        "Алгоритмические идеи:",
        *(trade_idea_lines(scenario) or ["Алгоритмические идеи недоступны"]),
        "",
        "Драйверы:",
        driver_text(scenario),
        "",
        "Контекст USD:",
        usd_context_text(scenario, direction),
        "",
        "Контекст объёмов:",
        volume_reasoning_text(scenario),
        "",
        "Макро/новости:",
        *macro_context_text(scenario),
    ])

    if kind in {"NEW_SCENARIO", "SCENARIO_RESUMED", "DIRECTION_CHANGED"}:
        context_lines = structure_context_lines(scenario)
        lines.extend(["", "Контекст:", *(context_lines or ["MTF-контекст недоступен"])])

    if kind not in {"SCENARIO_PAUSED", "SCENARIO_INVALIDATED"}:
        lines.extend(["", "Подтверждение:", ru_condition(hypothesis.get("confirmation") or conditions.get("confirmation"))])
    if kind == "SCENARIO_INVALIDATED":
        lines.extend(["", "Инвалидация:", "Подтверждена — цена закрепилась за границей сценария."])
    lines.extend([
        "", "Инвалидация:", ru_condition(hypothesis.get("invalidation") or conditions.get("invalidation")),
        "", "Тайминг:", ru_opportunity_reason(opportunity.get("reason")),
    ])
    if transition_band:
        lines.extend([
            "", "Переходная зона:",
            f"{format_number(transition_band.get('low'))}-{format_number(transition_band.get('high'))} — основная гипотеза ослаблена, противоположная ветка ещё не подтверждена.",
        ])
    if preview and kind == "NONE":
        lines.extend(["", "Предпросмотр: нового события для уведомления нет"])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a compact MarketRadar notification payload.")
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Build and print a message even when the event monitor says no notification is required.",
    )
    return parser.parse_args()


def main() -> None:
    configure_console_utf8()
    args = parse_args()
    scenario = load_json(SCENARIO_FILE)
    event_payload = load_json(EVENT_FILE, required=False)
    policy_payload = load_json(POLICY_FILE, required=False)
    should_notify = bool(policy_payload.get("should_notify"))
    build_required = should_notify or args.preview
    message = build_message(scenario, policy_payload, preview=args.preview) if build_required else ""

    payload = {
        "schema_version": 2,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "preview": bool(args.preview),
        "should_send": should_notify,
        "message_built": bool(message),
        "notification_type": policy_payload.get("notification_type", "NONE"),
        "priority": policy_payload.get("priority", "none"),
        "event_count": int(event_payload.get("event_count") or 0),
        "included_event_count": len(policy_payload.get("included_events") or []),
        "suppressed_event_count": len(policy_payload.get("suppressed_events") or []),
        "message": message,
    }
    driver = scenario.get("driver_context")
    if isinstance(driver, dict) and driver.get("available") and driver.get("source_url"):
        payload["links"] = {"driver_url": str(driver["source_url"])}
    atomic_write_json(OUTPUT_FILE, payload)

    if args.preview:
        PREVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
        PREVIEW_FILE.write_text(message + "\n", encoding="utf-8")

    if not build_required:
        print("NOTIFICATION_BUILDER: NO_ALERT — message not built")
    else:
        mode = "PREVIEW" if args.preview and not should_notify else "ALERT"
        print(f"NOTIFICATION_BUILDER: {mode}")
        print("\n" + message)
    print(f"NOTIFICATION_SAVED: {OUTPUT_FILE}")
    print("NOTIFICATION_BUILDER_ENGINE: PASSED")


if __name__ == "__main__":
    main()
