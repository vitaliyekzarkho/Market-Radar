"""Quarantine source time claims; no broker-offset inference or clock authority."""
from datetime import datetime,timezone,timedelta
SECONDS={'M15':900,'H1':3600,'H4':14400,'D1':86400,'W1':604800}

def inspect_times(rows,timeframe,received_at_utc):
    received=datetime.fromisoformat(received_at_utc.replace('Z','+00:00'))
    if received.utcoffset()!=timedelta(0):raise ValueError('UTC_RECEIPT_REQUIRED')
    result=[]
    for row in rows:
        epoch=int(row['time']);candidate=datetime.fromtimestamp(epoch,timezone.utc)
        close=candidate+timedelta(seconds=SECONDS[timeframe])
        reasons=['SOURCE_TO_UTC_AUTHORITY_UNVERIFIED','LOCAL_RECEIPT_CLOCK_UNVERIFIED']
        if candidate>received:reasons.append('CANDIDATE_OPEN_AFTER_RECEIPT')
        if close>received:reasons.append('CANDIDATE_CLOSE_AFTER_RECEIPT')
        result.append(dict(raw_mt5_epoch=epoch,candidate_utc_open=candidate.isoformat(),
            candidate_nominal_close=close.isoformat(),received_at_utc=received.isoformat(),
            time_status='TIME_UNVERIFIED',is_closed=None,historical_confirmation_allowed=False,
            reasons=reasons,close_semantics='nominal duration only; not proof of broker session closure'))
    return result

def require_admitted_capture(reports):
    # No authority-admission API exists in this repair. A past-looking bar is
    # not proof of its time scale; a caller cannot bypass this by a boolean.
    if not reports or any(r['time_status']!='TIME_VERIFIED' for rows in reports.values() for r in rows):
        raise RuntimeError('TIME_UNVERIFIED: receipt retained; raw cache not promoted; historical confirmations blocked')
