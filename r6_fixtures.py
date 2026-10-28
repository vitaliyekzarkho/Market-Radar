"""Artificial MT5 capability double. No broker data or native imports."""
import csv,io
from types import SimpleNamespace
from r5_fixtures import rejection_candles
from r5_input import TF_SECONDS

class FakeMT5:
    def __init__(self,fail=None):
        self.calls=[];self.fail=fail
        for tf in TF_SECONDS:setattr(self,'TIMEFRAME_'+tf,tf)
    def initialize(self):
        self.calls.append(('initialize',))
        return self.fail!='connect'
    def shutdown(self):self.calls.append(('shutdown',))
    def symbol_info(self,symbol):
        self.calls.append(('symbol_info',symbol))
        if self.fail=='symbol':return None
        return SimpleNamespace(visible=True,digits=2,point=0.01)
    def copy_rates_from_pos(self,symbol,tf,start,count):
        self.calls.append(('copy_rates_from_pos',symbol,tf,start,count))
        if self.fail=='empty':return []
        if self.fail=='exception':raise RuntimeError('private provider detail must not escape')
        rows=[]
        for r in csv.DictReader(io.StringIO(rejection_candles(tf).decode())):
            rows.append(dict(time=int(r['time']),**{k:float(r[k]) for k in ('open','high','low','close')},
                tick_volume=int(r['tick_volume']),spread=2,real_volume=0))
        return rows[-count:]
    def __getattr__(self,name):raise AssertionError('FORBIDDEN_API:'+name)

class Clock:
    def __init__(self):self.n=0
    def __call__(self):
        self.n+=1
        return dict(at_utc=f'2026-10-20T12:00:00.{self.n:06d}Z',monotonic_ns=self.n,
                    source='TEST_SYSTEM_CLOCK',uncertainty_seconds=None)
