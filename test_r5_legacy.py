import unittest,tempfile
from pathlib import Path
from r5_input import normalize_csv
from r5_fixtures import candles
from r5_legacy import analyze,CORE

class LegacyBridgeTests(unittest.TestCase):
    def test_actual_pipeline_and_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            views={tf:normalize_csv(candles(tf),symbol='EURUSD',timeframe=tf,cutoff='2026-10-20T12:00:00Z') for tf in (*CORE,'M5')}
            a=analyze(views,root=Path(tmp)/'a',symbol='EURUSD',cutoff='2026-10-20T12:00:00Z')
            b=analyze(views,root=Path(tmp)/'b',symbol='EURUSD',cutoff='2026-10-20T12:00:00Z')
            self.assertEqual(a,b);self.assertEqual(a['scenario']['symbol'],'EURUSD');self.assertIn('scenario_state.main',a['calls'])
            self.assertIn('market_pulse_engine.build_payload',a['calls'])
    def test_missing_input_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=analyze({},root=tmp,symbol='XAUUSD',cutoff='2026-10-20T12:00:00Z')
            self.assertEqual(r['status'],'INSUFFICIENT_DATA');self.assertEqual(r['calls'],[])
