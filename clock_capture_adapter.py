"""Explicit acquisition procedure; imported only by snapshot entry point.
No account/transport/trade API. Offline tests inject providers.
"""
import time
from datetime import datetime,timezone

def utc():return datetime.now(timezone.utc).isoformat()
def collect(mt5,frames,probe,*,count=500):
    before=probe();start=utc();samples=[]
    for _ in range(3):
        measurements=[];t=utc();tick=mt5.symbol_info_tick('GOLD.pro');end=utc()
        measurements.append(dict(kind='tick',start_utc=t,end_utc=end,value=None if tick is None else {k:getattr(tick,k) for k in ('time','time_msc')}))
        for name,tf in frames.items():
            t=utc();rates=mt5.copy_rates_from_pos('GOLD.pro',tf,0,count);end=utc()
            rows=[] if rates is None else [{k:r[k].item() for k in rates.dtype.names} for r in rates]
            measurements.append(dict(kind='bars',timeframe=name,start_utc=t,end_utc=end,requested_start_pos=0,rows=rows))
        samples.append(dict(measurements=measurements))
        time.sleep(.2)
    end=utc();after=probe()
    return dict(symbol='GOLD.pro',source='OANDA_MT5_LOCAL',initialize_success=True,initialize_start_utc=start,shutdown_end_utc=end,window_semantics='measurement window; connection managed by caller',samples=samples,external_before=before,external_after=after)
