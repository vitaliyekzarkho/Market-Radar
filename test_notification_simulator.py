from __future__ import annotations

import copy
import unittest

from notification_builder import build_message
from notification_simulator import CASES, build_cases


def fixture() -> dict:
    return {
        "symbol": "GOLD.pro",
        "current_price": 4378.25,
        "usd_context": {
            "available": True,
            "direction": "bearish",
            "strength": -67,
            "agreement": {"agreeing": 4, "total": 5},
            "scoring_applied": False,
        },
        "order_flow_context": {
            "available": True,
            "weekly_narrative": "bullish",
            "delivery_status": "ALIGNED_BULLISH",
            "structure_status": "REVERSAL_BUILDING",
            "phases": {
                "D1": {"phase": "counter_move"},
                "H4": {"phase": "pullback_or_transition"},
                "H1": {"phase": "pullback_or_transition"},
            },
        },
        "primary_scenario": {
            "direction": "bullish",
            "status": "building",
            "score": 68,
            "point_b": {"name": "PWH", "price": 4442.98},
            "next_decision_zone": {
                "name": "H4 bearish FVG", "low": 4400.25, "high": 4405.73,
            },
            "next_decision_state": "approaching",
            "current_reaction_zone": {
                "name": "H1 bullish FVG", "low": 4358.10, "high": 4381.69,
                "h1_state": "swept",
            },
            "pullback_zone": {
                "name": "H1 bullish FVG", "low": 4325.06, "high": 4343.68,
            },
            "conditions": {
                "confirmation": "IF H1 accepts above 4405.73, THEN continuation remains active",
                "invalidation": "IF H1 accepts below 4325.06, THEN downgrade the bullish branch",
            },
        },
    }


class NotificationSimulatorTests(unittest.TestCase):
    def test_all_eight_cases_render(self) -> None:
        rendered = build_cases(fixture())
        self.assertEqual(len(rendered), 8)
        self.assertEqual(len(rendered), len(CASES))

    def test_every_case_starts_with_visual_marker(self) -> None:
        for _, message in build_cases(fixture()):
            self.assertIn(message[0], {"🟢", "🔴", "🟡", "⚪"})

    def test_every_case_stays_within_telegram_limit(self) -> None:
        for _, message in build_cases(fixture()):
            self.assertLessEqual(len(message), 4096)

    def test_render_does_not_mutate_base_scenario(self) -> None:
        base = fixture()
        original = copy.deepcopy(base)
        build_cases(base)
        self.assertEqual(base, original)

    def test_critical_cases_are_present(self) -> None:
        critical = {case.key for case in CASES if case.priority == "critical"}
        self.assertEqual(critical, {"direction", "invalidated"})

    def test_compact_event_omits_full_context(self) -> None:
        rendered = dict((case.key, message) for case, message in build_cases(fixture()))
        self.assertNotIn("\nКонтекст:\n", rendered["decision"])
        self.assertIn("\nКонтекст:\n", rendered["new"])

    def test_driver_slot_is_always_present(self) -> None:
        for _, message in build_cases(fixture()):
            self.assertIn("Драйверы:\n", message)

    def test_usd_context_is_always_present(self) -> None:
        for _, message in build_cases(fixture()):
            self.assertIn("Контекст USD:\n", message)

    def test_bearish_usd_confirms_bullish_gold_route(self) -> None:
        rendered = dict((case.key, message) for case, message in build_cases(fixture()))
        self.assertIn("подтверждает bullish маршрут Gold", rendered["new"])

    def test_decision_simulation_price_is_inside_zone(self) -> None:
        rendered = dict((case.key, message) for case, message in build_cases(fixture()))
        self.assertIn("Цена: 4402.99", rendered["decision"])

    def test_inactive_routes_are_labelled_as_historical(self) -> None:
        rendered = dict((case.key, message) for case, message in build_cases(fixture()))
        self.assertIn("Отменённый маршрут:", rendered["invalidated"])
        self.assertIn("Предыдущий маршрут:", rendered["paused"])

    def test_news_risk_does_not_publish_missed_technical_opportunity(self) -> None:
        scenario = fixture()
        scenario["market_state"] = {
            "opportunity": {
                "state": "missed",
                "reason": "The minimum reaction target has already been delivered.",
            },
        }
        scenario["driver_context"] = {
            "available": True,
            "title": "ISM Services PMI",
            "status": "scheduled",
            "importance": "high",
        }
        message = build_message(
            scenario,
            {"notification_type": "NEWS_RISK", "reaction_direction": None},
            preview=False,
        )
        self.assertIn("ПРЕДСТОЯЩИЙ НОВОСТНОЙ РИСК", message)
        self.assertIn("ISM Services PMI", message)
        self.assertNotIn("MISSED", message)
        self.assertNotIn("LATE", message)
        self.assertNotIn("Главная гипотеза:", message)

    def test_news_driver_does_not_publish_late_technical_opportunity(self) -> None:
        scenario = fixture()
        scenario["market_state"] = {"opportunity": {"state": "late"}}
        scenario["driver_context"] = {
            "available": True,
            "title": "Core PCE",
            "status": "released",
            "importance": "high",
            "actual": "0.3%",
        }
        message = build_message(
            scenario,
            {"notification_type": "NEWS_DRIVER", "reaction_direction": None},
            preview=False,
        )
        self.assertIn("Core PCE", message)
        self.assertNotIn("LATE", message)
        self.assertNotIn("MISSED", message)

    def test_early_attention_calls_user_to_chart_before_confirmation(self) -> None:
        scenario = fixture()
        scenario["market_state"] = {
            "counter_candidate": {
                "direction": "bullish",
                "point_a": {"name": "H1 provisional reaction anchor", "price": 4159.0},
                "minimum_target": {"name": "H1 bearish FVG", "price": 4181.55},
            },
            "counter_candidate_opportunity": {"state": "early_attention"},
            "opportunity": {"state": "early_attention"},
        }
        message = build_message(
            scenario,
            {"notification_type": "EARLY_ATTENTION", "reaction_direction": "bullish"},
            preview=False,
        )
        self.assertIn("BULLISH ВОЗМОЖНОСТЬ ФОРМИРУЕТСЯ", message)
        self.assertIn("Возможность BULLISH: РАННЕЕ ВНИМАНИЕ", message)
        self.assertIn("Цена приближается к зоне", message)
        self.assertIn("Подойти к графику и наблюдать тест зоны", message)
        self.assertNotIn("LATE", message)
        self.assertNotIn("MISSED", message)

    def test_volume_block_separates_structural_leg_from_counter_reaction(self) -> None:
        scenario = fixture()
        scenario["market_state"] = {
            "primary": {"direction": "bearish", "route": []},
            "primary_structural_leg": {"direction": "bearish"},
            "counter_candidate": {"direction": "bullish"},
            "opportunity": {"state": "early_attention"},
            "volume_context": {
                "available": True,
                "leg_evaluations": {
                    "primary_structural_leg": {
                        "direction": "bearish", "relation": "supports",
                        "reason": "H4: усилие и результат согласованы с bearish structural leg.",
                    },
                    "active_counter_leg": {
                        "direction": "bullish", "relation": "supports_candidate",
                        "reason": "H1: bearish усилие перестало давать результат; это поддерживает наблюдение bullish реакции, но не подтверждает вход.",
                    },
                },
                "observations": [],
            },
        }
        scenario["primary_scenario"]["direction"] = "bearish"
        message = build_message(
            scenario,
            {"notification_type": "EARLY_ATTENTION", "reaction_direction": "bullish"},
            preview=False,
        )
        self.assertIn("Структурное движение: поддерживает", message)
        self.assertIn("Вложенная реакция: поддерживает наблюдение", message)
        self.assertIn("тиковый объём не создаёт вход", message)

    def test_structural_and_counter_legs_render_with_their_own_point_a(self) -> None:
        scenario = fixture()
        scenario["market_state"] = {
            "primary": {
                "direction": "bearish",
                "point_b": {"name": "PDL", "price": 4110.91},
                "minimum_target": {"name": "PDL", "price": 4110.91},
                "route": [],
                "confirmation": "bearish structure holds",
                "invalidation": "above 4206.11",
            },
            "primary_structural_leg": {
                "lifecycle_id": "primary-1", "direction": "bearish",
                "point_a": {"name": "H4 bearish FVG", "price": 4300.42},
                "confirmation": "H4 rejection initiated bearish delivery",
            },
            "active_counter_leg": {
                "lifecycle_id": "counter-1", "direction": "bullish", "kind": "counter_move",
                "state": "reaction_building",
                "point_a": {"name": "H1 fractal low", "price": 4110.91},
                "minimum_target": {"name": "H1 bearish FVG", "price": 4181.55},
            },
            "active_leg": {"direction": "bullish", "kind": "counter_move"},
            "alternative": {}, "pa_state": {"stage": "reaction_building"},
            "opportunity": {"state": "setup_forming"},
        }
        scenario["primary_scenario"]["direction"] = "bearish"
        message = build_message(
            scenario,
            {"notification_type": "REACTION_EVENT", "reaction_direction": "bullish"},
            preview=False,
        )
        self.assertIn("BEARISH A→B: H4 bearish FVG 4300.42 → PDL 4110.91", message)
        self.assertIn("BULLISH A→FTA: H1 fractal low 4110.91 → H1 bearish FVG 4181.55", message)
        self.assertNotIn("BEARISH A→B: H1 fractal low", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
