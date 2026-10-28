import unittest,copy
from test_time_authority_integration import fixture
from live_history_admission import assess,export_legacy_csv
from mt5_clock_authority import utc,epoch
class LiveAdmissionTests(unittest.TestCase):
    def test_current_not_requires_original_past_availability(self):
        r=assess(fixture(),decision_at_utc='2026-10-27T14:00:03Z')['rows'][0]
        self.assertTrue(r['BAR_CLOSED']);self.assertTrue(r['DATA_AVAILABLE_NOW']);self.assertTrue(r['price_order_only_admitted']);self.assertEqual(r['historical_availability_before_capture'],'UNKNOWN')
    def test_replay_before_receipt_rejected(self):
        r=assess(fixture(),decision_at_utc='2026-10-27T13:59:59Z',mode='historical_replay')
        self.assertTrue(all(not x['price_order_only_admitted'] for x in r['rows']))
    def test_replay_after_proven_receipt_can_use_that_receipt(self):
        r=assess(fixture(),decision_at_utc='2026-10-28T00:00:00Z',mode='historical_replay')['rows'][0]
        self.assertTrue(r['price_order_only_admitted']);self.assertFalse(r['DATA_AVAILABLE_NOW'])
    def test_shadow_no_backdating(self):
        self.assertFalse(assess(fixture(),decision_at_utc='2026-10-27T13:00:00Z',mode='shadow')['rows'][0]['price_order_only_admitted'])
    def test_current_bar_excluded(self):self.assertFalse(assess(fixture(),decision_at_utc='2026-10-27T14:00:03Z')['rows'][-1]['BAR_CLOSED'])
    def test_changed_bar_excluded(self):
        c=fixture();c['samples'][-1]['measurements'][1]['rows'][0]['close']=9
        self.assertFalse(assess(c,decision_at_utc='2026-10-27T14:00:03Z')['rows'][0]['BAR_CLOSED'])
    def test_all_five_timeframes(self):
        for tf in ('M15','H1','H4','D1','W1'):
            with self.subTest(tf=tf):self.assertTrue(assess(fixture(tf=tf),decision_at_utc='2026-10-27T14:00:03Z')['rows'][0]['BAR_CLOSED'])
    def test_no_offset_backfill_or_input_mutation(self):
        c=fixture();before=copy.deepcopy(c);r=assess(c,decision_at_utc='2026-10-27T14:00:03Z');self.assertEqual(c,before);self.assertTrue(all(x['verified_open_utc'] is None for x in r['rows']))
    def test_dst_unknown_not_invented(self):
        for stamp,offset in [('2026-10-25T00:59:00Z',7200),('2026-10-25T01:01:00Z',3600)]:
            r=assess(fixture(stamp,offset),decision_at_utc=utc(epoch(stamp)+3));self.assertIsNone(r['rows'][0]['verified_open_utc'])
    def test_expired_present_analysis_denied(self):self.assertFalse(assess(fixture(),decision_at_utc='2026-10-27T14:05:00Z')['rows'][0]['price_order_only_admitted'])
    def test_bad_source_order(self):
        c=fixture();c['samples'][-1]['measurements'][1]['rows'].reverse()
        with self.assertRaises(ValueError):assess(c,decision_at_utc='2026-10-27T14:00:03Z')
    def test_no_false_utc_export(self):
        with self.assertRaisesRegex(ValueError,'NO_FAKE_UTC'):export_legacy_csv()
    def test_unknown_mode(self):
        with self.assertRaises(ValueError):assess(fixture(),decision_at_utc='2026-10-27T14:00:03Z',mode='synthetic')
if __name__=='__main__':unittest.main()
