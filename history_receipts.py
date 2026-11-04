"""As-of view of trusted local ClockLedger captures, not arbitrary CSV authority."""
from copy import deepcopy
from live_history_admission import assess
from mt5_clock_authority import candidate, epoch, utc, digest


def select_history(records, cutoff, mode='current_analysis'):
    if mode not in {'current_analysis', 'historical_replay', 'shadow'}:
        raise ValueError('MODE_REQUIRED')
    limit = epoch(cutoff)
    first, active, history = {}, {}, []
    latest_receipt = None
    quarantined=[]
    if records and mode=='current_analysis':
        candidate(records[-1][1])  # never reuse an old good capture after a bad latest one
    for capture_id, capture, _ in records:
        try:
            authority = candidate(capture)
        except ValueError as exc:
            if mode!='current_analysis':raise
            quarantined.append({'capture_id':capture_id,'reason':str(exc)})
            continue  # retain raw evidence; a later good current snapshot must supersede it
        # The complete proof includes external-after measurements, not only shutdown.
        receipt = max(epoch(capture['shutdown_end_utc']), *[epoch(x['end_utc']) for x in capture['external_after']]) + authority['hi']
        if receipt > limit:
            continue
        report = assess(capture, decision_at_utc=utc(receipt), mode='historical_replay')
        latest_receipt = receipt
        for row in report['rows']:
            row = deepcopy(row)
            key = (capture['source'], capture['symbol'], row['timeframe'], row['raw_timestamp'])
            version = (*key, row['content_sha256'])
            first.setdefault(version, utc(receipt))
            previous = active.get(key)
            row.update(source=capture['source'], symbol=capture['symbol'], capture_id=capture_id,
                       first_observed_at_utc=first[version], HISTORICALLY_AVAILABLE_AT=first[version],
                       closure_proven_at_utc=utc(receipt), receipt_available_at_utc=utc(receipt),
                       authority_expires_at_utc=authority['expires_at_utc'],
                       evidence_id=digest(list(version)),
                       supersedes=previous['evidence_id'] if previous and previous['evidence_id'] != digest(list(version)) else None,
                       historical_availability_semantics='Earliest verified receipt of this exact content; closure proof may be later')
            # A correction, even unclosed/unproven, replaces the old bar in the as-of view.
            active[key] = row
            history.append(deepcopy(row))
    rows = list(active.values())
    for row in rows:
        fresh = latest_receipt is not None and limit <= epoch(row['authority_expires_at_utc']) and row['receipt_available_at_utc'] == utc(latest_receipt)
        row['DATA_AVAILABLE_NOW'] = fresh
        row['price_order_only_admitted'] = row['BAR_CLOSED'] and (mode != 'current_analysis' or fresh)
        if not row['price_order_only_admitted']:
            row['reasons'] = row['reasons'] + ['NO_CURRENT_CLOSED_VERSION_AT_CUTOFF']
    return dict(mode=mode, decision_at_utc=cutoff, rows=rows, version_history=history, quarantined=quarantined,
                utc_mapping='UNKNOWN', trade_authorized=False)
