import json,hashlib,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'admission_review';BASE=ROOT.parent/'MarketRadar_Time_Integration_2026-10-27'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(n,s):(OUT/n).write_text(s,encoding='utf-8')
def js(n,x):write(n,json.dumps(x,indent=2,ensure_ascii=False))
full=json.loads((ROOT/'r6_8_verification/full.json').read_text());assert full['success']
result=json.loads((OUT/'PA_HTF_RESTORATION_RESULTS.json').read_text())
js('REGRESSION_RESULTS.json',dict(full=full,new_admission_tests=13,native_run='NOT RUN',planning_helper='Syntax and default non-executing path checked; native execution path NOT RUN',restoration_status='CHANGES REQUIRED',note='Initial full suite also passed; final rerun includes capture-hash caching optimization.'))
shutil.copyfile(ROOT/'r6_8_verification/full.log',OUT/'full_regression.log')
write('HISTORICAL_ADMISSION_REPORT.md',f'''# Restore Historical PA/HTF Admission — 28 October 2026

**CHANGES REQUIRED. Full PA/HTF is not restored; no native cycle or production change performed in this task.**

The user-requested distinction is valid: present analysis does not need proof that old bars were available at their original close. It needs reliable closure and availability now. Backtest/replay must not use a later capture at an earlier cutoff.

Implemented an isolated admission prototype, live_history_admission.py, and 13 tests. It separates BAR_CLOSED, DATA_AVAILABLE_NOW and the receipt-bound availability proof. It retains raw OHLCV/epochs and does not create UTC timestamps, scores, directions, Point A/B or trade permissions. It has no runtime connection to production.

On the actual 27 October 14:30 recorded capture, at its conservative receipt cutoff, {result['source_relative_closed']} of {result['rows']} rows have a stable successor and a fresh source-clock tip. These are admitted only for consumers of price/order, never to existing UTC/session loaders. Five current bars are excluded. All {result['rows']} rows are denied at the earlier 09:35 replay cutoff. This is a reconstruction at the original receipt, not a live analysis on 28 October.

The closure proof is source-relative, not a verified historical UTC conversion. Therefore it does not yet satisfy the user's complete verified-time-scale admission requirement. Existing historical UTC export deliberately raises instead of inventing utc_time. This distinction is the remaining implementation boundary, not a new requirement for past information availability.

## Concrete remaining blockers

- The current +3600 observation is not a server-specific historical timezone/DST rule. Official TMS material located does not establish that mapping for the history. Do not extrapolate another OANDA entity's convention.
- market_features.load_closed_bars sorts/parses utc_time and publishes structural event times. session_context filters UTC session windows. key_levels groups broker dates; order_flow_structure chooses utc_time when supplied. Broadly populating utc_time from unverified raw epochs would create false session/freshness evidence.
- The prototype is not connected to these consumers. A typed source-time path that defers UTC-dependent features, or an independently proven source-specific historical mapping, is needed before full analysis can safely resume. No replacement scoring engine was added.
- first_observed_at_utc in this prototype is the conservative first proof bound within the supplied capture, not a global earliest-seen registry. Runtime integration must preserve earliest receipt per content version using the existing capture ledger; corrections need their own first-seen proof.

## Verification

Full offline suite: {full['total']}/{full['total']}, failures {full['failures']}, errors {full['errors']}, skips {full['skips']}, {full['duration_seconds']:.3f}s, Python 3.12.10 / Windows. Exact command and IDs: REGRESSION_RESULTS.json. Network/native MT5 import and production reads guarded. The existing Decision Journal and Point A/B tests remain in the suite, but this does not claim that the new admission prototype reaches the production Decision Journal.

Existing pipeline files compare byte-for-byte to the Time Integration baseline. No PA/HTF methodology change, production write, Telegram, trade, autostart or Git operation. The new rollover helper was syntax checked and its default planning path executed only. No new transition evidence is claimed.

## Next reviewable work

1. Review the source-relative closure contract and choose the UTC consumer solution above. Do not enable the prototype as full admission.
2. Execute, only after explicit authorization, one prepared bounded observation around a whole UTC hour to observe M15 and H1 together. It will validate current convention, not all old DST periods.
3. Integrate admission and earliest version observations into existing C/D/R with explicit current-analysis versus replay modes. Add end-to-end session/freshness and correction/supersession tests before claiming READY FOR FULL NATIVE RETEST.
''')
write('MT5_TIME_CONVENTION.md','''# OANDA time convention — evidence limits

Observed local evidence on 27 October: raw ticks differed from independent UTC by approximately +3600 seconds; the 14:00 M15/H1 rollover corroborated that window. The later 14:30 observation produced only a candidate because no rollover occurred. These are bounded measurements, not historical server rules.

[MetaQuotes Python rates documentation](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesfrom_py) specifies UTC epochs. The measured instance discrepancy therefore requires explicit source-specific handling; a naive Python timezone conversion is not independent broker proof.

[Official OANDA TMS MT5 guide](https://help.oanda.com/eu/en/faqs/mt5-user-guide-eu.htm) identifies the TMS service and ties daily candles to instrument sessions, but the reviewed page does not specify the server's historical GMT/DST conversion. That session statement does not prove raw epoch timezone offsets.

[OANDA Australia sub-account guide](https://www.oanda.com/au-en/trading/sub-account-differences/) describes MT5 chart alignment to New York close. It applies to that entity/context and is not adopted for TMS. The Japan historical CSV FAQ likewise cannot establish TMS semantics. No community broker listing is used as an authority.

Europe and US DST do not necessarily change on the same date. A successful +3600 current observation cannot choose a historic Europe/Warsaw, fixed CET or New York-aligned mapping. Missing mapping remains UNKNOWN. Session gaps and daily/weekly closes must be distinguished from appearance of the next bar; next-bar appearance gives a closed-by bound, not necessarily exchange close time.
''')
write('LIVE_VS_REPLAY_CONTRACT.md','''# Live versus replay — review contract

BAR_CLOSED: source-series completion, separately evidenced. Current position alone is insufficient. The prototype requires stable identical content in successive reads, a later source bar, strictly increasing source timestamps and a tip consistent with fresh ticks. This proves relative closure under the trusted MT5 source contract; it does not prove old UTC labels.

DATA_AVAILABLE_NOW: returned exact content is available at the externally bounded receipt cutoff, and the capture remains fresh for present analysis. Does not mean it existed at its original market-event time.

HISTORICALLY_AVAILABLE_AT: this prototype records only the conservative receipt timestamp at which exact content is demonstrated to exist. Earliest availability before capture remains UNKNOWN. A replay with an earlier cutoff is denied. A later replay may use that version after its proven receipt, subject to all other dependencies and correct time semantics.

Current analysis must not demand original historical availability. It must still distinguish source order, UTC/session time, derived-feature establishment and decision time. No score is a permission. No automatic synthetic/recorded conversion.

The new function is pure and does not persist global earliest-seen history, publish scenarios or update the journal. Its first-observed field is capture-local. Existing immutable receipts remain the audit source. A production consumer must bind source identity, instrument, timeframe, raw epoch, content hash and earliest verified receipt; revisions must not reuse the prior version's availability.

Legacy UTC/session admission remains false. No field called utc_time is populated with guessed source-local labels. The package is a contract/prototype step and must not be deployed as a completed restoration.
''')
write('DEPLOYMENT_DIFF.md','''# Deployment diff

No existing pipeline source changed. New files: live_history_admission.py (pure prototype), test_live_history_admission.py, prepare_hour_rollover.py (default planning only), package_history_review.py.

Existing mt5_reader.py, save_snapshot.py, run_radar.py, C/D/R, Decision Journal and PA/HTF code remain byte-identical to the baseline. No native data folder copied into the development build. The previous observation remains in the baseline and is read only for reconstruction.

Do not deploy. Remaining integration candidates: market_features.load_closed_bars, key_levels time grouping, session_context UTC windows, order_flow_structure time labels, save_snapshot admission/export, cdr_operational provenance and journal binding. Changes must preserve existing structure/Point A/B functions and explicitly defer unverifiable session features rather than quietly relabeling source timestamps.
''')
write('ROLLOVER_OBSERVATION_RUNBOOK.md','''# Prepared M15/H1 observation — NOT EXECUTED

Default command only prints a plan:

`python -B prepare_hour_rollover.py --boundary <whole-hour-UTC-ISO> --output <new-workspace-file.json>`

The prepared example in rollover_plan.json is not scheduled and may be stale. Choose a future whole UTC hour. Start within boundary minus 20 to minus 5 seconds. After separate authorization, --execute enables the read-only path. No account/Telegram/trade APIs, existing terminal only, shared StartupGate, no production analysis or process termination. Actual native API path is not validated by this task.

The loop has a 40-second budget, exits about 8 seconds after the boundary and disconnects IPC without terminating MT5. Native calls may block: a future operator must use an external owned-worker timeout, as in the previous native supervisor, before execution. No claim of a hard wall-clock timeout is made by the loop alone.

Require independent clocks, fresh ticks, raw and unchanged OHLCV, and pre/post M15 and H1 current-bar identities. A missing transition means UNKNOWN, not success. Retain partial data and errors; no automatic retry. This measurement alone cannot certify all historical DST periods or restore full PA/HTF.
''')
for n in ('live_history_admission.py','test_live_history_admission.py','prepare_hour_rollover.py','package_history_review.py'):shutil.copyfile(ROOT/n,OUT/n)
unchanged={p.name:sha(p)==sha(BASE/p.name) for p in ROOT.glob('*.py') if (BASE/p.name).exists()};assert all(unchanged.values())
js('baseline_comparison.json',unchanged)
names=['HISTORICAL_ADMISSION_REPORT.md','MT5_TIME_CONVENTION.md','LIVE_VS_REPLAY_CONTRACT.md','PA_HTF_RESTORATION_RESULTS.json','REGRESSION_RESULTS.json','DEPLOYMENT_DIFF.md','ROLLOVER_OBSERVATION_RUNBOOK.md','rollover_plan.json','full_regression.log','baseline_comparison.json','live_history_admission.py','test_live_history_admission.py','prepare_hour_rollover.py','package_history_review.py']
capture=next((BASE/'data/receipts').glob('*/clock_capture.json'))
shutil.copyfile(capture,OUT/'recorded_native_capture.json');names.append('recorded_native_capture.json')
m=dict(status='CHANGES REQUIRED',full_pa_htf_restored=False,files=[dict(path=n,bytes=(OUT/n).stat().st_size,sha256=sha(OUT/n)) for n in names]);js('MANIFEST.json',m)
z=OUT/'MarketRadar_Historical_Admission_Review_2026-10-28.zip'
with zipfile.ZipFile(z,'w',zipfile.ZIP_DEFLATED) as a:
    for n in names+['MANIFEST.json']:a.write(OUT/n,n)
with zipfile.ZipFile(z) as a:
    assert a.testzip() is None
    for f in m['files']:assert hashlib.sha256(a.read(f['path'])).hexdigest()==f['sha256']
print(json.dumps(dict(zip=str(z),sha256=sha(z),tests=full['total'],status=m['status']),indent=2))
