from test_news_lifecycle_engine import Fixture, event, tm


class DeliveryTests(Fixture):
    def key(self):
        return self.process()["notification_ids"][0]

    def test_attempt_is_durable_before_acknowledgment(self):
        key = self.key()
        self.assertEqual(self.delivery.begin(key, now=tm(1)), 1)
        self.assertEqual(self.s.outbox()[0]["status"], "attempted")
        self.assertIsNone(self.delivery.begin(key, now=tm(2)))

    def test_timeout_is_unknown_and_never_auto_retried(self):
        key = self.key()
        number = self.delivery.begin(key, now=tm(1))
        self.delivery.acknowledge(key, number, outcome="timeout", now=tm(2))
        self.assertEqual(self.s.outbox()[0]["status"], "unknown")
        self.assertIsNone(self.delivery.begin(key, now=tm(20)))

    def test_known_transient_failure_backoff_and_limit(self):
        key = self.key()
        for number, start, ack in [(1, 1, 2), (2, 12, 13), (3, 33, 34)]:
            self.assertEqual(self.delivery.begin(key, now=tm(start)), number)
            self.delivery.acknowledge(key, number, outcome="transient_failure", now=tm(ack))
            self.assertIsNone(self.delivery.begin(key, now=tm(ack)))
        self.assertIsNone(self.delivery.begin(key, now=tm(100)))
        self.assertEqual(self.s.outbox()[0]["attempts"], 3)

    def test_permanent_failure_is_not_retried(self):
        key = self.key()
        number = self.delivery.begin(key, now=tm(1))
        self.delivery.acknowledge(key, number, outcome="permanent_failure", now=tm(2))
        self.assertIsNone(self.delivery.begin(key, now=tm(30)))
        self.assertEqual(self.s.outbox()[0]["status"], "failed")

    def test_expired_queue_is_not_attempted(self):
        key = self.key()
        self.assertIsNone(self.delivery.begin(key, now=tm(300)))
        self.assertEqual(self.s.outbox()[0]["status"], "expired")

    def test_crash_recovery_only_after_attempt_lease(self):
        key = self.key()
        self.delivery.begin(key, now=tm(1))
        self.assertFalse(self.delivery.recover(now=tm(20)))
        self.assertEqual(self.delivery.recover(now=tm(21)), [key])
        self.assertEqual(self.s.outbox()[0]["status"], "unknown")

    def test_manual_retry_requires_audited_risk_acceptance(self):
        key = self.key()
        self.delivery.begin(key, now=tm(1))
        self.delivery.recover(now=tm(21))
        with self.assertRaises(ValueError):
            self.delivery.resolve_unknown(key, decision_ref="decision_a", action="retry",
                                          acknowledge_duplicate_risk=False, now=tm(22))
        self.delivery.resolve_unknown(key, decision_ref="decision_a", action="retry",
                                      acknowledge_duplicate_risk=True, now=tm(22))
        self.assertEqual(self.delivery.begin(key, now=tm(22)), 2)
        with self.assertRaises(ValueError):
            self.delivery.acknowledge(key, 1, outcome="delivered", receipt_ref="stale_receipt", now=tm(23))

    def test_unknown_cannot_be_retried_after_expiry(self):
        key = self.key()
        self.delivery.begin(key, now=tm(1))
        self.delivery.recover(now=tm(21))
        with self.assertRaises(ValueError):
            self.delivery.resolve_unknown(key, decision_ref="decision_a", action="retry",
                                          acknowledge_duplicate_risk=True, now=tm(300))
        self.delivery.resolve_unknown(key, decision_ref="decision_a", action="cancel",
                                      acknowledge_duplicate_risk=False, now=tm(300))
        self.assertEqual(self.s.outbox()[0]["status"], "cancelled")

    def test_late_positive_receipt_resolves_unknown(self):
        key = self.key()
        self.delivery.begin(key, now=tm(1))
        self.delivery.recover(now=tm(21))
        self.delivery.acknowledge(key, 1, outcome="delivered", receipt_ref="late_receipt", now=tm(22))
        self.assertEqual(self.s.outbox()[0]["status"], "delivered")

    def test_unsafe_error_and_receipt_are_not_stored(self):
        key = self.key()
        self.delivery.begin(key, now=tm(1))
        with self.assertRaises(ValueError):
            self.delivery.acknowledge(key, 1, outcome="https://secret.example/token", now=tm(2))
        with self.assertRaises(ValueError):
            self.delivery.acknowledge(key, 1, outcome="delivered", receipt_ref="123:secret", now=tm(2))
        self.assertEqual(self.s.outbox()[0]["status"], "attempted")

    def test_explicit_disposition_and_duplicate_ack_guard(self):
        key = self.key()
        self.delivery.dispose(key, disposition="superseded", decision_ref="review", now=tm(1))
        self.assertIsNone(self.delivery.begin(key, now=tm(2)))
        with self.assertRaises(ValueError):
            self.delivery.acknowledge(key, 1, outcome="delivered", receipt_ref="receipt_a", now=tm(3))
