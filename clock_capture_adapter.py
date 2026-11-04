"""Explicit acquisition procedure; imported only by snapshot entry point.
No account/transport/trade API. Offline tests inject providers.
"""
import time
from datetime import datetime,timezone

def utc():return datetime.fromtimestamp(time.time(),timezone.utc).isoformat()
def collect(mt5,frames,probe,*,count=500,max_wait_seconds=15.0,poll_seconds=.25):
    if not 0 < max_wait_seconds <= 30 or not .1 <= poll_seconds <= 2:
        raise ValueError('CAPTURE_WAIT_BOUNDS_INVALID')
    before=probe();start=utc();samples=[];polls=[]
    deadline=time.monotonic()+max_wait_seconds
    seen=set()
    # Deadline and attempt cap both apply; no unbounded retries on a frozen feed.
    for _ in range(int(max_wait_seconds/poll_seconds)+1):
        if time.monotonic() >= deadline:break
        measurements=[];t=utc();tick=mt5.symbol_info_tick('GOLD.pro');end=utc()
        value=None if tick is None else {k:getattr(tick,k) for k in ('time','time_msc')}
        polls.append(dict(start_utc=t,end_utc=end,value=value))
        if tick is not None and seen and tick.time_msc < max(seen):
            raise ValueError('TICK_REVERSED')
        if tick is None or tick.time_msc in seen:
            time.sleep(min(poll_seconds,max(0,deadline-time.monotonic())))
            continue
        seen.add(tick.time_msc)
        measurements.append(dict(kind='tick',start_utc=t,end_utc=end,value=value))
        for name,tf in frames.items():
            t=utc();rates=mt5.copy_rates_from_pos('GOLD.pro',tf,0,count);end=utc()
            rows=[] if rates is None else [{k:r[k].item() for k in rates.dtype.names} for r in rates]
            measurements.append(dict(kind='bars',timeframe=name,start_utc=t,end_utc=end,requested_start_pos=0,rows=rows))
        samples.append(dict(measurements=measurements))
        if len(samples)==3:break
        time.sleep(min(poll_seconds,max(0,deadline-time.monotonic())))
    end=utc();after=probe()
    return dict(symbol='GOLD.pro',source='OANDA_MT5_LOCAL',initialize_success=True,initialize_start_utc=start,shutdown_end_utc=end,window_semantics='measurement window; connection managed by caller',samples=samples,external_before=before,external_after=after,tick_poll_observations=polls,capture_wait_limit_seconds=max_wait_seconds)
