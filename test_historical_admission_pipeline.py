import copy
import json
import tempfile
import unittest
from pathlib import Path
import pandas as pd
from test_time_authority_integration import fixture
from mt5_clock_authority import ClockLedger, epoch, utc, PERIODS
from history_receipts import select_history
from historical_admission_pipeline import evaluate, journal_result
from source_time import admitted_frame
from market_features import analyse_timeframe
from order_flow_structure import analyse_frame, nested_parent_check


def capture(tf='M15'):
    c = fixture(tf=tf)
    for sample in c['samples']:
        rows = sample['measurements'][1]['rows']
        old = c['samples'][0]['measurements'][1]['rows'][-1]
        history = []
        for i in range(40, 0, -1):
            p = 10 + (i % 7)
            history.append(dict(old, time=old['time'] - i * PERIODS[tf], open=p, high=p+2, low=p-2, close=p+1, tick_volume=20+i))
        sample['measurements'][1]['rows'] = history + rows
    return c


def later(c, seconds=10):
    c = copy.deepcopy(c)
    def move(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k.endswith('_utc') and isinstance(v, str):
                    x[k] = utc(epoch(v) + seconds)
                else:
                    move(v)
        elif isinstance(x, list):
            for v in x: move(v)
    move(c)
    for sample in c['samples']:
        tick = sample['measurements'][0]['value']
        tick['time'] += seconds
        tick['time_msc'] += seconds * 1000
    return c


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ledger = ClockLedger(self.root/'clock.db')
        self.c = capture()
        self.ledger.ingest('one', self.c)

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def result(self, at='2026-10-27T14:00:05Z', mode='current_analysis'):
        return evaluate(self.ledger.records(), at, mode)

    def test_live_price_features_and_structure(self):
        r = self.result()
        self.assertEqual(r['features']['M15']['bars_used'], 41)
        self.assertEqual(r['order_flow']['timeframes']['M15']['bars_used'], 41)
        self.assertTrue(r['features']['M15']['last_closed_time'].startswith('source-time:'))

    def test_no_fake_utc_column(self):
        df = admitted_frame(self.result()['admission']['rows'], 'M15')
        self.assertNotIn('utc_time', df)
        self.assertNotIn('_time', df)

    def test_current_bar_rejected(self):
        self.assertFalse(self.result()['admission']['rows'][-1]['price_order_only_admitted'])

    def test_expired_capture_rejected(self):
        self.assertEqual(self.result('2026-10-27T14:02:00Z')['features']['M15']['bars_used'], 0)

    def test_before_receipt_replay(self):
        self.assertEqual(self.result('2026-10-27T13:59:00Z', 'historical_replay')['admission']['rows'], [])

    def test_external_after_proof_must_be_available(self):
        self.assertEqual(self.result('2026-10-27T14:00:03Z')['admission']['rows'], [])

    def test_replay_after_receipt_not_require_present_freshness(self):
        self.assertEqual(self.result('2026-10-28T00:00:00Z', 'historical_replay')['features']['M15']['bars_used'], 41)

    def test_first_exact_version_receipt_preserved(self):
        first = self.result()['admission']['rows'][0]
        self.ledger.ingest('two', later(self.c))
        new = self.result('2026-10-27T14:00:15Z')['admission']['rows'][0]
        self.assertEqual(first['first_observed_at_utc'], new['first_observed_at_utc'])
        self.assertEqual(first['evidence_id'], new['evidence_id'])

    def test_correction_supersedes_without_backdating(self):
        first = self.result()['admission']['rows'][0]
        c = later(self.c)
        for s in c['samples']: s['measurements'][1]['rows'][0]['close'] += .1
        self.ledger.ingest('two', c)
        new = self.result('2026-10-27T14:00:15Z')['admission']['rows'][0]
        self.assertEqual(new['supersedes'], first['evidence_id'])
        self.assertGreater(new['first_observed_at_utc'], first['first_observed_at_utc'])
        self.assertEqual(self.result()['admission']['rows'][0]['evidence_id'], first['evidence_id'])

    def test_unstable_correction_does_not_restore_old_content(self):
        c = later(self.c)
        c['samples'][-1]['measurements'][1]['rows'][0]['close'] += .1
        self.ledger.ingest('two', c)
        row = self.result('2026-10-27T14:00:15Z')['admission']['rows'][0]
        self.assertFalse(row['price_order_only_admitted'])

    def test_restart_and_duplicate(self):
        expected = self.result()
        self.ledger.close()
        self.ledger = ClockLedger(self.root/'clock.db')
        self.ledger.ingest('one', self.c)
        self.assertEqual(expected, self.result())
        self.assertEqual(len(self.ledger.records()), 1)

    def test_sessions_unknown_without_score(self):
        r = self.result()
        self.assertEqual(r['sessions']['status'], 'UNKNOWN')
        self.assertEqual(r['key_levels']['status'], 'UNKNOWN')
        self.assertFalse(r['sessions']['scoring_applied'])
        self.assertFalse(r['eligibility']['delivery_authorized'])

    def test_numerical_methodology_unchanged(self):
        frame = admitted_frame(self.result()['admission']['rows'], 'M15')
        legacy = frame.drop(columns=['source_time']).copy()
        # Synthetic comparison axis only; never sent to the bridge or journal.
        legacy['utc_time'] = pd.date_range('2020-01-01', periods=len(frame), freq='15min', tz='UTC')
        legacy['_time'] = legacy['utc_time']
        a = analyse_timeframe('M15', admitted_data=frame)
        b = analyse_timeframe('M15', admitted_data=legacy)
        def remove_times(x):
            if isinstance(x, dict): return {k: remove_times(v) for k,v in x.items() if 'time' not in k}
            if isinstance(x, list): return [remove_times(v) for v in x]
            return x
        self.assertEqual(remove_times(a), remove_times(b))
        self.assertEqual(remove_times(analyse_frame(frame,'M15')), remove_times(analyse_frame(legacy,'M15')))

    def test_nested_interval_unknown(self):
        frame = admitted_frame(self.result()['admission']['rows'], 'M15')
        self.assertEqual(nested_parent_check(frame,frame,'H4','H1',4)['status'], 'UNKNOWN')

    def test_journal_lineage_and_idempotency(self):
        (self.root/'OFFLINE_ADMISSION_SANDBOX').touch()
        r = self.result()
        a = journal_result(self.root, 'one', r)
        before = (self.root/'data/cdr/DECISION_JOURNAL.json').read_bytes()
        self.assertEqual(a, journal_result(self.root, 'one', r))
        self.assertEqual(before, (self.root/'data/cdr/DECISION_JOURNAL.json').read_bytes())
        events = json.loads(before)
        self.assertEqual(len(events), 4)
        self.assertTrue(all(e['receipt_lineage'] == r['admission'] for e in events))
        self.assertTrue(all(not e['should_notify'] and not e['trade_authorized'] for e in events))

    def test_journal_requires_explicit_sandbox(self):
        with self.assertRaisesRegex(ValueError, 'SANDBOX'):
            journal_result(self.root, 'one', self.result())

    def test_journal_conflict_does_not_append(self):
        (self.root/'OFFLINE_ADMISSION_SANDBOX').touch()
        journal_result(self.root, 'one', self.result())
        with self.assertRaisesRegex(ValueError,'CONFLICT'):
            journal_result(self.root, 'one', self.result('2026-10-27T14:00:06Z'))
        self.assertEqual(len(json.loads((self.root/'data/cdr/DECISION_JOURNAL.json').read_text())),4)

    def test_all_timeframes_existing_scenario_pipeline(self):
        c = capture('M15')
        for tf in ('W1','D1','H4','H1'):
            other = capture(tf)
            for i, sample in enumerate(c['samples']):
                sample['measurements'].append(other['samples'][i]['measurements'][1])
        ledger = ClockLedger(self.root/'all.db')
        try:
            ledger.ingest('all', c)
            r = evaluate(ledger.records(), '2026-10-27T14:00:05Z')
            self.assertEqual(r['scenario_status'], 'EVALUATED')
            self.assertIn('primary_scenario', r['scenario'])
            self.assertFalse(r['eligibility']['delivery_authorized'])
            self.assertIsNone(r['scenario']['generated_at_utc'])
            (self.root/'OFFLINE_ADMISSION_SANDBOX').touch()
            journal_result(self.root, 'all', r)
            from historical_admission_pipeline import previous_scenario_at
            self.assertIsNone(previous_scenario_at(self.root, '2026-10-27T14:00:05Z', 'current_analysis'))
            self.assertEqual(previous_scenario_at(self.root, '2026-10-27T14:00:06Z', 'current_analysis'), r['scenario'])
            self.assertIsNone(previous_scenario_at(self.root, '2026-10-27T14:00:06Z', 'historical_replay'))
            again = evaluate(ledger.records(), '2026-10-27T14:00:05Z', previous_scenario=r['scenario'])
            self.assertEqual(r['scenario']['primary_scenario'], again['scenario']['primary_scenario'])
        finally:
            ledger.close()

    def test_unknown_source_lifecycle_does_not_use_full_history(self):
        from level_state import MarketObject, object_window
        frame = admitted_frame(self.result()['admission']['rows'], 'M15')
        obj = MarketObject('x','fvg','x','M15',1,2,'support','test',active_from='invalid')
        self.assertTrue(object_window(obj,frame,{}).empty)
        obj.active_from = frame.iloc[0]['source_time'].reference()
        selected = object_window(obj,frame,{})
        self.assertEqual(len(selected),len(frame)-1)
        self.assertGreater(selected.iloc[0]['time'],frame.iloc[0]['time'])

    def test_source_time_domain_and_cross_frame_order(self):
        from source_time import SourceTime
        a = SourceTime('OANDA_MT5_LOCAL','GOLD.pro','H1',10)
        b = SourceTime('OANDA_MT5_LOCAL','GOLD.pro','M15',10)
        self.assertEqual(a,b)
        self.assertFalse(a > b)
        self.assertEqual(hash(a),hash(b))
        with self.assertRaises(ValueError):
            _ = a < pd.Timestamp('2026-01-01',tz='UTC')

    def test_owned_worker_timeout_is_failure(self):
        from prepare_hour_rollover import run_owned_worker
        import subprocess
        from unittest.mock import patch
        with patch('r6_8_job.run_owned',return_value={'timed_out':True,'exit_code':124}) as call:
            with self.assertRaisesRegex(RuntimeError,'TIMEOUT'):
                run_owned_worker(['--boundary','2026-10-28T20:00:00Z'])
            self.assertEqual(call.call_args.kwargs['seconds'],45)
            self.assertIn('--owned-worker',call.call_args.args[0])

    def test_previous_scenario_not_mutated(self):
        before = {'primary_scenario':None,'publication_state':'silent','time_basis':'SOURCE_ORDER_ONLY'}
        copy_before = copy.deepcopy(before)
        evaluate(self.ledger.records(),'2026-10-27T14:00:05Z',previous_scenario=before)
        self.assertEqual(before,copy_before)

    def test_bad_ohlcv_not_silently_dropped(self):
        rows = self.result()['admission']['rows']
        rows[0]['raw_ohlcv']['close'] = 999
        with self.assertRaisesRegex(ValueError,'OHLCV'):
            admitted_frame(rows, 'M15')


if __name__ == '__main__': unittest.main()
