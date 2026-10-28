import unittest,csv,io
from r5_fixtures import candles
from r5_input import normalize_csv,legacy_csv
from recorded_authority import Rejected

class InputTests(unittest.TestCase):
    def view(self,b,**kw):
        args=dict(symbol='XAUUSD',timeframe='H1',cutoff='2026-10-20T12:00:00Z');args.update(kw);return normalize_csv(b,**args)
    def test_ohlcv_preserved(self):
        b=candles('H1');v=self.view(b);original=list(csv.DictReader(io.StringIO(b.decode())))
        self.assertEqual([r['original'] for r in v['rows']],original)
        self.assertEqual(list(csv.DictReader(io.StringIO(legacy_csv(v).decode()))),original)
    def test_multiple_symbols_timeframes(self):
        for sym in ('XAUUSD','EURUSD','GBPJPY'):
            for tf in ('H1','M15','D1'):
                v=self.view(candles(tf),symbol=sym,timeframe=tf);self.assertEqual(len(v['rows']),120);self.assertEqual(v['symbol'],sym)
    def test_missing_volume_not_zero(self):
        v=self.view(candles('H1',missing_volume=True));self.assertIsNone(v['rows'][0]['tick_volume']);self.assertEqual(len(v['missing_tick_volume_rows']),120)
    def test_future_removed(self):
        v=self.view(candles('H1'),cutoff='2026-10-20T10:00:00Z');self.assertEqual(len(v['rows']),118)
        self.assertEqual(v['availability'],'UNKNOWN_UNLESS_SEPARATELY_ATTESTED')
    def test_duplicates_idempotent(self):
        b=candles('H1');lines=b.splitlines();v=self.view(b+lines[-1]+b'\n');self.assertEqual(v['duplicates'],1);self.assertEqual(len(v['rows']),120)
    def test_reversed_order(self):
        lines=candles('H1').splitlines()
        with self.assertRaises(Rejected):self.view(b'\n'.join([lines[0],*reversed(lines[1:])]))
    def test_gaps_not_filled(self):
        lines=candles('H1').splitlines();v=self.view(b'\n'.join(lines[:8]+lines[9:]));self.assertEqual(len(v['rows']),119);self.assertEqual(len(v['gaps']),1)
    def test_bad_values(self):
        text=candles('H1').decode();lines=text.splitlines();parts=lines[1].split(',');parts[lines[0].split(',').index('high')]='nan';lines[1]=','.join(parts)
        with self.assertRaises(Rejected):self.view('\n'.join(lines).encode())
    def test_naive_timestamp(self):
        with self.assertRaises(Rejected):self.view(candles('H1').replace(b'+00:00',b''))
    def test_numeric_closed_flag_canonicalized(self):
        b=candles('H1').replace(b',true',b',1');v=self.view(b)
        self.assertEqual(v['rows'][0]['original']['is_closed'],'1')
        self.assertEqual(list(csv.DictReader(io.StringIO(legacy_csv(v).decode())))[0]['is_closed'],'true')
    def test_conflicting_duplicate(self):
        lines=candles('H1').splitlines();row=lines[-1].split(b',');row[-2]=b'9999'
        with self.assertRaises(Rejected):self.view(b'\n'.join(lines+[b','.join(row)]))
    def test_zero_volume_is_not_missing(self):
        lines=candles('H1').splitlines();cols=lines[0].split(b',');i=cols.index(b'tick_volume')
        for n in range(1,len(lines)):
            r=lines[n].split(b',');r[i]=b'0';lines[n]=b','.join(r)
        v=self.view(b'\n'.join(lines));self.assertEqual(v['missing_tick_volume_rows'],[]);self.assertEqual(v['rows'][0]['tick_volume'],0)
