# D5 controlled shadow assessment — d5_shadow_v1

**IMPLEMENTED / AWAITING INDEPENDENT REVIEW** — offline synthetic use only.
Baseline: `bd0070056aa9c10ef8e74799af4bbf421cc1ba89` (accepted D4 main).
Branch: `feature/d5-controlled-shadow-assessment`.
Preflight confirmed exact remote main, accepted D1–D4 status, ancestry of tested
D4 `5e3d227772df73b6ec60c910787716b86268ef55`, and documentation-only changes after
that SHA. Existing baseline: 809 tests. Accepted source, tests and fixtures remain
unchanged. The working Radar directory was not accessed or synchronized.

## Public APIs and authority levels

`replay(raw: bytes, *, ledger_context=None, stop_after=None) -> ReplayResult`
accepts one bounded UTF-8 JSON fixture. Candidate-only replay is pure: no files,
system clock, credentials, network, subscriptions, services or database writes.
External paths/URLs are never resolved. There is no filesystem intake or report
export API. Explicit caller-selected isolated output is the only permitted place
to save a report; D5 itself writes no report files.

`ReplayResult.report_json` is an immutable canonical serialized public report.
`.report` returns a new dictionary each time. Mutating the original input or a
returned dictionary cannot rewrite the retained result. There is no durable D5
store or claim of durable crash recovery. Interrupted pure replay is recomputed
from the same bounded immutable fixture, optionally using stop_after.

`zone_diagnostic(zone, observation, cutoff) -> dict` returns position, touch,
break_state. These are synthetic diagnostics, not accepted PA definitions.

`assess_ledger_notification(audit, request, *, scenario_hash, scenario_version_id,
evidence_version_id, evidence_content_hash, decision_cutoff, report_cutoff,
operation_id) -> dict` is the explicit authoritative synthetic path. `audit` must
be an accepted NotificationAudit instance backed by caller-prepared **isolated
synthetic output journals**. It is not a path or a database opener. The caller
must never pass production ledgers. Accepted constructor restrictions still apply.

This path reads accepted D2 projection, matches exact scenario version/content,
evaluation cutoff and selected D1 evidence version/hash, and calls accepted
`NotificationAudit.assess`. D3 verifies its own exact upstream journal bindings.
D5 never implements an alternative D3 eligibility algorithm. The audit clock
must equal the requested report cutoff and the evaluation cannot be from the future.

Only D3 policy/eligibility records are written by that opt-in path. D1 and D2
records are read-only; input fixture bytes are read-only. D5 does not construct
ledgers, ingest evidence, publish READY, form scenarios, generate notifications,
prepare deliveries, start attempts or reconcile providers. Synthetic historical
notification records used by tests are explicitly seeded through accepted APIs
outside D5, without a delivery attempt or transport.

`ledger_context` maps exact fixture event IDs to `{audit, request}`. Its request
must match the snapshot's evaluation ID, hypothesis, episode, event type, policy
and recipients. Each context must expose the intended historical prefix and an
appropriate synthetic clock. Do not reuse a later mutable audit history to claim
earlier eligibility; incompatible chronology is UNKNOWN. A caller requiring full
authoritative replay must supply independently prepared per-cutoff synthetic
contexts. D5 does not synthesize missing histories to make replay pass.

Four scopes remain distinct:

| Scope | Meaning |
|---|---|
| D4 candidate_status | Accepted D4 compatibility result, not ledger membership |
| DECLARED_SYNTHETIC_ONLY | Availability/relation claims in the fixture, not authentication |
| ACCEPTED_SYNTHETIC_LEDGER | D3 assessed actual supplied D1/D2 journal references |
| UNAVAILABLE / UNKNOWN | Missing/rejected authoritative prerequisites |

D4's missing D3 lifecycle-history limitation may be resolved only by actual D3
assessment. Other D4 failures block that path. A candidate duplicate diagnosis
never becomes SUPPRESSED_DUPLICATE automatically. Accepted D3 alone supplies
ELIGIBLE, SUPPRESSED_DUPLICATE, DEFERRED, INELIGIBLE or UNKNOWN. Its event types
remain NEW/MATERIAL_UPDATE/RESOLVED/EXPIRED; DEVELOPING is D3's lifecycle state for
MATERIAL_UPDATE, not a new D5 event enumeration. No delivery permission is returned.

## Versioned fixture schema

`tests/fixtures/d5/base.json` is the complete executable example. The case catalog
maps 32 general cases and 28 XAU-focused cases to deterministic test constructors.
All prices and timestamps are synthetic examples inspired by the user description.
No screenshots, Telegram exports or private archives are packaged.

Case fields:

- schema_version=d5_shadow_v1, classification=synthetic, baseline=exact accepted SHA.
- case_id, instrument; scope=candidate_only or synthetic_ledgers.
- policy: version=d5_policy_v1, classification=synthetic.
- known_uncertainties and expected_invariants lists. Expectations are test metadata,
  never a source of an assessment outcome.
- steps: ordered nonempty list, maximum 128 cutoffs.

Each step contains:

- event_id, sequence, cutoff `{as_of_utc, maximum_visible_ledger_sequence}`.
- snapshot: a complete D4 bundle (envelope, payload, clock, policy, cutoff),
  snapshot_hash=canonical SHA-256 of that exact bundle.
- evidence: at most 128 declared observations. Each has id, source_id, item_id,
  revision, predecessor_id, observed_at_utc, available_at_utc, sequence, payload,
  content_hash, status, relation, timeframe, max_age_seconds, used_as_current.
- transition: null initially or `{event_id, predecessor_event_id, at_utc,
  sequence, evidence_ids, causal_reason, classification}`.
- targets: original_point_b, current_point_b, primary_source, local_anchor, history.
- formation: hypothesis ID to established UTC or null; unknown formation remains unknown.
- narratives: explicitly annotated semantic statements (see below).
- entry: timing and trade_permission_claimed; optional zone bounds/observation.
- historical_evidence: synthetic_only for test claims. Artifact-based history
  remains HISTORICAL_CHRONOLOGY_UNPROVEN.
- optional claimed_notification_status, checked against an actual D3 result.

Unknown extensions remain in private source bytes and are included in snapshot
integrity where applicable. D5 does not execute or interpret arbitrary extensions.
Raw bytes are not returned in the public report. Input and fixture files must be
retained privately by the caller if exact audit reconstruction is required.

Limits: 4,000,000 input bytes, depth 40 (checked before parsing), 100,000 JSON nodes,
128 steps, 128 observations per step; UTC-aware zero-offset dates in 2000–2100;
nonnegative integer sequence positions, finite numeric values. Duplicate JSON
keys, executable/non-JSON inputs, nonfinite numbers, wrong shapes and unknown
contracts fail closed with fixed error codes. No unbounded filesystem discovery.

## Cutoff and prefix semantics

Steps require unique immutable event IDs and strictly increasing explicit sequence
positions, with nondecreasing UTC cutoffs. Equal timestamps are ordered by sequence;
displayed minute strings are ignored. Missing/reversed ordering fails closed.

Observation eligibility requires observed <= available <= current cutoff and
sequence <= maximum visible sequence. Missing availability is UNKNOWN; no file
mtime, intake time or historical message timestamp substitutes for it. Delayed,
scheduled, stale and unconfirmed observations stay visible with fixed reason codes.
Explicit age policy uses observed time; a scheduled event is not a published surprise.

Each source revision is unique; content changes under the same identity are rejected.
Corrections require the known predecessor and matching source/item lineage. Earlier
records retain their exact evidence prefix and cannot be reinterpreted using a
later correction. No older version is selected as a fallback for missing new data.
The D4/D2 decision cutoff remains separate from the later report cutoff.

Transition evidence must be available at both the change instant and the frozen
D2 decision cutoff, with eligible sequence positions. Transition time is no earlier
than the prior assessed cutoff and no later than scenario creation/report cutoff.
There is no inferred transition time from a chart or displayed Telegram timestamp.

Each record binds the exact input prefix, eligible declared evidence prefix, D4
result hash, scenario content, identities, policy and discrepancies. Stable prefix
identity excludes later steps and expected test outcomes. Full replay timeline[:N]
equals independently evaluated prefix timeline. Overall report hash changes when
the assessed prefix changes; individual earlier record hashes do not.

Corrupt snapshot hashes produce a rejected cutoff record without advancing trusted
evidence/scenario state. Structural parse/order errors reject the supplied case;
the caller may separately evaluate a known-valid earlier prefix. No repair occurs.

## Scenario continuity and target lineage

D5 compares supplied fields, never recalculates PA/HTF, FVG, OB, displacement or
direction. A material Point A/B, direction, target, invalidation, topology or
permission change needs a new scenario version, exact predecessor and available
causal evidence. Original formation cannot silently change. New hypothesis IDs
need individually targeted HYPOTHESIS_FORMED; removed IDs or lost parent/role
relationships are discrepancies. Primary reversal needs technical authority.
Permission changes also require an explicit diagnostic authority declaration.
These declarations never grant D5 trading permission.

Target comparison retains fingerprinted before/after structural Point B, FTA,
intermediate targets, event, cutoff, causal reason and classification. Original
Point B and primary source are immutable. A local continuation anchor is separate.
Target history distinguishes introduced/reached/breached/superseded/invalidated,
with evidence identity, matching declared target/state and timestamp; breached
requires explicit closed_confirmation. These are synthetic claims, not authenticated
market facts. Reaching a target alone cannot authorize a new structural target.

Classification: SCENARIO_REVISION, DYNAMIC_FTA_UPDATE, NEARER_OBSTACLE,
ROUTE_ONLY_UPDATE, TARGET_STATUS_UPDATE, TARGET_PROGRESSION, UNCHANGED or
UNEXPLAINED_TRANSITION. TARGET_PROGRESSION_AVAILABILITY_UNKNOWN and
TARGET_PROGRESSION_UNVERIFIED are derived diagnostic outputs, not input event types.
Dynamic FTA/nearer-obstacle changes preserve Point B; route-only classification
cannot hide a material technical change. Unsupported explanation remains unexplained.

The focused examples preserve 4073.55 → reached/breached → 4019.92 history and
independent FTA 4019.92 → 4058.97 transitions, testing valid and incomplete variants.
Same-minute notification candidates use event/causation/sequence identity, never
titles or displayed timestamps as proof. Actual D3 tests cover semantic suppression,
material FTA updates, resolution proof, expiry, unknown and deferred outcomes.

### XAUUSD clarification — D1 fractal IDM 4019.92

The user identifies 4019.92 as a D1 fractal IDM (inducement) liquidity objective.
It is a structurally justified **candidate**, not an arbitrary price. PWL 4073.545
is the preceding intermediate liquidity objective in continuing bearish delivery;
4058.97 and 4044.76 remain intermediate FTA/TA. Earlier fixture values 4073.55
are rounded examples, not a different historical objective. The new
`tests/fixtures/d5/idm_progression.json` uses the exact clarification values.

No relevant intervening HTF FVG is identified by the user. The fixture retains
`USER_REPORTED_NONE_IDENTIFIED` as a source claim; D5 does not calculate FVGs or
claim to independently establish that the path is obstacle-free.

TARGET_PROGRESSION describes activation of a pre-established liquidity objective
within the same scenario and primary hypothesis, with unchanged direction and
HYPOTHESIS_REVISED under an explicit accepted synthetic technical authority.
It requires the normal new scenario version, predecessor, target history and
timestamped causal event. It does **not** require a new hypothesis ID or a new
formation event solely because the next target becomes active. In this specific
progression path, the accepted D2 revision authority replaces D5's extra
target_change_authorized flag; merely reaching a target still creates no authority.

The transition references exactly one matching D1 evidence candidate with
payload `{objective_type: D1_FRACTAL_IDM, price, established_at_utc}`. Its price
matches Point B; timeframe is D1. Establishment must precede availability, and
availability/sequence must be eligible at the causal event and frozen decision
cutoff. Other causal evidence must also be eligible. Synthetic support is a declared
fixture proof only, never independent authentication of the historical IDM.

The comparison separately reports structural_candidate, historical_availability,
declared_change_event and declared_causal_reason:

| Evidence situation | Availability / result |
|---|---|
| Established and eligible by cutoff | SUPPORTED_SYNTHETIC / TARGET_PROGRESSION |
| Establishment or availability chronology missing | UNKNOWN / TARGET_PROGRESSION_AVAILABILITY_UNKNOWN; EVIDENCE_GAP |
| Explicit availability or sequence after cutoff | NOT_AVAILABLE_AT_CUTOFF / TARGET_PROGRESSION_UNVERIFIED; TEMPORAL_VIOLATION |

Missing chronology retains DECLARED_D1_FRACTAL_IDM and does not classify the
target itself as structurally incorrect or an unexplained reversal. Explicitly
late data cannot be used retrospectively. Unknown/unverified progression cannot
invoke authoritative D3 eligibility through the replay coordinator. Corruption,
staleness, missing predecessor/version, an actual reversal, or missing unrelated
causal evidence retain their original rejection; the clarification does not
downgrade those errors to simple availability uncertainty.

Original and current targets, preceding PWL, intermediate FTA/TA, primary source,
formation time and earlier record hashes remain separate. A synthetic D2 test
verifies revision under the same hypothesis ID and unchanged formation history.
Historical availability of the actual IDM remains **UNKNOWN** until timestamped
evidence is supplied; user confirmation establishes intended target semantics,
not its historical availability to the engine.

## Price, narrative and macro diagnostics

Zone output has separate position (ABOVE_ZONE/INSIDE_ZONE/BELOW_ZONE/UNKNOWN),
touch (TOUCH_CONFIRMED/UNKNOWN) and break_state (BREAK_CONFIRMED/UNKNOWN).
Last price only establishes position. A supplied available candle range crossing
the zone can establish synthetic touch; closed candle plus explicit synthetic
confirmation/rule and an outside close is required for synthetic break. Observation,
candle end, candle availability and report cutoff remain distinct. Later screenshots
cannot confirm earlier breaks. No accepted PA rule is overridden.

Narratives are **structured annotations**, not arbitrary-language comprehension.
Kinds: CURRENT_STATE, CONDITIONAL_STATE, TRANSITION_TRIGGER, INVALIDATION_CONDITION,
UNVERIFIED_STATE. An asserted active triggered state needs matching eligible
trigger evidence and closed_confirmation; otherwise STATE_SEMANTICS_MISMATCH.
4107.50 development and 4179.07 weakening/invalidation remain separate annotations.

Entry timing is forming/late/expired/continuation/unknown. Confirmed direction and
late entry coexist. Local anchors cannot reset late/expired timing without explicit
authority, nor replace the primary source. No report has an actionable conclusion.

Supporting/contradicting relations are supplied observations, not new macro signals.
USD support and yield conflict are retained simultaneously. COT is not an entry
trigger; unconfirmed D1 volume, H1 exhaustion, stale W1 volume and pending PMI
retain source/timeframe/status distinctions. No probabilities, new scores or
performance claims are calculated. Missing inputs remain UNKNOWN, not false.

## Discrepancies, report and privacy

Every discrepancy includes cutoff reference, event reference, relevant opaque IDs
and fixed reason code. Categories: CONTRACT_VIOLATION, TEMPORAL_VIOLATION,
LIFECYCLE_VIOLATION, UNEXPLAINED_TRANSITION, POLICY_MISMATCH, EVIDENCE_GAP.
This is also deterministic severity precedence; EVIDENCE_GAP yields final
INSUFFICIENT_EVIDENCE. No discrepancies yields SHADOW_CONSISTENT, never trade or
delivery authorization. Overall status considers **all** cutoffs, not only the last.

Timeline contains case/event/cutoff identity, input/evidence prefix fingerprints,
D4 result/reasons, authoritative D1/D2/D3 references where available, primary/nested/
alternative references, scenario/invalidation/target/narrative fingerprints, direction,
entry timing, transition comparison, supporting/contradicting/missing evidence,
notification result, diagnostic zone state, discrepancies, final status and hash.
No arbitrary source IDs, causal prose, payloads, raw recipients or credentials are
echoed. Only bounded validated enums, timestamps, numbers and hashes are public.
Hashes permit correlation and dictionary guessing; they are not encryption.
Do not use synthetic input labels to publish real private material.

The historical XAU gate remains **INSUFFICIENT_EVIDENCE**. We did not receive the
referenced images/Telegram exports in this task; the user's written observations
informed synthetic examples only. Exact target identification, target break,
FTA discovery, notification formation and source availability chronology are unproven.

## Verification and reproduction

Library: **74 named scenario cases** (32 general + 28 original focused XAU +
14 IDM clarification cases), generated from the committed fixtures by explicit
test constructors; catalog provides exact test names. Additional boundary and actual accepted-ledger integration tests extend
coverage. No old test or fixture changed.

From the isolated checkout, with the existing isolated temporary test root:

```powershell
python -B -c "import tempfile,unittest,platform,sys; tempfile.tempdir=r'C:\Users\User\AppData\Local\Temp\MarketRadar_D5_OfflineTests'; print(sys.version); print(platform.platform()); r=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromName('test_offline_shadow_assessment.XauFocusedTests')); print('XAU',r.testsRun,'failures',len(r.failures),'errors',len(r.errors),'skipped',len(r.skipped)); raise SystemExit(not r.wasSuccessful())"
python -B -c "import tempfile,unittest,platform,sys; tempfile.tempdir=r'C:\Users\User\AppData\Local\Temp\MarketRadar_D5_OfflineTests'; print(sys.version); print(platform.platform()); r=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromName('test_offline_shadow_assessment.IdmProgressionTests')); print('IDM',r.testsRun,'failures',len(r.failures),'errors',len(r.errors),'skipped',len(r.skipped)); raise SystemExit(not r.wasSuccessful())"
python -B -c "import tempfile,unittest,platform,sys; tempfile.tempdir=r'C:\Users\User\AppData\Local\Temp\MarketRadar_D5_OfflineTests'; print(sys.version); print(platform.platform()); r=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromName('test_offline_shadow_assessment')); print('D5',r.testsRun,'failures',len(r.failures),'errors',len(r.errors),'skipped',len(r.skipped)); raise SystemExit(not r.wasSuccessful())"
python -B -c "import tempfile,unittest,platform,sys; tempfile.tempdir=r'C:\Users\User\AppData\Local\Temp\MarketRadar_D5_OfflineTests'; print(sys.version); print(platform.platform()); r=unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('.', pattern='test_*.py')); print('FULL',r.testsRun,'failures',len(r.failures),'errors',len(r.errors),'skipped',len(r.skipped)); raise SystemExit(not r.wasSuccessful())"
```

IDM clarification: exact tested implementation and fresh regression results will
be recorded after the correction commit. Status remains awaiting independent review.

Earlier, pre-clarification 2026-10-22 tested implementation: `445660b4d9a0dcc5b827c77f0ff5fcb378f32b52`.
**28/28 focused XAU**, **90/90 total D5**, **899/899 full offline regression**;
0 failures, 0 errors, 0 skipped in each run. CPython 3.12.10 (MSC v.1943 64-bit
AMD64), Windows-11-10.0.26200-SP0, existing dependencies. Full suite: 34.211 seconds.
These are implementer results, not independent validation or Linux execution.
The following result commit changes documentation only. Full regression repeats
the D5 deterministic comparisons and all accepted D1–D4 lifecycle/availability tests.
Tests assert complete report equality, separate-process equality, full/prefix equality,
duplicate submission, immutable earlier records, corruption rejection and accepted
D3 journal restart/idempotency. Candidate replay has no persistence claim.

## Remaining runtime gates

D5 has no runtime sources. The accepted D4 file-race limitation remains unchanged:
it is not protection against hostile concurrent replacement. D5's byte-only input
does not solve secure production acquisition. Any real source needs a separately
reviewed isolation/export mechanism, authenticated availability/clock evidence,
approved freshness/provenance policies, protected ledgers, privacy and recipient
policies, and explicit runtime authorization. None is implemented here.

No D6, production adapter, Radar launch, MT5, Telegram or trading operation.
Do not merge D5 without independent review. Stop after publishing this branch.
