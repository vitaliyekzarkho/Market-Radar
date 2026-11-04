import copy,unittest
from unittest.mock import patch
from test_historical_admission_pipeline import capture,later
from history_receipts import select_history
import mt5_clock_probe

class RecoveryTests(unittest.TestCase):
    def test_https_error_keeps_real_status_and_valid_date_metadata(self):
        import urllib.error,email.message,email.utils
        headers=email.message.Message();headers['Date']=email.utils.formatdate(usegmt=True)
        error=urllib.error.HTTPError('https://www.cloudflare.com/cdn-cgi/trace',404,'Not Found',headers,None)
        with patch('urllib.request.urlopen',side_effect=error):
            result=mt5_clock_probe.https_date('https://www.cloudflare.com/cdn-cgi/trace')
        self.assertEqual(result['status'],'OK');self.assertEqual(result['http_status'],404)
        self.assertIn('offset_interval_seconds',result)
    def test_bad_prior_capture_does_not_poison_new_current_capture(self):
        bad=capture();bad['external_before'][0]['elapsed_seconds']=4
        good=later(capture(),10)
        result=select_history([('bad',bad,{}),('good',good,{})],'2026-10-27T14:00:15Z')
        self.assertEqual(result['quarantined'][0]['capture_id'],'bad')
        self.assertTrue(any(r['price_order_only_admitted'] for r in result['rows']))
        self.assertTrue(all(r['capture_id']=='good' for r in result['rows']))
    def test_latest_bad_capture_cannot_fall_back(self):
        bad=later(capture(),10);bad['external_after'][0]['status']='ERROR'
        with self.assertRaises(ValueError):select_history([('good',capture(),{}),('bad',bad,{})],'2026-10-27T14:00:15Z')
    def test_retries_are_bounded_and_failed_measurements_retained(self):
        bad={'status':'ERROR','elapsed_seconds':4}
        with patch.object(mt5_clock_probe,'ntp',return_value=bad) as n,patch.object(mt5_clock_probe,'https_date',return_value=bad) as h:
            result=mt5_clock_probe.probes()
        self.assertEqual(n.call_count,6);self.assertEqual(h.call_count,6)
        self.assertTrue(all(len(r['rejected_attempts'])==3 for r in result))

if __name__=='__main__':unittest.main()
