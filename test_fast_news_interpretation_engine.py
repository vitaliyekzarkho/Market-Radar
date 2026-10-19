from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fast_news_interpretation_engine import build_payload, classify_story_families, contextual_member_family, interpret_story


NOW = datetime(2026, 10, 9, 21, 0, tzinfo=timezone.utc)


def diesel_story(status: str = "developing_multi_origin") -> dict:
    return {
        "story_id": "diesel-1", "title": "Trump announces Russian diesel supply", "status": status,
        "permissions": {"confirm_driver": status == "officially_verified"},
        "members": [
            {"title": "Trump: Russia to supply 300k tons of diesel", "story_relationship": "initial_report"},
            {"title": "US Treasury allows supply of Russian diesel to global market", "story_relationship": "story_development"},
            {"title": "Sky repeats Trump diesel statement", "story_relationship": "redistribution_same_origin"},
        ],
    }


class FastNewsInterpretationTests(unittest.TestCase):
    def test_diesel_story_is_supply_relief_not_automatic_deescalation(self) -> None:
        families = classify_story_families(diesel_story())
        names = {item["event_family"] for item in families}
        self.assertIn("oil_supply_relief", names)
        self.assertNotIn("geopolitical_deescalation", names)

    def test_supply_relief_keeps_competing_gold_channels(self) -> None:
        result = interpret_story(diesel_story())
        ids = {item["mechanism_id"] for item in result["mechanism_candidates"]}
        self.assertIn("oil_inflation_risk_down", ids)
        self.assertIn("conditional_disinflation_support_for_gold", ids)
        self.assertIn("bullish", result["gold_paths"])
        self.assertIn("conditional", result["gold_paths"])
        self.assertEqual(result["directional_resolution"], "unresolved_pending_market_reaction")

    def test_unverified_multi_origin_story_cannot_confirm_fact_or_cause(self) -> None:
        result = interpret_story(diesel_story())
        self.assertFalse(result["fact_verified"])
        self.assertTrue(all(not item["confirmation_allowed"] for item in result["mechanism_candidates"]))
        self.assertFalse(any(result["permissions"].values()))

    def test_official_story_still_waits_for_market_confirmation(self) -> None:
        result = interpret_story(diesel_story("officially_verified"))
        self.assertTrue(result["fact_verified"])
        self.assertTrue(all(item["state"] == "verified_fact_pending_market_confirmation" for item in result["mechanism_candidates"]))
        self.assertFalse(result["permissions"]["confirm_cause"])

    def test_update_semantics_separate_development_from_redistribution(self) -> None:
        semantics = interpret_story(diesel_story())["update_semantics"]
        self.assertEqual(semantics["new_information_count"], 1)
        self.assertEqual(semantics["redistribution_count"], 1)

    def test_unknown_story_does_not_invent_market_mechanism(self) -> None:
        result = interpret_story({"story_id": "x", "title": "Officials meet Tuesday", "members": []})
        self.assertFalse(result["recognized"])
        self.assertEqual(result["mechanism_candidates"], [])

    def test_sanctions_easing_is_not_monetary_easing(self) -> None:
        member = {"title": "Easing sanctions will not end the war"}
        self.assertEqual(contextual_member_family(member), "unknown")

    def test_foreign_central_bank_does_not_get_direct_gold_mechanism(self) -> None:
        member = {"title": "Bank of Canada rate hike chances fall on jobs report"}
        self.assertEqual(contextual_member_family(member), "unknown")

    def test_fed_more_hikes_is_tightening_repricing(self) -> None:
        member = {"title": "Fed officials see more rate hikes coming"}
        self.assertEqual(contextual_member_family(member), "monetary_hawkish")

    def test_payload_never_grants_trade_permissions(self) -> None:
        payload = build_payload({"stories": [diesel_story()]}, NOW)
        self.assertEqual(payload["recognized_count"], 1)
        self.assertFalse(any(payload["permissions"].values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
