"""Offline evidence packaging; never executes Radar or acquisition."""
import json,hashlib,zipfile,difflib,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent/'MarketRadar_v052_Native_Repair_2026-10-27'
OUT=ROOT.parent/'MarketRadar_Time_Authority_Integration_Review_2026-10-27'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(name,text):
    p=OUT/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8')
def js(name,value):write(name,json.dumps(value,ensure_ascii=False,indent=2))
OUT.mkdir(exist_ok=True)
full=json.loads((ROOT/'r6_8_verification/full.json').read_text())
replay=json.loads((ROOT/'time_verification/recorded_replay.json').read_text())
assert full['success'] and not full['failures'] and not full['errors'] and not full['skips']
assert replay['status']=='PASS'
changed=[p.name for p in ROOT.glob('*.py') if not (BASE/p.name).exists() or sha(p)!=sha(BASE/p.name)]
contract=dict(version='recorded-clock/1',build='0.5.2-time-authority-integration-review',source='OANDA_MT5_LOCAL',symbol='GOLD.pro',trust_boundary='Trusted local recorder. Not a signed remote authority; JSON hashes bind content, not authorship.',ttl_seconds=60,max_cross_capture_gap_seconds=60,max_external_clock_bracket_gap_seconds=60,max_capture_window_seconds=60,max_clock_probe_latency_seconds=2,max_clock_step_seconds=.05,max_individual_envelope_width_seconds=3,max_tick_lag_seconds=5,min_distinct_fresh_ticks=3,
    authority_rule='Fresh tick-derived candidate, corroborated by externally bracketed bar rollover. Never infer authority from start_pos=1. Candidate alone grants no bar admission.',
    offset_rule='Derived per bounded capture, nearest minute; no global -1h. Changed/expired/missing authority starts a new segment. No past rows shifted.',
    expiry_rule='No new cross-capture inference after expiry. Previously established exact-content closure certificates remain immutable historical facts with their own availability, not permission to use current offset on history.',
    fields={'raw_timestamp':'Original integer MT5 epoch, unchanged','raw_ohlcv':'Unchanged returned source fields','verified_utc_timestamp':'Open timestamp only when that opening transition was observed in the same uninterrupted authority segment; otherwise null','verified_close_utc':'Evidence-derived next-bar rollover label, bounded by close_interval_utc; proves closed by observation, not exact exchange session end','capture_timestamp':'Windows receipt timestamp; never itself historical availability proof','available_at_utc':'Upper UTC observation bound after closed content was returned, never nominal close/replay time','source_authority':'Segment, measured offset and exact capture hashes','time_status':['TIME_VERIFIED','TIME_UNVERIFIED'],'closure_status':['OBSERVED_CLOSED','CURRENT_OR_FUTURE','UNVERIFIED'],'availability_status':['AVAILABLE_AT','UNKNOWN']},
    historical_policy='No general historical authority. Missing open/formation/input availability remains UNKNOWN. Historical confirmation, causality, backtest and Shadow Trading stay unauthorized.',
    higher_timeframes='H4/D1/W1 observations persist across cycles/restarts. Only measured rollover with matching nominal raw period can certify closure; DST-short/long periods are deferred, never forcibly corrected.',
    derived_policy='Current integration always DEFERRED for full-history reasoning; all required technical/news/USD/yield/volume and formation availability would need independent acceptance. No automatic reopening of analytics.',
    synthetic_contract='UNCHANGED; no automatic conversion',
    restart='SQLite append-only content hash chain, immutable capture ID/content binding, duplicate no-op, contradictory duplicate/out-of-order/integrity failure rejected',
    safety='No native run performed for implementation. Production unchanged. Limited pipeline never invokes causality/analytics/Telegram/trading modules.')
js('TIME_AUTHORITY_CONTRACT.json',contract)
results=dict(status='READY FOR NATIVE RETEST',scope='Controlled observation-only retest; not full operational analytics/deployment approval',full_regression=full,recorded_replay={k:v for k,v in replay.items() if k not in {'lineage','decision_journal'}},native_acquisition='NOT RUN',production_deployment='NOT RUN',notes=['First focused test invocation failed at sandbox temp SQLite creation; workspace scratch resolved it.','One added test initially failed due import after chdir; importing adapter before chdir fixed test harness.','Final full log is authoritative; earlier incomplete/failing attempts are not counted as passes.'])
js('TIME_REGRESSION_RESULTS.json',results)
js('BAR_ADMISSION_RESULTS.json',dict(lineage=replay['lineage'],counts=replay['counts'],no_lookahead=replay['lookahead_cutoff_rejected'],restart_idempotency=replay['restart_idempotency']))
js('evidence/RECORDED_REPLAY.json',replay)
for src,dst in [(ROOT/'r6_8_verification/full.log',OUT/'evidence/full.log'),(ROOT/'r6_8_verification/full.json',OUT/'evidence/full.json'),(ROOT/'verification_inputs/time_authority/native_clock_capture.json',OUT/'evidence/native_clock_capture.json')]:
    dst.parent.mkdir(exist_ok=True,parents=True);shutil.copyfile(src,dst)
for n in changed:
    p=OUT/'sources'/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,p)
patch=[]
for n in changed:
    old=(BASE/n).read_text('utf-8-sig').splitlines(keepends=True) if (BASE/n).exists() else []
    new=(ROOT/n).read_text('utf-8-sig').splitlines(keepends=True)
    patch.extend(difflib.unified_diff(old,new,fromfile='a/'+n if old else '/dev/null',tofile='b/'+n))
write('sources/TIME_INTEGRATION.diff',''.join(patch))
write('CDR_TIME_LINEAGE_REPORT.md','''# C/D/R time lineage

`save_snapshot.main(allow_deferred=True)` → `clock_capture_adapter.collect` → `time_ingestion.persist_capture` → immutable clock receipt/raw capture + `ClockLedger` → `run_radar.run_pipeline` limited branch → `journal_deferred_cycle` → existing `RecordedLedger` + `publish_journal` + `DecisionJournal`.

The future native adapter calls the existing MT5 connection helper and raw copy_rates_from_pos for GOLD.pro (start_pos=0). It captures three observations and all five frames, bracketing the batch with independent clocks. No new provider is executed in recorded replay. Existing get_rates and PA/HTF methods remain unchanged. The older strict `save_snapshot.main()` callable is retained for compatibility; the script entry point explicitly selects deferred capture.

C receives an explicit DEFERRED context; news/causal inference is skipped when historical input availability is unproved. D records a non-assessment, no technical hypothesis, no eligibility and no notification. R stores raw records and time certificates in a distinct immutable ledger, linked into all four existing operational journal stages. Accepted D1–D5 schemas and synthetic contracts were not weakened; no claim of D1 historical authority is fabricated. The ordinary observer also attaches validated time lineage when present, without upgrading its existing assessment.

Each stage embeds raw timestamp/OHLCV hashes, capture hash/time, nullable verified open UTC, observed rollover close and AVAILABLE_AT bound, source authority and status. Capture/journal repetition is idempotent. Tampered receipt/latest view/ledger fail verification. Full original technical state and notification markers remain untouched by the limited branch.

Actual replay: 25 final captured rows; two observed closures (M15/H1); 23 TIME_UNVERIFIED. All H4/D1/W1 rows unverified. Four journal stages; one capture record after repetition. A cutoff at the earlier first native run (09:35:47 UTC) admits none of these later closures.

The legacy analytical pipeline cannot currently consume arbitrary unknown-time history safely. Therefore this is a bounded evidence/non-assessment cycle, not a successful full market analysis. Return code zero for that path means observation persistence succeeded; heartbeat/journal explicitly say DEFERRED. No stale prior scenario is displayed as a fresh result. The native capture of this implementation has NOT RUN.
''')
files='\n'.join('- `'+n+'` — '+('modified' if (BASE/n).exists() else 'new') for n in changed)
write('DEPLOYMENT_DIFF.md',f'''# Deployment diff — review only

Isolated build: `{ROOT}`
Base repair: `{BASE}`
Historical accepted GitHub base: `a3c4f27f5f2f9307e7e4c645b54e55ee18668caa`; this is a local review delta, not a new Git commit.

{files}

No production copy, autostart, terminal settings, credentials or system clock changes. No Git push/merge. Existing PA/HTF scoring, direction, Point A/B and permission rules are unchanged. Existing default time validation and fail-closed raw cache promotion are unchanged; script entry-point now takes an explicit observation-only branch.

Rollback: discard or retain this isolated review folder; production was never switched. Do not copy its old deployment manifest or historical READY documents into production as fresh certification. Native retest requires separate authorization and the existing single-instance/startup preflight. At that gate run one no-Telegram cycle only; expect DEFERRED while full input authority remains incomplete. Do not enable sustained monitoring automatically.

Package contains changed/new sources, diff, exact observed fixture and evidence, not the entire predecessor build. Apply only to the stated repair base and verify manifest hashes. To replay in a fresh copy: place evidence/native_clock_capture.json at verification_inputs/time_authority/native_clock_capture.json; run replay_time_integration.py. It refuses to overwrite an existing replay output directory.
''')
write('TIME_AUTHORITY_INTEGRATION_REPORT.md',f'''# Market Radar v0.5.2 — Time Authority Integration

**READY FOR NATIVE RETEST — controlled observation-only scope.** No claim of complete historical time authority or restored full operational analysis.

## Implemented

- Bounded clock evidence integrated at the snapshot entry point; raw epochs and OHLCV retained without historical shifts. SHA-256 capture/receipt links and a persistent append-only clock ledger.
- Explicit 60-second authority lifetime and maximum cross-capture gap; actual use restricted to observed rollover bounds. Offset changes, stale/missing clock proof or excessive gaps break the authority segment.
- Current/future, observed closed, verified/unverified time and availability are separate fields. Old known closures remain facts after expiry, but expiry cannot authorize new evidence.
- H4/D1/W1 accumulation supported conservatively; unknown/DST session gaps are not filled. A next-bar appearance proves the old bar closed by then, not its exact exchange-session close time. See contract semantics.
- Existing run_radar takes a limited non-assessment path when time history is incomplete, journals capture → scenario observation → policy → notification outcome, and skips old-cache analytics, causal inference and Telegram. It preserves prior scenario and delivery state.

## Actually executed

Full offline suite: **{full['total']}/{full['total']} PASS**, failures {full['failures']}, errors {full['errors']}, skips {full['skips']}; duration {full['duration_seconds']:.3f}s. Includes the 27 new integration tests plus the predecessor suite. Exact interpreter, command, test IDs and blocked-attempt list are in TIME_REGRESSION_RESULTS.json and evidence/full.log. Guard denies network/native MT5 import and production/private configuration reads. Expected denial tests may appear in blocked-attempt list; those are not successful external actions.

Recorded replay: PASS; 25 rows, two verified observed closures, 23 unverified; four existing journal stages; identical replay adds no duplicate; earlier cutoff admits no later evidence. Guarded replay made zero attempted external calls. No new native session, full live analysis, Telegram or trading operation was performed.

Tests cover measured offset, expiry, offset change/DST, missing fresh ticks, M15/H1, higher-TF observed transitions and unknown history, restart, receipt/view tampering, journal completeness and no-lookahead. The actual saved measurement is used unchanged as input; added source labels are explicitly local adapter metadata, not newly obtained broker proof.

## Limits and review points

1. The build deliberately always defers complete time-sensitive reasoning until a separate reviewed complete-input admission exists. It is ready to retest ingestion/journaling, not to announce normal signal operation.
2. Source offset proof is empirical and local. NTP is unauthenticated; TLS Date is coarse. This is not an authenticated remote timestamp service, nor a general historical OANDA/DST rule. Receipt hashes alone do not validate externally forged source files.
3. H4/D1/W1 closure behavior is synthetically tested; no additional actual rollovers were captured. Real history remains unverified. Nominal raw spacing checks may defer DST-short/long daily/weekly intervals.
4. Exact opening UTC stays null unless its opening was observed in the same continuously renewed authority segment. No historical availability is inferred from replay, filesystem time or candle index.
5. Three brief native samples may contain fewer than three distinct ticks on a closed/quiet market; that correctly yields TIME_UNVERIFIED/DEFERRED. Repeated observations can accumulate evidence, but no background recorder was started.
6. Retained full captures and repeated journal lineage increase disk use; ledger verification currently scans retained history. Long-running storage/performance sizing is not certified by this short recorded replay. No evidence was purged.
7. Production and the source repair build were not edited. Existing D1–D5 and trading methodology remain unchanged. The archive is a review delta, not a deployment package.

## Exact final regression invocation

```powershell
$env:MARKET_RADAR_TEST_SCRATCH='C:\\Users\\User\\AppData\\Local\\Temp\\MarketRadar_Time_Integration_Tests_20261027'
& '..\\MarketRadar_v0.5.2_2026-10-24\\.venv\\Scripts\\python.exe' -B verify_release.py full
```

Working directory: `{ROOT}`.
Replay: same interpreter with `-B replay_time_integration.py`.

Initial focused invocation hit Windows sandbox temporary-folder SQLite permissions; explicit workspace scratch resolved it. One added test import failed after its temporary chdir and was corrected. Final complete regression and recorded replay are the reported acceptance evidence.
''')
manifest=dict(status='READY FOR NATIVE RETEST',scope='observation only; history incomplete',base=str(BASE),build=str(ROOT),base_source_hashes={p.name:sha(p) for p in BASE.glob('*.py')},changed_source_files=changed,files=[])
for p in sorted(OUT.rglob('*')):
    if p.is_file() and p.name not in {'MANIFEST.json'} and p.suffix!='.zip':manifest['files'].append(dict(path=p.relative_to(OUT).as_posix(),size_bytes=p.stat().st_size,sha256=sha(p)))
js('MANIFEST.json',manifest)
z=OUT/'MarketRadar_Time_Authority_Integration_2026-10-27.zip'
with zipfile.ZipFile(z,'w',zipfile.ZIP_DEFLATED) as archive:
    for item in manifest['files']:archive.write(OUT/item['path'],item['path'])
    archive.write(OUT/'MANIFEST.json','MANIFEST.json')
with zipfile.ZipFile(z) as archive:
    assert archive.testzip() is None
    for item in manifest['files']:assert hashlib.sha256(archive.read(item['path'])).hexdigest()==item['sha256']
print(json.dumps(dict(zip=str(z),sha256=sha(z),bytes=z.stat().st_size,files=len(manifest['files'])+1,changed=changed),indent=2))
