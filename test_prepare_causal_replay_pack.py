from __future__ import annotations

import unittest
from types import SimpleNamespace

from prepare_causal_replay_pack import (
    channel_status, choose_gold_symbol, historical_event,
    select_market_symbols, select_verified_event,
)


def sym(name: str, description: str = "") -> SimpleNamespace:
    return SimpleNamespace(name=name, description=description)


class PrepareCausalReplayPackTests(unittest.TestCase):

    def test_selects_one_authoritative_released_event(self) -> None:
        selected = select_verified_event({"events": [{
            "title": "ISM Services PMI", "status": "released", "actual": "54.9",
            "source_verified": True, "authoritative_fact": True,
        }]}, "ISM Services PMI")
        self.assertEqual(selected["actual"], "54.9")

    def test_rejects_calendar_only_or_ambiguous_event(self) -> None:
        with self.assertRaisesRegex(ValueError, "VERIFIED_EVENT_MATCH_COUNT:0"):
            select_verified_event({"events": [{
                "title": "ISM Services PMI", "status": "scheduled", "actual": None,
                "source_verified": False, "authoritative_fact": False,
            }]}, "ISM Services PMI")

    def test_historical_event_preserves_verified_release_fields(self) -> None:
        args = SimpleNamespace(
            event_id="ism-services-20261005", event_family="growth_labor_downside_surprise",
            title="ignored", event_time="2026-10-05T14:00:00Z", source="ignored", source_url="ignored",
        )
        result = historical_event(args, {
            "title": "ISM Services PMI", "event_time_utc": "2026-10-05T14:00:00Z",
            "actual": "54.9", "forecast": "55.1", "previous": "55.4",
            "source_verified": True, "authoritative_fact": True, "primary_source": False,
            "source_transport": "official_syndicated_release",
        })
        self.assertEqual(result["actual"], "54.9")
        self.assertEqual(result["forecast"], "55.1")
        self.assertFalse(result["primary_source"])
        self.assertEqual(result["source_transport"], "official_syndicated_release")

    def test_prefers_configured_gold_symbol(self) -> None:
        symbols = [sym("XAUUSD"), sym("GOLD.pro")]
        self.assertEqual(choose_gold_symbol(symbols, "XAUUSD"), "XAUUSD")

    def test_finds_gold_by_description_when_alias_is_missing(self) -> None:
        self.assertEqual(choose_gold_symbol([sym("METAL1", "Spot Gold XAUUSD")]), "METAL1")

    def test_selects_brent_and_direct_dxy(self) -> None:
        result = select_market_symbols([
            sym("GOLD.pro"), sym("OILBRNT.pro", "Brent Crude"), sym("DXY.pro"),
        ])
        self.assertEqual(result["oil"], "OILBRNT.pro")
        self.assertEqual(result["usd"]["mode"], "direct_dxy")

    def test_falls_back_to_usd_breadth(self) -> None:
        result = select_market_symbols([
            sym("GOLD.pro"), sym("UKOIL.pro"), sym("EURUSD.pro"), sym("GBPUSD.pro"),
            sym("USDCAD.pro"), sym("USDCHF.pro"),
        ])
        self.assertEqual(result["usd"]["mode"], "breadth")
        self.assertGreaterEqual(len(result["usd"]["breadth"]), 3)

    def test_less_than_three_fx_pairs_is_not_usd_context(self) -> None:
        result = select_market_symbols([sym("GOLD.pro"), sym("EURUSD.pro"), sym("GBPUSD.pro")])
        self.assertEqual(result["usd"]["mode"], "unavailable")

    def test_channel_status_requires_gold_oil_and_usd(self) -> None:
        selection = {
            "gold": "GOLD.pro", "oil": "OILBRNT.pro",
            "usd": {"mode": "direct_dxy", "dxy": "DXY.pro", "breadth": {}}, "rates": {},
        }
        downloads = {name: {} for name in ("GOLD.pro", "OILBRNT.pro", "DXY.pro")}
        status = channel_status(selection, downloads)
        self.assertTrue(all(value["available"] for value in status.values() if value["required"]))

    def test_missing_oil_is_explicit(self) -> None:
        selection = {
            "gold": "GOLD.pro", "oil": None,
            "usd": {"mode": "direct_dxy", "dxy": "DXY.pro", "breadth": {}}, "rates": {},
        }
        status = channel_status(selection, {"GOLD.pro": {}, "DXY.pro": {}})
        self.assertFalse(status["oil"]["available"])

    def test_downloaded_breadth_needs_three_components(self) -> None:
        selection = {
            "gold": "GOLD.pro", "oil": "UKOIL.pro", "rates": {},
            "usd": {"mode": "breadth", "dxy": None, "breadth": {
                "EURUSD": "EURUSD.pro", "GBPUSD": "GBPUSD.pro", "USDCAD": "USDCAD.pro",
            }},
        }
        downloads = {"GOLD.pro": {}, "UKOIL.pro": {}, "EURUSD.pro": {}, "GBPUSD.pro": {}}
        self.assertFalse(channel_status(selection, downloads)["usd"]["available"])
        downloads["USDCAD.pro"] = {}
        self.assertTrue(channel_status(selection, downloads)["usd"]["available"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
