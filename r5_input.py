"""Lossless raw CSV retention with explicit, conservative closed-bar filtering."""
import csv,io,math,re
from datetime import datetime,timedelta,timezone
from recorded_authority import Rejected,sha

TF_SECONDS={'M5':300,'M15':900,'H1':3600,'H4':14400,'D1':86400,'W1':604800}

def time_utc(value):
    try:
        d=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if d.tzinfo is None:raise ValueError('timezone missing')
        return d.astimezone(timezone.utc)
    except (TypeError,ValueError) as e:raise Rejected('EXPLICIT_UTC_TIME_REQUIRED') from e

def normalize_csv(raw,*,symbol,timeframe,cutoff):
    if not isinstance(raw,bytes) or len(raw)>8_000_000:raise Rejected('CSV_SIZE_INVALID')
    if not isinstance(symbol,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,40}',symbol):raise Rejected('SYMBOL_INVALID')
    if timeframe not in TF_SECONDS:raise Rejected('TIMEFRAME_UNSUPPORTED')
    cut=time_utc(cutoff)
    try:
        reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
        header=reader.fieldnames
        if not header or len(set(header))!=len(header) or not {'utc_time','open','high','low','close','is_closed'}<=set(header):
            raise Rejected('CSV_COLUMNS_INVALID')
        rows=list(reader)
    except (UnicodeError,csv.Error) as e:raise Rejected('CSV_INVALID') from e
    selected=[];seen={};last=None;gaps=[];duplicates=0;excluded=0;missing=[]
    for i,row in enumerate(rows):
        if None in row or any(v is None for v in row.values()):raise Rejected('CSV_ROW_WIDTH')
        if 'symbol' in row and row['symbol']!=symbol:raise Rejected('SYMBOL_CONFLICT')
        if 'timeframe' in row and row['timeframe']!=timeframe:raise Rejected('TIMEFRAME_CONFLICT')
        at=time_utc(row['utc_time']);stamp=at.isoformat()
        if 'broker_time' in row:time_utc(row['broker_time'])
        if 'time' in row:
            try:epoch=float(row['time'])
            except ValueError as e:raise Rejected('SOURCE_EPOCH_INVALID') from e
            if not math.isfinite(epoch):raise Rejected('SOURCE_EPOCH_INVALID')
        if stamp in seen:
            if seen[stamp]!=row:raise Rejected('CONFLICTING_BAR_DUPLICATE')
            duplicates+=1;continue
        if last is not None and at<last:raise Rejected('BAR_ORDER_INVALID')
        seen[stamp]=row
        if last is not None and (at-last).total_seconds()>TF_SECONDS[timeframe]:
            gaps.append({'after':last.isoformat(),'before':at.isoformat(),'reason':'GAP_OR_MARKET_CLOSURE_NOT_FILLED'})
        last=at;values={}
        for k in ('open','high','low','close'):
            try:values[k]=float(row[k])
            except ValueError as e:raise Rejected('OHLC_INVALID') from e
            if not math.isfinite(values[k]) or values[k]<=0:raise Rejected('OHLC_INVALID')
        if values['low']>min(values['open'],values['close']) or values['high']<max(values['open'],values['close']) or values['low']>values['high']:
            raise Rejected('OHLC_GEOMETRY_INVALID')
        for k in ('tick_volume','real_volume'):
            if k not in row or row[k]=='':values[k]=None
            else:
                try:values[k]=float(row[k])
                except ValueError as e:raise Rejected('VOLUME_INVALID') from e
                if not math.isfinite(values[k]) or values[k]<0:raise Rejected('VOLUME_INVALID')
        if row['is_closed'].lower() not in {'true','false','1','0'}:raise Rejected('CLOSED_FLAG_INVALID')
        closed=row['is_closed'].lower() in {'true','1'}
        # An explicit source close is preserved; otherwise use the conservative
        # nominal UTC interval end solely for market-data filtering, not authority.
        end=time_utc(row['bar_closed_at_utc']) if row.get('bar_closed_at_utc') else at+timedelta(seconds=TF_SECONDS[timeframe])
        if end<at:raise Rejected('BAR_CLOSE_BEFORE_OPEN')
        if not closed or end>cut:excluded+=1;continue
        if values['tick_volume'] is None:missing.append(i)
        selected.append({'source_row':i,'original':dict(row),'utc_time':at.isoformat(),'bar_closed_at_utc':end.isoformat(),**values})
    return {'symbol':symbol,'timeframe':timeframe,'source_sha256':sha(raw),'header':header,'rows':selected,
            'duplicates':duplicates,'excluded_open_or_future':excluded,'gaps':gaps,'missing_tick_volume_rows':missing,
            'availability':'UNKNOWN_UNLESS_SEPARATELY_ATTESTED','transformations':['UTC parsed for filtering; original strings retained',
            'OHLCV float view for legacy calculations; exact CSV bytes remain authoritative',
            'Closed flag canonicalized to true only in staged legacy CSV',
            'No sorting, gap filling or missing-volume substitution']}

def legacy_csv(view):
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=view['header']);writer.writeheader()
    for r in view['rows']:writer.writerow(dict(r['original'],is_closed='true'))
    return stream.getvalue().encode('utf-8')
