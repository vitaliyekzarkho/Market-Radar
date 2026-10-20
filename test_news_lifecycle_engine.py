import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from news_lifecycle_engine import Lifecycle
from news_lifecycle_store import Store
from news_notification_policy import Policy
from news_delivery_state import Delivery


ROOT = Path(__file__).resolve().parent


def tm(seconds=0):
    return (datetime(2026, 10, 19, 12, tzinfo=timezone.utc) + timedelta(seconds=seconds)).isoformat()


def policy(**kwargs):
    return replace(Policy(**json.loads((ROOT / "tests/fixtures/c335/policy.json").read_text())), **kwargs)


def event(number=1, seconds=0, **changes):
    value = {
        "input_id": f"input_{number}", "upstream_story_id": "upstream_a",
        "member_event_ids": ["member_a"], "published_at_utc": tm(seconds-60),
        "observed_at_utc": tm(seconds-1), "available_at_utc": tm(seconds),
        "significant": True, "change_kind": "initial" if number == 1 else "development",
        "assessment": "supports", "fact_status": "verified", "hypothesis_status": "supported",
        "hypothesis_id": "hypothesis_a", "fact_refs": ["claim_a"], "mechanism_refs": ["mechanism_a"],
        "evidence": [
            {"ref": f"fact_{number}", "origin_ref": "origin_a", "subject_ref": "claim_a",
             "relation": "supports", "kind": "fact", "verified": True, "independent": True,
             "observed_at_utc": tm(seconds-1), "available_at_utc": tm(seconds)},
            {"ref": f"validation_{number}", "origin_ref": "measurement_a", "subject_ref": "hypothesis_a",
             "relation": "supports", "kind": "validation", "verified": True, "independent": False,
             "observed_at_utc": tm(seconds-1), "available_at_utc": tm(seconds)}
        ],
    }
    value.update(changes)
    return value


def contradiction(number=2, seconds=10):
    value = event(number, seconds, change_kind="hypothesis_contradiction",
                  hypothesis_status="contradicted", contradicts_hypothesis_id="hypothesis_a")
    value["evidence"][1]["relation"] = "contradicts"
    return value


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "lifecycle.sqlite"
        self.s = Store(self.path, namespace="synthetic", mode="offline")
        self.addCleanup(self.s.close)
        self.engine = Lifecycle(self.s, policy())
        self.delivery = Delivery(self.s)

    def process(self, value=None, seconds=0, recipients=None, **kwargs):
        return self.engine.process(value or event(), recipients or ["recipient_a"], now=tm(seconds), **kwargs)

    def delivered(self, key, seconds=1):
        attempt = self.delivery.begin(key, now=tm(seconds))
        self.delivery.acknowledge(key, attempt, outcome="delivered", receipt_ref="receipt_test", now=tm(seconds))


class LifecycleTests(Fixture):
    def test_stable_identity_after_regrouping_and_missing_members(self):
        first = self.process(event(member_event_ids=["member_a", "member_b"]))
        next_ = self.process(event(2, 10, upstream_story_id="upstream_b", member_event_ids=["member_b"]), 10)
        self.assertEqual(first["canonical_story_id"], next_["canonical_story_id"])
        self.assertEqual(first["episode_id"], next_["episode_id"])
        self.assertEqual(next_["semantic_version"], 1)
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM members").fetchone()[0], 2)

    def test_late_earlier_publication_does_not_replace_identity(self):
        first = self.process()
        second = self.process(event(2, 10, published_at_utc=tm(-1000),
                                    member_event_ids=["member_a", "member_earlier"]), 10)
        self.assertEqual(first["canonical_story_id"], second["canonical_story_id"])
        self.assertEqual(second["notification_ids"], [])

    def test_ambiguous_identity_is_journal_only(self):
        self.process()
        self.process(event(2, 1, upstream_story_id="upstream_b", member_event_ids=["member_b"]), 1)
        result = self.process(event(3, 2, upstream_story_id="new_group", member_event_ids=["member_a", "member_b"]), 2)
        self.assertEqual(result["identity_status"], "ambiguous")
        self.assertFalse(result["notification_ids"])
        self.assertEqual(len(self.s.outbox()), 2)

    def test_same_origin_redistribution_and_repetition_do_not_extend_lifetime(self):
        first = self.process()
        for n, kind in enumerate(["repeat", "redistribution", "recalculation"], 2):
            self.process(event(n, n*10, change_kind=kind), n*10)
        self.assertEqual(len(self.s.outbox()), 1)
        self.assertEqual(self.s.episode(first["canonical_story_id"])["last_material_at_utc"], tm())

    def test_independent_fact_development_is_material(self):
        self.process()
        value = event(2, 10, fact_refs=["claim_a", "claim_b"])
        value["evidence"][0]["subject_ref"] = "claim_b"
        result = self.process(value, 10)
        self.assertEqual(result["state"], "DEVELOPING")
        self.assertEqual(result["semantic_version"], 2)
        self.assertEqual(len(result["notification_ids"]), 1)

    def test_new_outlet_without_independence_does_not_justify_fact_update(self):
        self.process()
        value = event(2, 10, fact_refs=["claim_b"])
        value["evidence"][0].update(independent=False, origin_ref="another_outlet")
        result = self.process(value, 10)
        self.assertEqual(result["semantic_version"], 1)
        self.assertFalse(result["notification_ids"])

    def test_correction_has_distinct_notification(self):
        self.process()
        value = event(2, 10, fact_status="corrected", change_kind="fact_correction")
        value["evidence"][0].update(kind="correction", relation="corrects")
        self.process(value, 10)
        self.assertEqual(self.s.outbox()[-1]["payload"]["notification_kind"], "FACT_CORRECTION")

    def test_hypothesis_contradiction_only_for_previously_communicated_recipient(self):
        first = self.process(recipients=["recipient_a", "recipient_b"])
        self.delivered(first["notification_ids"][0])
        result = self.process(contradiction(), 10, recipients=["recipient_a", "recipient_b"])
        self.assertEqual(len(result["notification_ids"]), 1)
        payload = self.s.outbox()[-1]["payload"]
        self.assertEqual(payload["notification_kind"], "MARKET_HYPOTHESIS_CONTRADICTED")
        self.assertEqual(payload["contradicted_hypothesis_id"], "hypothesis_a")
        self.assertEqual(payload["recipient_key"], "recipient_a")

    def test_unverified_or_misdirected_contradiction_rejected(self):
        self.process()
        for field, value in [("verified", False), ("subject_ref", "another_hypothesis"), ("relation", "context")]:
            candidate = contradiction()
            candidate["evidence"][1][field] = value
            with self.assertRaises(ValueError):
                self.process(candidate, 10)
        self.assertEqual(len(self.s.outbox()), 1)

    def test_old_evidence_cannot_be_announced_as_subsequent_contradiction(self):
        first = self.process()
        self.delivered(first["notification_ids"][0])
        value = contradiction()
        value["evidence"][1]["observed_at_utc"] = tm(-5)
        result = self.process(value, 10)
        self.assertFalse(result["notification_ids"])

    def test_migration_preserves_queued_delivery_opportunity(self):
        first = self.process()
        self.engine.migrate(migration_id="migration_a", parents=[first["canonical_story_id"]],
                            children={"next_alias": ["member_a"]},
                            authorization_ref="review_a", now=tm(1))
        result = self.process(event(2, 2, upstream_story_id="next_alias"), 2)
        self.assertEqual(len(result["notification_ids"]), 1)
        self.assertEqual(self.s.outbox()[0]["status"], "superseded")
        self.assertEqual(self.s.outbox()[-1]["payload"]["notification_kind"], "MATERIAL_UPDATE")

    def test_unrelated_fact_attestation_does_not_support_added_fact(self):
        self.process()
        result = self.process(event(2, 10, fact_refs=["claim_a", "unrelated_claim"]), 10)
        self.assertEqual(result["semantic_version"], 1)
        self.assertFalse(result["notification_ids"])

    def test_pa_conflict_is_not_hypothesis_disproof(self):
        self.process()
        self.process(event(2, 10, assessment="conflicts", change_kind="context_update"), 10)
        message = self.s.outbox()[-1]["payload"]
        self.assertEqual(message["notification_kind"], "PA_HTF_CONTEXT_CONFLICT")
        self.assertIsNone(message["contradicted_hypothesis_id"])

    def test_support_conflict_support_retains_distinct_versions(self):
        self.process()
        second = self.process(event(2, 10, assessment="conflicts"), 10)
        third = self.process(event(3, 20), 20)
        self.assertEqual([second["semantic_version"], third["semantic_version"]], [2, 3])
        self.assertEqual(len({r["id"] for r in self.s.outbox()}), 3)

    def test_complete_scenario_and_inputs_preserved(self):
        scenario = {"direction": "bearish", "score": 91, "targets": [1, 2],
                    "invalidation": {"price": 3}, "permissions": {"entry": True}}
        value = event()
        before = copy.deepcopy((scenario, value))
        result = self.process(value, scenario=scenario)
        self.assertEqual((scenario, value), before)
        self.assertEqual(result["scenario"], scenario)
        result["scenario"]["targets"].append(4)
        self.assertEqual((scenario, value), before)
        self.assertFalse(any(self.s.outbox()[0]["payload"]["permissions"].values()))

    def test_expiry_does_not_resolve_fact_and_new_episode_links_old(self):
        first = self.process()
        self.engine.expire(now=tm(600))
        ep = self.s.episode(first["canonical_story_id"])
        self.assertEqual(ep["state"], "EXPIRED")
        self.assertEqual(ep["semantic"]["fact_status"], "verified")
        repeated = self.process(event(2, 601), 601)
        self.assertEqual(repeated["episode_id"], first["episode_id"])
        changed = self.process(event(3, 602, mechanism_refs=["mechanism_b"]), 602)
        self.assertNotEqual(changed["episode_id"], first["episode_id"])
        self.assertEqual(self.s.episode(first["canonical_story_id"])["predecessor_episode_id"], first["episode_id"])

    def test_resolution_requires_evidence_and_is_terminal(self):
        first = self.process()
        value = event(2, 10, change_kind="resolution", resolution_reason="authorized_manual")
        with self.assertRaises(ValueError):
            self.process(value, 10)
        value["evidence"].append(dict(value["evidence"][0], ref="resolution_a", kind="resolution", relation="resolves"))
        result = self.process(value, 10)
        self.assertEqual(result["state"], "RESOLVED")
        self.assertEqual(result["episode_id"], first["episode_id"])
        self.assertEqual(self.process(event(3, 11), 11)["state"], "RESOLVED")

    def test_future_stale_and_naive_input_rejected_without_mutation(self):
        for value in [event(available_at_utc=tm(1)), event(observed_at_utc=tm(2)),
                      event(available_at_utc="2026-10-19T12:00:00"), event(1, -8000)]:
            with self.assertRaises(ValueError):
                self.process(value)
        self.assertFalse(self.s.outbox())

    def test_equivalent_timezones_and_canonical_order_are_same_input(self):
        value = event(member_event_ids=["member_a", "member_b"], mechanism_refs=["mechanism_b", "mechanism_a"])
        self.process(value)
        value["available_at_utc"] = "2026-10-19T13:00:00+01:00"
        value["member_event_ids"].reverse()
        value["mechanism_refs"].reverse()
        self.assertTrue(self.process(value)["duplicate"])

    def test_late_available_version_never_rewrites_latest_decision(self):
        first = self.process()
        self.process(event(2, 20, assessment="conflicts"), 20)
        result = self.process(event(3, 10), 30)
        self.assertEqual(result["reason"], "late_version_journal_only")
        self.assertEqual(self.s.episode(first["canonical_story_id"])["semantic"]["assessment"], "conflicts")

    def test_max_lifetime_bounds_material_updates(self):
        self.engine = Lifecycle(self.s, policy(version="short_test", episode_max_seconds=30))
        first = self.process()
        self.process(event(2, 20, mechanism_refs=["mechanism_b"]), 20)
        self.engine.expire(now=tm(30))
        self.assertEqual(self.s.episode(first["canonical_story_id"])["state"], "EXPIRED")

    def test_merge_split_preserves_history_and_does_not_reannounce(self):
        a = self.process()
        self.delivered(a["notification_ids"][0])
        b = self.process(event(2, 2, upstream_story_id="upstream_b", member_event_ids=["member_b"]), 2)
        merged = self.engine.migrate(migration_id="merge_a", parents=[a["canonical_story_id"], b["canonical_story_id"]],
                                    children={"merged_alias": ["member_a", "member_b"]}, authorization_ref="review_a", now=tm(3))[0]
        result = self.process(event(3, 4, upstream_story_id="merged_alias", member_event_ids=["member_a", "member_b"]), 4)
        self.assertEqual(result["canonical_story_id"], merged)
        self.assertFalse(result["notification_ids"])
        children = self.engine.migrate(migration_id="split_a", parents=[merged],
                                       children={"split_left": ["member_a"], "split_right": ["member_b"]},
                                       authorization_ref="review_b", now=tm(5))
        self.assertTrue(all(self.s.communicated(child, "recipient_a", "hypothesis_a") for child in children))
        self.assertEqual(len(self.s.db.execute("SELECT * FROM migrations").fetchall()), 4)
        self.assertEqual(self.s.outbox()[0]["status"], "delivered")

    def test_migration_without_complete_partition_rolls_back(self):
        a = self.process(event(member_event_ids=["member_a", "member_b"]))
        with self.assertRaises(ValueError):
            self.engine.migrate(migration_id="bad", parents=[a["canonical_story_id"]],
                                children={"child": ["member_a"]}, authorization_ref="review", now=tm(2))
        self.assertEqual(self.s.db.execute("SELECT status FROM stories").fetchone()[0], "active")

    def test_no_network_or_mt5_dependency(self):
        import ast
        for name in ["news_lifecycle_engine", "news_notification_policy", "news_lifecycle_store", "news_delivery_state"]:
            tree = ast.parse((ROOT / (name + ".py")).read_text(encoding="utf-8"))
            modules = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
            modules += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
            self.assertFalse(any(m.split(".")[0] in {"socket", "urllib", "requests", "MetaTrader5", "telegram_notifier", "subprocess"} for m in modules))
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            self.process()
