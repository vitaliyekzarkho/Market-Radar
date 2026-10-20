import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from historical_evidence_assessment import assess_archives, assess_source, xau_case
from news_notification_policy import canonical


class HistoricalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_missing_root_never_falls_back_to_synthetic_or_runtime(self):
        result = assess_archives()
        self.assertEqual(result["verdict"], "INSUFFICIENT_EVIDENCE")
        self.assertTrue(all(v["quality"] == "MISSING" for v in result["sources"].values()))

    def test_missing_availability_prevents_historical_pass(self):
        self.write("sample.json", {"published_at_utc": "2026-10-19T00:00:00Z",
                                   "observed_at_utc": "2026-10-19T00:00:01Z"})
        result, _ = assess_source(self.root, "sample.json")
        self.assertEqual(result["quality"], "PARTIAL")
        self.assertEqual(result["missing_fields"]["available_at_utc"], 1)
        self.assertEqual(result["verdict"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result["properties"][0]["property"], "archive_parseable")

    def test_self_declared_complete_times_do_not_authenticate_history(self):
        self.write("sample.json", {"published_at_utc": "2026-10-19T00:00:00Z",
                                   "observed_at_utc": "2026-10-19T00:00:01Z",
                                   "available_at_utc": "2026-10-19T00:00:02Z"})
        self.assertEqual(assess_source(self.root, "sample.json")[0]["verdict"], "INSUFFICIENT_EVIDENCE")

    def test_invalid_chronology_is_untrusted_failure(self):
        self.write("sample.json", {"published_at_utc": "2026-10-20T00:00:00Z",
                                   "observed_at_utc": "2026-10-19T00:00:01Z",
                                   "available_at_utc": "2026-10-19T00:00:02Z"})
        result, _ = assess_source(self.root, "sample.json")
        self.assertEqual((result["quality"], result["verdict"]), ("UNTRUSTED", "FAIL"))

    def test_corrupt_archive_is_not_repaired(self):
        path = self.write("news/fast_news_stories.json", {})
        path.write_bytes(b"{broken")
        result = assess_archives(self.root)
        self.assertEqual(result["sources"]["stories"]["quality"], "UNTRUSTED")
        self.assertEqual(path.read_bytes(), b"{broken")

    def test_archive_bytes_and_names_remain_unchanged(self):
        self.write("news/fast_news_stories.json", {"stories": []})
        self.write("macro/rates_context.json", {"generated_at_utc": "2026-10-20T00:00:00Z"})
        def fingerprints():
            return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.root.rglob("*") if p.is_file()}
        before = fingerprints()
        with patch("socket.socket", side_effect=AssertionError("network")), \
             patch.object(Path, "write_text", side_effect=AssertionError("write")), \
             patch.object(Path, "write_bytes", side_effect=AssertionError("write")):
            first, second = assess_archives(self.root), assess_archives(self.root)
        self.assertEqual(canonical(first), canonical(second))
        self.assertEqual(fingerprints(), before)

    def test_private_content_not_echoed_in_report(self):
        secret_marker = "PRIVATE_CONTENT_MUST_NOT_APPEAR"
        self.write("telegram/GOLD_pro.json", {"message": secret_marker, "telegram_message_id": secret_marker})
        self.assertNotIn(secret_marker, canonical(assess_archives(self.root)))

    def test_reference_anchors_do_not_establish_prediction_or_pwl_touch(self):
        rows = []
        for timestamp, price in (("2026-10-19T07:54:39Z", 4178.91),
                                 ("2026-10-20T08:09:36Z", 4119.97),
                                 ("2026-10-20T15:40:19Z", 4133.20)):
            rows.append({"generated_at_utc": timestamp, "current_snapshot": {
                "current_price": price, "primary": {"direction": "bearish", "point_b": {"price": 4110.91}},
                "ideas": {"nested_bullish_pullback": {"state": "forming"}}}})
        result = xau_case(rows)
        self.assertTrue(all(a["record_found"] for a in result["anchors"]))
        self.assertTrue(result["later_price_record_found"])
        self.assertEqual(result["verdict"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result["pwl_reached"], "NOT_ESTABLISHED")
        self.assertEqual(result["bullish_recognition_before_move"], "NOT_ESTABLISHED")

    def test_unrelated_price_at_anchor_is_not_a_match(self):
        result = xau_case([{"generated_at_utc": "2026-10-19T07:54:39Z",
                            "current_snapshot": {"current_price": 9999}}])
        self.assertFalse(result["anchors"][0]["record_found"])

    def test_malformed_nested_snapshots_cannot_produce_anchor_pass(self):
        rows = [{"generated_at_utc": "2026-10-19T07:54:39Z", "current_snapshot": value}
                for value in ("invalid", [], {"primary": "invalid", "ideas": []})]
        result = xau_case(rows)
        self.assertFalse(any(a["record_found"] for a in result["anchors"]))
        self.assertEqual(result["verdict"], "INSUFFICIENT_EVIDENCE")

    def test_path_escape_fails_closed(self):
        result, _ = assess_source(self.root, "../outside.json")
        self.assertEqual(result["quality"], "UNTRUSTED")

    def test_csv_without_availability_stays_partial(self):
        path = self.root / "sample.csv"
        path.write_text("utc_time,close,snapshot_utc\n2026-10-19T00:00:00Z,100,2026-10-20T00:00:00Z\n")
        result, _ = assess_source(self.root, "sample.csv")
        self.assertEqual(result["missing_fields"]["available_at_utc"], 1)
        self.assertEqual(result["verdict"], "INSUFFICIENT_EVIDENCE")
