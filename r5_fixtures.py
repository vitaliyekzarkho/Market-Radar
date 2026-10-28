"""Artificial OHLCV for contract tests only; not genuine historical market data."""
import csv,io,math
from datetime import datetime,timedelta,timezone
from r5_input import TF_SECONDS

def candles(timeframe,*,count=120,scale=4100,missing_volume=False):
    end=datetime(2026,10,20,12,tzinfo=timezone.utc);step=timedelta(seconds=TF_SECONDS[timeframe])
    stream=io.StringIO(newline='');fields=['utc_time','broker_time','time','open','high','low','close','tick_volume','is_closed']
    if missing_volume:fields.remove('tick_volume')
    w=csv.DictWriter(stream,fieldnames=fields);w.writeheader()
    for i in range(count):
        middle=scale*(1+0.008*math.sin(i/4)-0.00008*i)
        o=middle-scale*.0003;c=middle+scale*.0003*math.sin(i)
        row=dict(utc_time=(end-step*(count-i)).isoformat(),broker_time=(end-step*(count-i)).isoformat(),time=int((end-step*(count-i)).timestamp()),open=o,high=max(o,c)+scale*.0005,
                 low=min(o,c)-scale*.0005,close=c,is_closed='true')
        if not missing_volume:row['tick_volume']=100+i
        w.writerow(row)
    return stream.getvalue().encode()

def rejection_candles(timeframe):
    """Controlled TEST OHLC, not prebuilt FVG/PA objects or historic evidence.

    D1 has a downward three-candle gap followed by a wick retest/close below.
    Only the unchanged legacy algorithms decide what structures this implies.
    """
    b=candles(timeframe,count=40)
    if timeframe!='D1':return b
    rows=list(csv.DictReader(io.StringIO(b.decode())))
    prices=[(4215,4220,4205,4210),(4205,4210,4160,4170),(4165,4175,4150,4160),
            (4160,4185,4145,4160),(4160,4170,4130,4140),(4140,4150,4100,4110)]
    for row,values in zip(rows[-6:],prices):
        row.update(dict(zip(('open','high','low','close'),values)))
    s=io.StringIO(newline='');w=csv.DictWriter(s,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    return s.getvalue().encode()
