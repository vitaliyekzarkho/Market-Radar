# MarketRadar handoff — D4

## D4 read-only shadow boundary — 2026-10-22

Status: **IMPLEMENTED / AWAITING INDEPENDENT REVIEW**.
Branch: `feature/d4-read-only-shadow-boundary`.
Accepted D3 main: `5a89d9a08a0339e2db7fcdcb6fc8c47e18ba1043`.
Preflight verified approved D3 `489e1a4093ffc5d4166e0044f759a772494516f6`
is an ancestor; only HANDOFF/D3 contract changed afterwards. D1/D2/D3 remain
accepted, with source and tests unchanged. D4 is developed in an isolated checkout.

New `offline_shadow_boundary.py` exposes pure assess_bytes and read-only
assess_files. The versioned synthetic envelope/candidate profile distinguishes
schema, semantic compatibility, provenance, availability and final readiness.
No accepted ledger import/write, generation, delivery, service or runtime adapter.
Exact raw input is retained privately; the public report contains fixed reasons
and fingerprints, never arbitrary input identifiers or private strings.

Added files: offline_shadow_boundary.py, test_offline_shadow_boundary.py,
tests/fixtures/d4/bundle.json, docs/D4_READ_ONLY_SHADOW_BOUNDARY_CONTRACT.md.
Only this HANDOFF changes among existing files.
See [D4 contract](D4_READ_ONLY_SHADOW_BOUNDARY_CONTRACT.md) for exact schemas,
trust boundaries, API, decision precedence, test commands and security limits.

Final tested implementation SHA and results: pending the committed regression.
The following documentation-only result commit will record the exact tested SHA.

Important limits: normalized synthetic candidate checks are not authentic ledger
membership/prefix proofs, independent provenance or historical timeliness evidence.
Source hashes are integrity checks, not authentication. D3 non-NEW lifecycle events
remain insufficient without validated history; D4 never authorizes notification.
File roots require protection against hostile concurrent replacement; this is not
an OS sandbox. SHADOW_READY means only the explicit offline profile passed.

The working Radar folder was not accessed, changed or synchronized; no Radar,
MT5, Telegram, live source or trading activity. No D5 implementation. Next action:
independent review of D4; do not merge without acceptance. Historical C3.3.6/XAUUSD
evidence remains INSUFFICIENT_EVIDENCE; no trading-performance claim is added.

---

## D3 acceptance — 2026-10-21

**D3 — ACCEPTED / CLOSED** as an offline component.
Independent review approved the correction and closed the blocking retry issue.
Approved implementation: `489e1a4093ffc5d4166e0044f759a772494516f6`.
Approved published HEAD: `82e8a97c19c5f4cdbcab4a5cfa0cfec79c569c7a`.
Verified pre-merge main: `54f92effad0515757ca8bfd9ae5263f41e5aa14d`.
Only documentation changed after the tested implementation. This acceptance commit
changes only HANDOFF and the D3 contract; implementation, fixtures and tests remain
unchanged. Main is fast-forwarded through the approved history without force-push.
The resulting acceptance SHA is reported in the final handoff (a commit cannot
embed its own SHA in its own contents).

Accepted results: D3 **60/60**, full offline regression **730/730**, no failures,
errors or skips. These are the implementer's 2026-10-21 Windows/CPython 3.12.10
results, not an independent reviewer test run. No regression is rerun for this
pure documentation acceptance.

Retry after uncertainty requires confirmed_not_accepted plus the explicit synthetic
policy and risk acknowledgment. still_unknown/provider_accepted never authorize
retry. Provider acknowledgment is not proof of recipient delivery or reading.
Synthetic clocks/authorities, unverified source authenticity, external retention
and absent production freshness/recipient/retry policies remain limitations.
No historical forecasting performance or live deployment readiness is established.

Next: separately scope and review D4 planning. No Radar startup, MT5 connection,
Telegram messages, trading operation, working-folder synchronization or D4
implementation is authorized by this acceptance.


## Current work — D3

### Retry/reconciliation review correction — 2026-10-21

Status remains **ACCEPTED / CLOSED**.
Tested corrected implementation: `489e1a4093ffc5d4166e0044f759a772494516f6`.
Fresh tests: **60/60 D3**, **730/730 full offline regression**, 0 failures,
0 errors, 0 skipped. CPython 3.12.10 64-bit (MSC v.1943 AMD64),
Windows-11-10.0.26200-SP0, existing dependencies. Implementer execution only.
The exact D3/full commands below were rerun on this SHA. The earlier 57/57 and
727/727 results apply only to the earlier implementation, not this correction.

Review finding: still_unknown previously permitted retry with duplicate-risk
acknowledgment. Now only confirmed_not_accepted can authorize retry; policy,
attempt limit and explicit risk acknowledgment remain necessary. provider_accepted
and still_unknown forbid retry at both reconciliation and attempt preparation.
Tests cover rejected authorization with complete journal/projection equality,
still_unknown persistence across restart and atomic retry rejection afterwards.

Correction changes only offline_notification_audit.py, its test module, D3 contract
and HANDOFF. Accepted D1/D2, eligibility, recipient invariants and schemas are unchanged.
The next documentation-only commit records this result; D3 offline acceptance is authorized.

### Earlier D3 implementation record

Status: **ACCEPTED / CLOSED**.
Branch: `feature/d3-offline-notification-audit`.
Accepted D2 main: `54f92effad0515757ca8bfd9ae5263f41e5aa14d`.
Part A was already completed in that separate documentation-only acceptance commit.
Verified ancestry from D1 `2465cb7532c0be3dff259d48eb22ea78a56638e5`;
only HANDOFF/D2 contract changed after approved implementation
`822c6cadcbb48e3bbbf9b41c56f4fa1c79b0efd9`. No repeated acceptance commit was needed.
Prior 14/14 lifecycle, 67/67 D2 and 670/670 full results are implementer results on
Windows/CPython 3.12.10, not independent reviewer execution.

D3 adds `offline_notification_audit.py`, `test_offline_notification_audit.py`,
`tests/fixtures/d3/policy.json` and `docs/D3_OFFLINE_NOTIFICATION_AUDIT_CONTRACT.md`.
Only this HANDOFF changes among existing tracked files on the D3 branch.
See [D3 technical contract](D3_OFFLINE_NOTIFICATION_AUDIT_CONTRACT.md).
Tested implementation SHA: `b9f49d55d31468958d3a8c1f43a0f23fcf900abb`.
2026-10-21: D3 **57/57**, full offline regression **727/727** (670 accepted + 57 D3),
0 failures, 0 errors, 0 skipped in both runs. CPython 3.12.10 64-bit
(MSC v.1943 AMD64), Windows-11-10.0.26200-SP0, existing installed dependencies.
Implementer validation, not independent reviewer execution or clean/Linux verification.
The following documentation-only commit records the result without implementation changes.

Exact commands from the isolated D3 checkout:

```powershell
python -B -c "import tempfile,unittest,platform,sys; tempfile.tempdir=r'C:\Users\User\AppData\Local\Temp\MarketRadar_D3_OfflineTests'; print(sys.version); print(platform.platform()); r=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromName('test_offline_notification_audit')); print('D3',r.testsRun,'failures',len(r.failures),'errors',len(r.errors),'skipped',len(r.skipped)); raise SystemExit(not r.wasSuccessful())"
python -B -c "import tempfile,unittest; tempfile.tempdir=r'C:\Users\User\AppData\Local\Temp\MarketRadar_D3_OfflineTests'; r=unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('.', pattern='test_*.py')); print('FULL',r.testsRun,'failures',len(r.failures),'errors',len(r.errors),'skipped',len(r.skipped)); raise SystemExit(not r.wasSuccessful())"
```

Synthetic validation includes all requested 30 categories, exact five-prefix replay,
concurrent duplicate preparation, real process exits before/after commit, separate
upstream immutability checks and fail-closed corrupted D1/D2 references. No real
message/recipient history is used. The public fixtures contain only synthetic data.

Eligibility, generation, preparation, start, provider acknowledgment/failure/unknown
and reconciliation are separate immutable events. Provider acceptance never proves
recipient receipt/read. UNKNOWN blocks retry until explicit reconciliation, policy
approval and duplicate-risk acknowledgment. There is no transport implementation.

Synthetic authorities/clocks, unverified authenticity, external payload retention,
no production freshness/recipient/retry policy and no historical forecasting evidence
remain limitations. Accepted D1/D2 source/tests and PA/HTF permissions are unchanged.
Working Radar untouched; no MT5, Telegram, trades or D4-D6 implementation.
D3 is accepted for offline use; D4 requires a separate planning task.

## Earlier accepted stages

## D2 acceptance — 2026-10-21

**D2 — ACCEPTED / CLOSED** for offline use.
Independent code review APPROVED for implementation
`822c6cadcbb48e3bbbf9b41c56f4fa1c79b0efd9` and published HEAD
`f40d3d0ed484de0766037c6734d85d5c83fbb19c`.
Verified pre-merge main: `2465cb7532c0be3dff259d48eb22ea78a56638e5`.
The acceptance commit is a direct child of the approved HEAD and changes only
this documentation and the companion D2 contract/HANDOFF. All implementation,
fixtures, accepted D1 modules and tests remain unchanged. Main is fast-forwarded
through the approved branch; no force-push.

Accepted regression: **670/670**, focused lifecycle **14/14**, D2 **67/67**,
0 failures/errors/skips, implementer's Windows/Python 3.12.10 run on 2026-10-21.
No independent Python execution or new regression is claimed at closure.
Synthetic clocks/technical authority, unverified source authenticity, external
payload retention dependency and absent production freshness policy remain.
Historical/XAUUSD remains INSUFFICIENT_EVIDENCE; no forecast timeliness,
performance or live deployment authorization is established.

Next stage: D3 offline notification/delivery audit, pending a separate scoped task.
No D3 implementation, Radar/MT5/Telegram launch or runtime folder synchronization.
The acceptance commit's SHA is reported in the final handoff; it cannot embed its
own SHA in its own contents.


## Current work — D2

### Lifecycle review correction — 2026-10-21

Status remains **ACCEPTED / CLOSED**.
Independent review requested a fix for new hypothesis IDs entering through revision
or invalidation targeting an old ID. Corrected implementation SHA:
`822c6cadcbb48e3bbbf9b41c56f4fa1c79b0efd9`.
Every subsequent snapshot compares all IDs: exactly one new ID requires its own
correctly targeted HYPOTHESIS_FORMED; multiple new IDs are rejected. Existing IDs
remain preserved. Legacy initial observations retain unknown formation times.

Fresh validation on that SHA: **14/14 focused lifecycle**, **67/67 D2 module**,
**670/670 full offline regression**, each with 0 failures, 0 errors, 0 skips.
CPython 3.12.10 64-bit (MSC v.1943 AMD64), Windows-11-10.0.26200-SP0;
existing dependencies. Implementer execution, not independent reviewer execution.
The earlier 656/656 below applies only to the earlier implementation.
The following documentation commit records the new results without code changes.

Commands from the isolated D2 checkout (all invoked with Python -B):

```powershell
python -B -c "import tempfile,unittest,platform,sys; tempfile.tempdir=r'C:\Users\User\AppData\Local\Temp\MarketRadar_D2_OfflineTests'; print(sys.version); print(platform.platform()); loader=unittest.defaultTestLoader; loader.testNamePatterns=['*test_lifecycle_*']; r=unittest.TextTestRunner().run(loader.loadTestsFromName('test_offline_scenario_ledger')); print('FOCUSED',r.testsRun,'failures',len(r.failures),'errors',len(r.errors),'skipped',len(r.skipped)); raise SystemExit(not r.wasSuccessful())"
python -B -c "import tempfile,unittest; tempfile.tempdir=r'C:\Users\User\AppData\Local\Temp\MarketRadar_D2_OfflineTests'; r=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromName('test_offline_scenario_ledger')); print('D2',r.testsRun,'failures',len(r.failures),'errors',len(r.errors),'skipped',len(r.skipped)); raise SystemExit(not r.wasSuccessful())"
python -B -c "import tempfile,unittest; tempfile.tempdir=r'C:\Users\User\AppData\Local\Temp\MarketRadar_D2_OfflineTests'; r=unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('.', pattern='test_*.py')); print('FULL',r.testsRun,'failures',len(r.failures),'errors',len(r.errors),'skipped',len(r.skipped)); raise SystemExit(not r.wasSuccessful())"
```

Correction changes only offline_scenario_ledger.py, test_offline_scenario_ledger.py,
the D2 contract and this HANDOFF. Accepted D1 source/tests, news interpretation,
cutoff/provenance rules, PA/HTF, permissions and notification engines are unchanged.
The review correction made no schema change, D3-D6 implementation or runtime change. Independent code review APPROVED; offline acceptance authorized.

### Earlier D2 implementation record

Status: **ACCEPTED / CLOSED**.
Branch: `feature/d2-scenario-decision-ledger`.
Accepted D1 main: `2465cb7532c0be3dff259d48eb22ea78a56638e5`.
D1 was accepted for offline use after independent code review of
`82577797a8d9cbc623edc7a5f7ad6e9d3e7acc54`; implementation/tests unchanged.
Its 603/603 result is the implementer's Windows/Python 3.12.10 run, not independent
test execution. Synthetic clocks, unverified source authenticity, external retention
dependency and absent production freshness policy remain limitations.

D2 adds `offline_scenario_ledger.py`, `test_offline_scenario_ledger.py`,
`tests/fixtures/d2/scenario.json` and `docs/D2_SCENARIO_DECISION_LEDGER_CONTRACT.md`.
Only this HANDOFF changes among existing files on the D2 branch.
See [D2 contract](D2_SCENARIO_DECISION_LEDGER_CONTRACT.md) for API, schema,
transaction, provenance and temporal boundaries.

Earlier implementation SHA: `7c1091c6e002099612369e654fae781c1d786a45`.
2026-10-21 full offline regression: **656/656 passed**, 0 failures, 0 errors,
0 skipped (603 accepted + 53 D2). CPython 3.12.10, 64-bit,
Windows-11-10.0.26200-SP0, existing installed dependencies; no fresh installation
or Linux/CI result claimed. This is implementer validation, not independent review.
The following documentation-only commit records results without changing code.

Exact command from the isolated D2 checkout:

```powershell
python -B -c "import tempfile,unittest,platform,sys; tempfile.tempdir=r'C:\Users\User\AppData\Local\Temp\MarketRadar_D2_OfflineTests'; print(sys.version); print(platform.platform()); r=unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('.', pattern='test_*.py')); print('RESULT',r.testsRun,'failures',len(r.failures),'errors',len(r.errors),'skipped',len(r.skipped)); raise SystemExit(not r.wasSuccessful())"
```

Synthetic validation includes all requested D2 categories, six independently
executed historical prefixes, full semantic equality, concurrent evaluation and
separate-process crashes before/after commit. D1 is read without journal changes.
Limitations: synthetic technical/clock authority, declared source attestations,
caller-owned external retention, and no approved production freshness policy.
Each evaluation is tied to its scenario version's original cutoff; newer inputs
require a new explicit scenario version. No historical forecast quality is claimed.

D2 offline acceptance and fast-forward merge authorized; no D3-D6 implementation or live authorization. Work is isolated
from the working Radar; no MT5, Telegram or trading operations are involved.

## Prior accepted stages

Date: 2026-10-21
Status: **C3.3.6 — ACCEPTED / CLOSED**
Base: `8af2c7439fd63a2bad2fcf4945ae0f26b5db5d38`
Branch: `feature/c3-3-6-offline-validation`
Implementation SHA: `926e19155a3475438b0a6f72d238bee3129cf5b0` (559/559 tested). Approved branch HEAD: `ddc2ca5603df2e8535d5b351193afc06b8ea8add`.

C3.3.5 remains ACCEPTED / CLOSED in the base. This feature validates contracts;
it does not deploy them or establish historical forecast quality.

## Files

Added:
- `offline_validation.py`
- `historical_evidence_assessment.py`
- `test_offline_validation.py`
- `test_historical_evidence_assessment.py`
- `tests/fixtures/c336/manifest.json`
- `tests/fixtures/c336/sequence.json`
- `tests/fixtures/c336/README.md`
- `tests/fixtures/c336/.gitattributes` (stable LF for hashed fixture JSON)
- `docs/C3_3_6_CONTRACT.md`

Updated only `docs/HANDOFF.md` among existing tracked files.
Accepted source modules and tests are unchanged. Development uses a separate
checkout, `work/c336_validation`, created from the exact accepted GitHub base.

## Validation results

Implementer-reported full regression: **559/559 passed**, 0 failures/errors (530 existing + 29 new).
Python 3.12.10, Windows, existing installed dependencies, 2026-10-20.
Used Python -B with unittest discovery and an explicit writable temporary directory
outside every repository. No fresh dependency installation or Linux/CI result is
claimed. See C3_3_6_CONTRACT.md for commands and exact interfaces.

Synthetic replay: **PASS**, 19 operation cases, nine invariants. Two complete
runs, restart after every operation and each cutoff-prefix comparison match.
Every persisted semantic field is compared; substantive IDs/timestamps are retained.
Semantic report hash:
`0b9ac8badb7e2f83a0e5a75927ff130782b24fce01428b1e57dbeea7aa5860b0`

Historical assessment: **INSUFFICIENT_EVIDENCE**. Evaluated 50 private source
entries. Both XAUUSD reference anchors were found, but input availability,
initial formation and delivery chronology remain unestablished. No PWL touch or
advance recognition is claimed. Before/after SHA-256 inventory confirmed 173
private archive files unchanged. Private archives and generated reports remain
outside the repository and are not published.

## Architecture and limitations

Two modules separate synthetic replay from read-only legacy evidence assessment.
One composed chain calls the accepted driver/grouping/interpretation/reaction/
validation/PA/lifecycle interfaces. Later correction, contradiction and delivery
cases are declared synthetic boundary inputs. This is not exhaustive coverage of
every live or macro/FOMC path, nor a production adapter. The technical scenario is
supplied and preserved, not recalculated.

Strict gates check explicit availability, ordering and candle close eligibility.
No publication, observation, file modification or report time is substituted for
availability. Fixture attestations are declared synthetic, not independently
verified facts. Historical timestamp presence alone does not authenticate history.
Scoped parseability PASS never implies historical-timeliness PASS.

The legacy historical assessor is intentionally conservative: it cannot certify a
new trusted ledger format. Concurrent archive modification prevents reproducible
assessment; use fixed private archives. No production database is opened or migrated.

## Acceptance and next step

Independent code review APPROVED; closure authorized on 2026-10-21.
The approved branch is a direct descendant of the unchanged base main, two
commits ahead. Acceptance preserves all implementation, tests and dependencies.
This closure changes documentation only. The implementer's 2026-10-20 regression
is accepted without rerunning it; no independent test execution is claimed.

The acceptance commit fast-forwards main from the base through the approved HEAD.
Final accepted main SHA: `5bf112b1648ac6186399e93edcd9a53dc8ca642e`.

Block D architecture: **APPROVED / ACCEPTED** (2026-10-21).
D1: **ACCEPTED / CLOSED** for offline use, 2026-10-21.
Independent code review APPROVED for `82577797a8d9cbc623edc7a5f7ad6e9d3e7acc54`.
Acceptance changes documentation only; D1 implementation and tests are unchanged.
Accepted Block D main: `a812c9b32371fa8b8da0df92d977f7a0ed3e8a89`.
D1 branch: `feature/d1-offline-evidence-ledger`.
See [D1 contract](D1_OFFLINE_EVIDENCE_LEDGER_CONTRACT.md).
D1 adds only the offline ledger module, its tests, one synthetic JSON fixture and
the technical contract; this HANDOFF is the only existing file changed.
Tested implementation SHA: `1c78c9f9bb10b4b995e042b78903f2ac7099339d`.
2026-10-21 full offline regression: **603/603 passed**, 0 failures, 0 errors,
0 skipped (559 accepted + 44 D1). Windows-11-10.0.26200-SP0, CPython 3.12.10
64-bit, existing dependencies. All 18 requested D1 synthetic categories are
covered, including real process termination before/after READY commit and
concurrent duplicate publication. No independent reviewer run is claimed.
The following documentation-only commit records these results without changing code.
D1 closure is authorized; the 603/603 result remains attributed to the implementer.
No independent test execution or live authorization is claimed.

Exact regression command from the isolated D1 checkout:

```powershell
python -B -c "import tempfile,unittest,platform,sys; tempfile.tempdir=r'C:\Users\User\AppData\Local\Temp\MarketRadar_D1_OfflineTests'; print(sys.version); print(platform.platform()); r=unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('.', pattern='test_*.py')); print('RESULT',r.testsRun,'failures',len(r.failures),'errors',len(r.errors),'skipped',len(r.skipped)); raise SystemExit(not r.wasSuccessful())"
```

Initial test launches were blocked by sandbox-default temp access and the
repository-path exclusion; the successful run used the explicit external temp
directory above. No production paths were used. D1 synthetic JSON fixture SHA-256
(tested working-file bytes):
`5ae61008df422e8587ec93746989648434ae8d274488794340c1da8a9439971d`.
Fixture is parsed JSON, not an exact-byte fixture manifest; checkout line-ending
conversion does not change its parsed semantics.

Limitations: synthetic clock policy only; attested source independence remains
unverified; external payload retention is caller-owned and missing/corrupted bytes
fail closed. Hash chain/SQLite triggers do not establish tamper-proof history.
No production clock/freshness/source-trust policy, notification or runtime adapter
is implemented. Accepted interfaces and PA/HTF modules remain unchanged.
Approved planning HEAD: `eba6a53083161757b94f8bc2c4fcd152fdf5bf3f`.
Planning branch: `feature/block-d-controlled-integration-plan`.
See [technical plan](BLOCK_D_CONTROLLED_INTEGRATION_PLAN.md).
D1 offline evidence ledger; D2 immutable scenario/decision ledger; D3 offline
notification audit bridge; D4 read-only shadow boundary; D5 controlled shadow
assessment; D6 optional controlled transport. D4-D6 require separate authorization.
Approved planning branch fast-forwarded into main; this separate acceptance changes HANDOFF only.
D1 is accepted. D2 is authorized in an isolated branch from the new acceptance main.
D2 is accepted for offline use. D3-D6 are not accepted implementations. Exact D1 acceptance main:
`2465cb7532c0be3dff259d48eb22ea78a56638e5`.
Freshness, clock uncertainty, source trust, movement definitions, private retention
and subscription/retry policies remain explicit decisions before runtime.
No live deployment is authorized. Historical/XAUUSD remains
INSUFFICIENT_EVIDENCE; neither historical forecast timeliness nor trading
performance has been established.

**Do not start Radar, connect MT5, send Telegram messages, place trades, change
PA/HTF or permissions, or modify/synchronize the working Radar directory.**
