"""Local, append-only, bounded observation authority. No MT5/network imports.

Only trusted local acquisition is an authority source. Hashes bind contents;
they do not authenticate a third-party JSON supplied as a purported capture.
"""
import hashlib,json,math,sqlite3,statistics
from datetime import datetime,timezone
from pathlib import Path

VERSION='recorded-clock/1'
TTL=60
MAX_GAP=60
PERIODS={'M15':900,'H1':3600,'H4':14400,'D1':86400,'W1':604800}
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(x):return hashlib.sha256(canonical(x).encode()).hexdigest()
def epoch(s):
    d=datetime.fromisoformat(s.replace('Z','+00:00'))
    if d.utcoffset() is None or d.utcoffset().total_seconds()!=0:raise ValueError('UTC_REQUIRED')
    return d.timestamp()
def utc(t):return datetime.fromtimestamp(t,timezone.utc).isoformat()

def bounds(items):
    expected={'time.cloudflare.com':'NTP_UDP_UNAUTHENTICATED','time.google.com':'NTP_UDP_UNAUTHENTICATED','https://www.cloudflare.com/cdn-cgi/trace':'HTTPS_DATE_TLS','https://www.google.com/generate_204':'HTTPS_DATE_TLS'}
    if len(items)!=4 or {i['source'] for i in items}!=set(expected):raise ValueError('INDEPENDENT_CLOCK_SOURCES_REQUIRED')
    ranges=[]
    for i in items:
        if i['method']!=expected[i['source']] or i['status']!='OK':raise ValueError('CLOCK_SOURCE_FAILED')
        if not 0<=i['elapsed_seconds']<=2 or abs(i['local_clock_step_seconds'])>.05:raise ValueError('CLOCK_LATENCY_OR_STEP')
        if epoch(i['end_utc'])<epoch(i['start_utc']):raise ValueError('CLOCK_ORDER')
        if i['method']=='NTP_UDP_UNAUTHENTICATED':
            if i['stratum'] not in range(1,16):raise ValueError('NTP_UNSYNCHRONIZED')
            r=i['conservative_offset_interval_seconds']
        else:
            if i.get('age_header') not in (None,'0'):raise ValueError('CACHED_CLOCK')
            r=i['offset_interval_seconds']
        if len(r)!=2 or not all(math.isfinite(v) for v in r) or r[0]>r[1]:raise ValueError('CLOCK_BOUNDS_INVALID')
        ranges.append(r)
    if max(r[0] for r in ranges)>min(r[1] for r in ranges):raise ValueError('CLOCK_DISAGREEMENT')
    lo=min(r[0] for r in ranges);hi=max(r[1] for r in ranges)
    if hi-lo>3:raise ValueError('CLOCK_UNCERTAINTY')
    return lo,hi

def candidate(c):
    """Fresh ticks give a candidate; a candle rollover must independently bind it."""
    if c.get('symbol')!='GOLD.pro' or c.get('source')!='OANDA_MT5_LOCAL':raise ValueError('SOURCE_IDENTITY_REQUIRED')
    b=bounds(c['external_before']);a=bounds(c['external_after'])
    if max(a[0],b[0])>min(a[1],b[1]):raise ValueError('CLOCK_CHANGED')
    lo=min(a[0],b[0]);hi=max(a[1],b[1])
    start=epoch(c['initialize_start_utc']);end=epoch(c['shutdown_end_utc'])
    if not c.get('initialize_success') or not 0<end-start<=60:raise ValueError('CAPTURE_WINDOW_INVALID')
    if max(epoch(i['end_utc']) for i in c['external_before'])>start or min(epoch(i['start_utc']) for i in c['external_after'])<end:raise ValueError('CLOCK_BRACKETING_REQUIRED')
    if start-min(epoch(i['start_utc']) for i in c['external_before'])>60 or max(epoch(i['end_utc']) for i in c['external_after'])-end>60:raise ValueError('CLOCK_PROOF_TOO_OLD')
    ticks=[];last=start
    for sample in c['samples']:
        for m in sample['measurements']:
            t0=epoch(m['start_utc']);t1=epoch(m['end_utc'])
            if not start<=t0<=t1<=end or t0<last:raise ValueError('MEASUREMENT_ORDER')
            last=t0
            if m['kind']=='tick' and m.get('value'):
                v=m['value']
                if v['time']!=v['time_msc']//1000:raise ValueError('TICK_EPOCH_INCONSISTENT')
                ticks.append((v['time_msc']/1000,t0,t1))
    if len({t[0] for t in ticks})<3:raise ValueError('FRESH_TICKS_REQUIRED')
    if any(x[0]>y[0] for x,y in zip(ticks,ticks[1:])):raise ValueError('TICK_REVERSED')
    offset=round(statistics.median(t-(x+y)/2 for t,x,y in ticks)/60)*60
    if abs(offset)>14*3600:raise ValueError('OFFSET_RANGE')
    if any(t-offset>y+hi or t-offset<x+lo-5 for t,x,y in ticks):raise ValueError('TICK_FRESHNESS_OR_OFFSET')
    return dict(offset_seconds=offset,window_start_utc=utc(start+lo),window_end_utc=utc(end+hi),expires_at_utc=utc(end+hi+TTL),lo=lo,hi=hi,status='CANDIDATE_NOT_AUTHORITY')

class ClockLedger:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path)
        self.db.execute('CREATE TABLE IF NOT EXISTS captures (seq INTEGER PRIMARY KEY, id TEXT UNIQUE, body TEXT, result TEXT, previous TEXT, hash TEXT)')
        self.db.commit()
    def close(self):self.db.close()
    def records(self):
        out=[];previous='0'*64
        for seq,key,body,result,prev,h in self.db.execute('SELECT * FROM captures ORDER BY seq'):
            if seq!=len(out)+1 or prev!=previous or digest([seq,key,body,result,prev])!=h:raise ValueError('TIME_LEDGER_INTEGRITY')
            out.append((key,json.loads(body),json.loads(result)));previous=h
        return out
    def ingest(self,key,c):
        raw=canonical(c)
        with self.db:
            records=self.records()
            for old,body,result in records:
                if old==key:
                    if canonical(body)!=raw:raise ValueError('CAPTURE_ID_CONFLICT')
                    return result
            if records and epoch(c['initialize_start_utc'])<epoch(records[-1][1]['shutdown_end_utc']):raise ValueError('OUT_OF_ORDER_CAPTURE')
            r=dict(contract=VERSION,capture_id=key,capture_sha256=digest(c),status='TIME_UNVERIFIED',authority=None,closures=[],openings=[],reasons=[],trade_authorized=False)
            try:auth=candidate(c)
            except (ValueError,KeyError,TypeError) as exc:r['reasons'].append(str(exc));auth=None
            if auth:
                segment=digest([key,auth['offset_seconds']]);prior=None
                if records:
                    _,pc,pr=records[-1];pa=pr.get('authority')
                    if pa and pa['offset_seconds']==auth['offset_seconds'] and 0<=epoch(c['initialize_start_utc'])-epoch(pc['shutdown_end_utc'])<=MAX_GAP and epoch(c['initialize_start_utc'])<=epoch(pa['expires_at_utc']):
                        prior=(pc,pr);segment=pa['segment']
                    else:r['reasons'].append('PREVIOUS_AUTHORITY_EXPIRED_CHANGED_OR_UNVERIFIED')
                auth['segment']=segment;r['authority']=auth
                histories={tf:[] for tf in PERIODS}
                if prior:
                    pc,pr=prior
                    for m in pc['samples'][-1]['measurements']:
                        if m['kind']=='bars':histories[m['timeframe']].append((m,pr['authority'],pr['capture_sha256']))
                for s in c['samples']:
                    for m in s['measurements']:
                        if m['kind']=='bars':histories[m['timeframe']].append((m,auth,r['capture_sha256']))
                openings=[o for _,_,pr in records for o in pr['openings'] if o['segment']==segment]
                for tf,items in histories.items():
                    for (old,oa,oh),(new,na,nh) in zip(items,items[1:]):
                        if not old['rows'] or not new['rows']:continue
                        xs=[v['time'] for v in new['rows']]
                        if xs!=sorted(set(xs)):raise ValueError('UNORDERED_BARS')
                        x=old['rows'][-1]['time'];y=new['rows'][-1]['time']
                        if y==x:continue
                        closed=[v for v in new['rows'][:-1] if v['time']==x]
                        lower=epoch(old['start_utc'])+oa['lo'];upper=epoch(new['end_utc'])+na['hi']
                        t=y-auth['offset_seconds']
                        if y-x!=PERIODS[tf] or len(closed)!=1 or upper-lower>MAX_GAP+3 or not lower<=t<=upper:
                            r['reasons'].append('UNPROVEN_'+tf+'_ROLLOVER');continue
                        opening=next((o for o in openings+r['openings'] if o['timeframe']==tf and o['raw_epoch']==x),None)
                        cert=dict(timeframe=tf,raw_epoch=x,raw_bar=closed[0],raw_bar_sha256=digest(closed[0]),verified_open_utc=opening['verified_open_utc'] if opening else None,verified_close_utc=utc(t),available_at_utc=utc(upper),close_interval_utc=[utc(lower),utc(upper)],source_authority=dict(segment=segment,offset_seconds=auth['offset_seconds'],capture_sha256=[oh,nh],scope='observed transition only'),time_status='TIME_VERIFIED',closure_status='OBSERVED_CLOSED')
                        r['closures'].append(cert)
                        r['openings'].append(dict(segment=segment,timeframe=tf,raw_epoch=y,verified_open_utc=utc(t),capture_sha256=nh))
                if r['closures']:auth['status']='OBSERVED_ROLLOVER_AUTHORITY';r['status']='TIME_VERIFIED'
                else:r['reasons'].append('NO_OBSERVED_ROLLOVER_IN_WINDOW')
            seq=len(records)+1;previous=self.db.execute('SELECT hash FROM captures ORDER BY seq DESC LIMIT 1').fetchone();previous=previous[0] if previous else '0'*64
            result=canonical(r);h=digest([seq,key,raw,result,previous])
            self.db.execute('INSERT INTO captures VALUES (?,?,?,?,?,?)',(seq,key,raw,result,previous,h))
            return r
    def lineage(self,key,decision_at_utc):
        cutoff=epoch(decision_at_utc);records=self.records();chosen=next((v for v in records if v[0]==key),None)
        if chosen is None:raise ValueError('CAPTURE_UNKNOWN')
        _,c,r=chosen
        # Later captures must never confer authority on an earlier decision.
        certificates=[cert for _,_,v in records for cert in v['closures'] if epoch(cert['available_at_utc'])<=cutoff]
        result=[]
        for m in c['samples'][-1]['measurements']:
            if m['kind']!='bars':continue
            for raw in m['rows']:
                cert=next((v for v in certificates if v['timeframe']==m['timeframe'] and v['raw_bar']==raw),None)
                row=dict(timeframe=m['timeframe'],raw_timestamp=raw['time'],raw_ohlcv=raw,raw_sha256=digest(raw),capture_sha256=r['capture_sha256'],capture_timestamp=m['end_utc'],verified_utc_timestamp=None,verified_close_utc=None,available_at_utc=None,source_authority=None,time_status='TIME_UNVERIFIED',closure_status='CURRENT_OR_FUTURE' if raw==m['rows'][-1] else 'UNVERIFIED',availability_status='UNKNOWN',historical_confirmation_allowed=False)
                if cert:
                    row.update(verified_utc_timestamp=cert['verified_open_utc'],verified_close_utc=cert['verified_close_utc'],available_at_utc=cert['available_at_utc'],source_authority=cert['source_authority'],time_status='TIME_VERIFIED',closure_status='OBSERVED_CLOSED',availability_status='AVAILABLE_AT')
                result.append(row)
        auth=r['authority'];state='UNVERIFIED' if not auth else ('EXPIRED' if cutoff>epoch(auth['expires_at_utc']) else auth['status'])
        return dict(contract=VERSION,capture_id=key,capture_sha256=r['capture_sha256'],decision_at_utc=decision_at_utc,authority_state=state,rows=result,status='DEFERRED',reasons=['REQUIRED_HISTORY_NOT_FULLY_VERIFIED','NO_CAUSAL_BACKTEST_OR_SHADOW_AUTHORIZATION'],trade_authorized=False,notification_eligible=False)
