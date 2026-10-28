"""Capability-limited MT5 reader. No native import, credentials or generic dispatch.

The caller owns the native module; Python is not a hostile-code security sandbox.
Binding/initialize against a real terminal requires a separate approved launch.
"""
from dataclasses import dataclass
import math,re
from r5_input import TF_SECONDS
from recorded_authority import Rejected

ALLOWLIST = ('initialize','shutdown','symbol_info','copy_rates_from_pos')
RATE_FIELDS = ('time','open','high','low','close','tick_volume','spread','real_volume')

@dataclass(frozen=True)
class Profile:
    symbol_id: str
    broker_symbol: str
    asset_class: str
    timeframes: tuple
    digits: int
    point_size: float
    session_calendar: str
    timezone: str
    enabled: bool

    def validate(self):
        for value in (self.symbol_id,self.broker_symbol):
            if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,40}',value):raise Rejected('SYMBOL_INVALID')
        if not self.enabled:raise Rejected('PROFILE_DISABLED')
        if not self.timeframes or len(set(self.timeframes))!=len(self.timeframes) or not set(self.timeframes)<=TF_SECONDS.keys():raise Rejected('TIMEFRAMES_INVALID')
        if type(self.digits)!=int or not 0<=self.digits<=10 or not math.isfinite(self.point_size) or self.point_size<=0:raise Rejected('PRICE_SCALE_INVALID')
        if self.timezone!='UTC' or not self.session_calendar or not self.asset_class:raise Rejected('PROFILE_METADATA_REQUIRED')

XAUUSD = Profile('XAUUSD','GOLD.pro','metal',('W1','D1','H4','H1','M15','M5'),2,0.01,'UNCONFIRMED_BROKER_SESSION','UTC',True)
GER40 = Profile('GER40','GER40','index',XAUUSD.timeframes,2,0.01,'UNCONFIRMED_BROKER_SESSION','UTC',False)
EURGBP = Profile('EURGBP','EURGBP','fx',XAUUSD.timeframes,5,0.00001,'UNCONFIRMED_BROKER_SESSION','UTC',False)

class ReadOnlyAdapter:
    __slots__ = ('__initialize','__shutdown','__info','__rates','__frames','__connected')
    def __init__(self,api):
        # Retain only four capabilities, never the module or an unrestricted getattr.
        self.__initialize=api.initialize;self.__shutdown=api.shutdown
        self.__info=api.symbol_info;self.__rates=api.copy_rates_from_pos
        self.__frames={t:getattr(api,'TIMEFRAME_'+t) for t in TF_SECONDS}
        self.__connected=False
    def __enter__(self):
        if self.__connected:raise Rejected('ALREADY_CONNECTED')
        try:
            if not self.__initialize():raise Rejected('CONNECTION_FAILED')
            self.__connected=True
        except Exception:
            self.__shutdown()
            raise Rejected('CONNECTION_FAILED') from None
        return self
    def __exit__(self,*args):
        self.__connected=False;self.__shutdown()
    def read(self,profile,timeframe,count=500):
        profile.validate()
        if not self.__connected:raise Rejected('NOT_CONNECTED')
        if timeframe not in profile.timeframes:raise Rejected('TIMEFRAME_NOT_ENABLED')
        if type(count)!=int or not 7<=count<=2000:raise Rejected('COUNT_OUT_OF_BOUNDS')
        try:
            info=self.__info(profile.broker_symbol)
            if info is None or not info.visible:raise Rejected('SYMBOL_NOT_VISIBLE')
            if info.digits!=profile.digits or not math.isclose(info.point,profile.point_size,rel_tol=1e-10):raise Rejected('SYMBOL_SCALE_MISMATCH')
            # Position 1 excludes the still-forming bar without wall-clock inference.
            rates=self.__rates(profile.broker_symbol,self.__frames[timeframe],1,count)
            if rates is None or len(rates)==0:raise Rejected('QUOTES_UNAVAILABLE')
            if len(rates)>count:raise Rejected('SOURCE_COUNT_INVALID')
            rows=[];last=None
            for rate in rates:
                row={k:rate[k].item() if hasattr(rate[k],'item') else rate[k] for k in RATE_FIELDS}
                if any(type(v) not in (int,float) or not math.isfinite(v) for v in row.values()):raise Rejected('NONFINITE_RATE')
                if type(row['time'])!=int or row['time']<=0 or (last is not None and row['time']<=last):raise Rejected('RATE_ORDER_INVALID')
                if any(row[k]<0 for k in ('tick_volume','spread','real_volume')):raise Rejected('VOLUME_INVALID')
                if min(row[k] for k in ('open','high','low','close'))<=0 or row['low']>min(row['open'],row['close']) or row['high']<max(row['open'],row['close']):raise Rejected('OHLC_INVALID')
                last=row['time'];rows.append(row)
            return rows
        except Rejected:raise
        except Exception:raise Rejected('READ_FAILED') from None
