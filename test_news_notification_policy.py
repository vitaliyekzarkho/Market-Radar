from dataclasses import replace
from news_notification_policy import canonical, digest
from test_news_lifecycle_engine import Fixture, event, contradiction, policy, tm


class NotificationTests(Fixture):
    def test_policy_has_no_production_defaults(self):
        from news_notification_policy import Policy
        with self.assertRaises(TypeError):
            Policy()
        for value in [0, -1, True, float("inf")]:
            with self.assertRaises(ValueError):
                policy(retry_max_attempts=value)

    def test_same_policy_version_cannot_change_meaning(self):
        from news_lifecycle_engine import Lifecycle
        with self.assertRaises(ValueError):
            Lifecycle(self.s, policy(notification_ttl_seconds=99))

    def test_text_and_ordering_do_not_create_semantic_event(self):
        self.process(event(display_text="first wording"))
        result = self.process(event(2, 10, display_text="completely different wording"), 10)
        self.assertFalse(result["notification_ids"])
        self.assertEqual(len(self.s.outbox()), 1)
        self.assertNotIn("wording", canonical(self.s.outbox()))

    def test_independent_recipients_have_distinct_keys(self):
        self.process(recipients=["recipient_b", "recipient_a", "recipient_a"])
        rows = self.s.outbox()
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["id"], rows[1]["id"])
        self.delivered(rows[0]["id"])
        self.assertEqual([r["status"] for r in self.s.outbox()], ["delivered", "queued"])

    def test_information_label_permissions_and_provenance(self):
        self.process()
        payload = self.s.outbox()[0]["payload"]
        self.assertTrue(payload["informational_only"])
        self.assertEqual(payload["disclaimer"], "Informational update — not a trading signal.")
        self.assertEqual(payload["available_at_utc"], tm())
        self.assertEqual(payload["evidence_refs"], ["fact_1", "validation_1"])
        self.assertEqual(payload["expires_at_utc"], tm(300))
        self.assertFalse(any(payload["permissions"].values()))

    def test_correction_bypasses_ordinary_suppression(self):
        first = self.process()
        self.delivered(first["notification_ids"][0], 1)
        value = event(2, 2, change_kind="fact_correction", fact_status="corrected")
        value["evidence"][0].update(kind="correction", relation="corrects")
        self.process(value, 2)
        self.assertEqual(self.s.outbox()[-1]["next_at"], tm(2))

    def test_suppression_delays_material_updates_not_contradictions(self):
        first = self.process()
        self.delivered(first["notification_ids"][0], 1)
        second = self.process(event(2, 2, mechanism_refs=["new_mechanism"]), 2)
        self.assertIsNone(self.delivery.begin(second["notification_ids"][0], now=tm(3)))
        third = self.process(contradiction(3, 4), 4)
        self.assertEqual(self.s.outbox()[-1]["next_at"], tm(4))
        self.assertIsNotNone(self.delivery.begin(third["notification_ids"][0], now=tm(4)))

    def test_genuine_second_contradiction_is_not_text_deduped(self):
        first = self.process()
        self.delivered(first["notification_ids"][0])
        a = self.process(contradiction(2, 10), 10)
        self.process(event(3, 20), 20)
        b = self.process(contradiction(4, 30), 30)
        self.assertNotEqual(a["notification_ids"], b["notification_ids"])
        repeated = self.process(contradiction(5, 31), 31)
        self.assertFalse(repeated["notification_ids"])

    def test_unacknowledged_first_message_does_not_claim_prior_communication(self):
        self.process()
        result = self.process(contradiction(), 10)
        self.assertFalse(result["notification_ids"])

    def test_serialization_is_deterministic(self):
        self.assertEqual(digest({"b": [1], "a": 2}), digest({"a": 2, "b": [1]}))
        with self.assertRaises(ValueError):
            canonical({"x": float("nan")})
