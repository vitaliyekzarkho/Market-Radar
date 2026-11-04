import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_time_authority_integration import fixture
from time_ingestion import persist_capture, verified_utc_view, verified_delivery_window
from historical_admission_pipeline import prepare_runtime
from test_historical_admission_pipeline import capture


class IntegratedTimeGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def persist(self, at='2026-10-27T14:00:00Z', offset=3600):
        from mt5_clock_authority import utc, epoch
        cutoff = utc(epoch(at)+5)
        persist_capture(self.root, 'capture', fixture(at,offset), cutoff)
        return cutoff

    def test_no_offset_backfill(self):
        cutoff = self.persist()
        view = verified_utc_view(self.root, cutoff)
        self.assertEqual(view['historical_timezone_rule'],'UNKNOWN')
        self.assertTrue(all(not r['utc_feature_admitted'] for r in view['rows']))
        self.assertTrue(all(r['verified_utc_timestamp'] is None for r in view['rows']))

    def test_future_receipt_not_admitted(self):
        self.persist()
        self.assertFalse(any(r['utc_feature_admitted'] for r in verified_utc_view(self.root,'2026-10-27T13:59:59Z')['rows']))

    def test_current_clock_allows_dublin_daytime(self):
        self.assertTrue(verified_delivery_window(self.root,self.persist())[0])

    def test_clock_expiry_blocks_delivery(self):
        self.persist()
        self.assertFalse(verified_delivery_window(self.root,'2026-10-27T14:02:00Z')[0])

    def test_no_authority_blocks_delivery(self):
        self.assertFalse(verified_delivery_window(self.root,'2026-10-27T14:00:00Z')[0])

    def test_dublin_summer_0800_is_0700_utc(self):
        self.assertTrue(verified_delivery_window(self.root,self.persist('2026-07-10T07:00:00Z',7200))[0])

    def test_dublin_winter_before_0800_denied(self):
        self.assertFalse(verified_delivery_window(self.root,self.persist('2026-10-27T07:00:00Z'))[0])

    def test_dublin_summer_2000_is_1900_utc(self):
        self.assertFalse(verified_delivery_window(self.root,self.persist('2026-07-10T19:00:00Z',7200))[0])

    def test_dst_fold_does_not_authorize_repeated_local_hour(self):
        # Both UTC occurrences of local 01:xx are outside the alert window.
        for n, at in enumerate(('2026-10-25T00:00:00Z','2026-10-25T01:00:00Z')):
            with tempfile.TemporaryDirectory() as d:
                from mt5_clock_authority import utc, epoch
                cutoff = utc(epoch(at)+5)
                persist_capture(d, str(n), fixture(at,7200 if n==0 else 3600), cutoff)
                self.assertFalse(verified_delivery_window(d,cutoff)[0])

    def test_runtime_uses_existing_core_but_blocks_unverified_collectors(self):
        c = capture()
        for tf in ('W1','D1','H4','H1'):
            other = capture(tf)
            for i,s in enumerate(c['samples']):s['measurements'].append(other['samples'][i]['measurements'][1])
        persist_capture(self.root,'all',c,'2026-10-27T14:00:05Z')
        result = prepare_runtime(self.root,'2026-10-27T14:00:05Z')
        self.assertEqual(result['scenario_status'],'EVALUATED')
        self.assertEqual(result['status'],'READY')
        self.assertTrue((self.root/'data/states/GOLD_pro.json').exists())
        self.assertFalse((self.root/'data/scenario_states/GOLD_pro.json').exists())
        self.assertFalse((self.root/'data/raw').exists())
        self.assertFalse(result['transport_authorized'])

    def test_runner_does_not_fall_through_to_telegram(self):
        import run_radar
        with patch.object(run_radar,'run_step',return_value=(True,'HISTORICAL_ADMISSION_CAPTURE: test')) as step:
            self.assertFalse(run_radar.run_pipeline(self.root,False))
        self.assertEqual(step.call_count,1)
        self.assertEqual(json.loads((self.root/run_radar.HEARTBEAT_FILE).read_text())['status'],'BLOCKED')

    def test_notifier_blocks_before_transport_without_admission(self):
        import telegram_notifier as t
        import sys
        n=self.root/'notification.json';n.write_text(json.dumps({'should_send':True,'message':'TEST'}))
        with patch.object(t,'NOTIFICATION_FILE',n),patch.object(t,'STATE_FILE',self.root/'state.json'),patch.object(t,'DELIVERY_FILE',self.root/'delivery.json'),patch.object(sys,'argv',['telegram_notifier.py']),patch.object(t,'send_telegram') as send,patch('time_ingestion.verified_delivery_window',return_value=(False,'NO_PROOF')):
            self.assertEqual(t.main(),1)
            send.assert_not_called()
        self.assertFalse((self.root/'state.json').exists())


if __name__ == '__main__':unittest.main()
