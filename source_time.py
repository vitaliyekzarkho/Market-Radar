"""Explicit source-clock labels. Never masquerade as UTC datetimes."""
from dataclasses import dataclass
import math
import pandas as pd
from functools import total_ordering


@total_ordering
@dataclass(frozen=True, eq=False)
class SourceTime:
    source: str
    symbol: str
    timeframe: str
    epoch: int

    def reference(self):
        return f"source-time:{self.source}:{self.symbol}:{self.timeframe}:{self.epoch}"

    def __eq__(self, other):
        return isinstance(other, SourceTime) and (self.source, self.symbol, self.epoch) == (other.source, other.symbol, other.epoch)

    def __hash__(self):
        return hash((self.source, self.symbol, self.epoch))

    def __lt__(self, other):
        if not isinstance(other, SourceTime) or (self.source, self.symbol) != (other.source, other.symbol):
            raise ValueError('INCOMPATIBLE_TIME_DOMAINS')
        return self.epoch < other.epoch


def parse_source_time(value):
    if not isinstance(value, str) or not value.startswith('source-time:'):
        return None
    _, source, symbol, tf, raw = value.split(':')
    if source != 'OANDA_MT5_LOCAL' or symbol != 'GOLD.pro' or tf not in {'W1','D1','H4','H1','M15'}:
        raise ValueError('SOURCE_TIME_IDENTITY')
    return SourceTime(source, symbol, tf, int(raw))


def time_label(value):
    return value.reference() if isinstance(value, SourceTime) else value.isoformat()


def bar_time(row, column="utc_time"):
    if "source_time" in row:
        value = row["source_time"]
        if not isinstance(value, SourceTime):
            raise ValueError("TYPED_SOURCE_TIME_REQUIRED")
        return value.reference()
    value = row[column]
    return value.isoformat()


def admitted_frame(rows, timeframe):
    selected = [r for r in rows if r['timeframe'] == timeframe and r['price_order_only_admitted']]
    selected.sort(key=lambda r: r['raw_timestamp'])
    if len({r['raw_timestamp'] for r in selected}) != len(selected):
        raise ValueError('DUPLICATE_ACTIVE_BAR_VERSION')
    data = []
    for r in selected:
        bar = dict(r['raw_ohlcv'])
        for field in ('open', 'high', 'low', 'close', 'tick_volume'):
            if field not in bar or not math.isfinite(float(bar[field])):
                raise ValueError('INVALID_OHLCV')
        if not bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high'] or bar['tick_volume'] < 0:
            raise ValueError('INVALID_OHLCV_RANGE')
        bar['source_time'] = SourceTime('OANDA_MT5_LOCAL', 'GOLD.pro', timeframe, r['raw_timestamp'])
        bar['is_closed'] = True
        data.append(bar)
    frame = pd.DataFrame(data)
    frame.attrs['time_basis'] = 'SOURCE_ORDER_ONLY'
    return frame
