# D4 read-only shadow boundary — d4_boundary_v1

Status: **ACCEPTED / CLOSED**. Offline synthetic assessment only.
Accepted on 2026-10-22 after independent code review found no blocking issues.
Reviewed implementation: `5e3d227772df73b6ec60c910787716b86268ef55`;
reviewed published HEAD: `74b274089e3b65a43c0cba693f546bcefb5525eb`.
Acceptance changes documentation only; existing 79/79 and 809/809 implementer
results stand, with no new regression or independent Python execution claimed.
Concurrent hostile file replacement remains outside the protection guarantee;
accepted use is limited to controlled synthetic fixtures. Real-source D5 requires
separate isolation guarantees and explicit authorization. **D5 is not authorized.**
Accepted D3 main: `5a89d9a08a0339e2db7fcdcb6fc8c47e18ba1043`.
Approved D3 implementation `489e1a4093ffc5d4166e0044f759a772494516f6` is an ancestor;
only D3 contract/HANDOFF changed between those commits. Accepted D1/D2/D3 source
and tests are unchanged. Branch: `feature/d4-read-only-shadow-boundary`.

## Public API

`offline_shadow_boundary.assess_bytes(envelope_bytes, payload_bytes, *, cutoff,
intake_clock, policy) -> Assessment` is pure: no clock reads, file access, network,
imports of accepted ledgers or side effects. Arguments must be bounded UTF-8 JSON
object bytes and caller-supplied synthetic context.

`assess_files(envelope_path, payload_path, *, fixture_root, cutoff, intake_clock,
policy) -> Assessment` reads exactly two explicitly supplied `.json` files under
an explicitly approved synthetic fixture root. Relative paths are relative to
that root. Payload references inside JSON are never followed. This method does
not enumerate directories, infer paths, inspect the runtime, or read credentials.

`Assessment.report` is the public structured result. `envelope_bytes` and
`payload_bytes` retain exact bounded valid source bytes privately, including all
unknown fields/correlation IDs. They are excluded from repr. **Do not serialize
the whole dataclass (including with asdict)** or publish its private bytes. Only
serialize `.report`; original input may contain arbitrary private data.
Malformed/oversized input can be discarded without retention. No disk report
writer, CLI, persistence, synchronization, exporter or ledger constructor exists.
An explicitly authorized caller may save `.report` to its isolated output directory.

Example with the committed synthetic bundle:

```python
import json
from pathlib import Path
from offline_shadow_boundary import assess_bytes, canonical

x = json.loads(Path("tests/fixtures/d4/bundle.json").read_text(encoding="utf-8"))
result = assess_bytes(canonical(x["envelope"]).encode(), canonical(x["payload"]).encode(),
                      cutoff=x["cutoff"], intake_clock=x["clock"], policy=x["policy"])
assert result.report["final_decision"] == "SHADOW_READY"
```

## Explicit input profile

This is a **normalized D4 candidate profile**, not the serialized database records
of D1/D2/D3. It does not claim to revalidate authentic ledger membership, full D1
prefixes, authoritative D2 manifests or the latest authoritative predecessor.
No automatic conversion from production outputs is implemented. Future conversion
requires a separately reviewed mapping with exact immutable ledger references.

Envelope schema `d4_snapshot_v1`:

| Field | Meaning / required rule |
|---|---|
| schema_version, classification | Exact schema and `synthetic` |
| snapshot_id | Nonempty identity; public report uses fingerprint |
| source | component, producer_version, artifact_type, instrument, timeframe, provenance |
| source.provenance | method `synthetic_fixture_attestation`, nonempty attestation_id |
| content_hash | SHA-256 of exact payload bytes, not reserialized bytes |
| raw_payload_ref | Exactly `sha256:<content_hash>`; never a file to follow |
| observation_start_utc/end_utc | Separate observation window; null means unknown |
| published_at_utc | Publication, not availability; nullable with explicit publication_missing_reason |
| claimed_available_at_utc | Claimed readiness; null remains unknown |
| availability_attestation | method `synthetic_ready_attestation`, authority, ready_sequence |
| declared_completeness | `complete` required for ready; otherwise insufficient |
| known_limitations | List, retained privately |
| optional extensions | Retained verbatim, including correlation/causation IDs; never executed |

Intake clock is a separate required argument and appears in the report. It contains
`at_utc`, `session_id`, `health_id`, finite nonnegative `monotonic` and
`uncertainty_seconds`. The latter must not exceed explicit policy. No system clock,
file modification time, message creation or intake time supplies missing availability.
Single-snapshot checks do not establish clock health across a real process restart.

Policy: `classification=synthetic`, `contract_version=d4_boundary_v1`, a versioned
`policy_id` beginning `synthetic:`, explicit positive integer `max_file_bytes`,
`max_envelope_bytes`, `max_payload_bytes`, `max_depth`, `max_nodes`, finite nonnegative
`max_clock_uncertainty_seconds`, `provenance_method=synthetic_fixture_attestation`,
`availability_method=synthetic_ready_attestation`. There are no production defaults.
Limits are capped at 16,000,000 and depth at 100. Fixture policy uses 131072 bytes
per file/payload, 32768 envelope bytes, depth 24, 10000 nodes, zero clock uncertainty.

Payload schema `d4_candidate_bundle_v1` requires `d1`, `d2`, `d3` objects:

### D1 candidate

Required source_id, item_id, version_id, selected_version_id, schema_version,
source_revision, evidence_payload, content_hash, published, observed,
available_at_utc, ready_sequence, availability_authority, attestation, quality,
validation_status, predecessor. Metadata schema_version is a nonempty source
schema identity; D4 does not implement arbitrary source-specific validators.

Content hash is SHA-256 of canonical evidence_payload (sorted keys, compact UTF-8,
no nonfinite values). Publication/observation are `{at_utc, authority}`; publication
may explicitly be absent with missing_reason. Attestation includes verification_method,
claimed_source_identity, nonempty lineage list, uncertainty and boolean independent.
Source identity must match; synthetic verification only. `independent=False` is a
valid declaration and never upgraded. `True` is not independent authentication.
Validation status must be valid. Quality remains a source declaration.

Selected version must equal supplied version: never substitute an older available
version. Correction revision other than `1` requires predecessor identity with same
source/item and distinct version_id. This checks structural lineage only; it cannot
authenticate that predecessor or prove that no newer correction exists.

### D2 candidate

Required scenario_id, scenario_version_id, scenario, cutoff, evidence_ref, authority,
event_type, hypothesis_id, predecessor, created_at_utc, formation_at_utc.
evidence_ref is exactly `{version_id, content_hash, cutoff}` matching supplied D1
and the scenario's own frozen decision cutoff. That cutoff is distinct from the
outer assessment cutoff; both time and sequence must be no later than the outer
cutoff. Evidence must already be ready at the decision cutoff, which cannot be
later than scenario creation. Later evidence never becomes eligible merely because
it precedes report intake.
This D4 reference is not an authoritative D2 evidence manifest. Authority is explicit
`synthetic_technical_authority` with engine_version.

Full scenario retains instrument, timeframe, point_a, point_b, primary, nested,
alternatives, direction, htf, market_structure, FTA, TA, invalidation,
liquidity_objectives, permissions and all extensions. Missing content is not invented.
Points require finite price, UTC time and nonempty definition. Hypothesis IDs are
unique; parent links must exist and be acyclic. Primary direction matches scenario;
nested bullish hypotheses do not change bearish primary. Permissions remain exact.

Lifecycle events: HYPOTHESIS_FORMED, HYPOTHESIS_REVISED, HYPOTHESIS_INVALIDATED,
SCENARIO_OBSERVED_EXISTING. Initial formation time equals creation time in this
profile. Legacy observation retains null formation. Revision/invalidation requires
predecessor with version_id, same scenario_id, full scenario and cutoff. Existing IDs
cannot disappear; each new ID needs its individually targeted formation. Direction
or primary changes require direction_change_authorized; parent changes require
topology_change_authorized. Invalidation requires target state invalidated.
For a new targeted formation, formation_at_utc means **that target's formation**,
not the top-level original formation_at_utc stored in accepted D2 records.
Other hypothesis formation times are not inferred. Supplied predecessor is not
proof of authoritative latest-version membership.

### D3 candidate

Required evaluation, hypothesis_id, episode_id, event_type, reason, policy,
recipients, frozen_recipients, eligibility, delivery. Evaluation contains
evaluation_id, scenario_version_id, cutoff, manifest_fingerprint (D4 evidence_ref
hash), technical_context_hash (entire scenario hash), evaluated_at_utc, knowledge
(KNOWN/UNKNOWN), action (RECORDED/WAIT), news_status (supports/conflicts/
insufficient_data/not_evaluated). All references must agree; evaluated time is
between creation and cutoff. Episode uses synthetic identity, reason is nonempty.

Notification policy declares synthetic version, allowed_roles/events/states,
boolean require_entry_permission, nonnegative episode_max_age_seconds, and retry
policy (synthetic version, positive max_attempts, boolean allow_failed_retry and
allow_reconciled_unknown_retry). Policy presence is not policy approval.

Recipients are opaque `{set_id: "synthetic:...", fingerprint: <64 lowercase hex>}`
and equal frozen_recipients. No raw recipient list is accepted. Exact hypothesis,
role-preserving scenario, news_status, episode, event and recipient descriptor
provide semantic deduplication prerequisites; **no duplicate-free claim is made**.
v1 only assesses NEW without history. MATERIAL_UPDATE/RESOLVED/EXPIRED return
LIFECYCLE_HISTORY_NOT_VALIDATED until a separately reviewed history proof exists.

Eligibility can be null or a status descriptor (ELIGIBLE/SUPPRESSED_DUPLICATE/
DEFERRED/INELIGIBLE/UNKNOWN) with generation_claimed=false. This checks separation,
not actual eligibility. D4 never grants eligibility or generates notifications.
Delivery can be null or PREPARED/STARTED/ACKNOWLEDGED/FAILED/UNKNOWN/RECONCILED.
recipient_delivery_proven and recipient_read_proven must be false. authorize_retry
must be boolean. Reconciliation has provider_accepted/confirmed_not_accepted/
still_unknown. Retry true requires confirmed_not_accepted, enabling synthetic
retry policy, duplicate_risk_acknowledged=true, and positive attempt_count below
max_attempts. UNKNOWN/STARTED/still_unknown remain insufficient. Nothing here
prepares, starts, reconciles or proves an actual provider operation.

## Time and determinism

All timestamps are explicit UTC-aware ISO strings; nonzero offsets/naive times fail.
Cutoff is `{as_of_utc, maximum_visible_ledger_sequence}` with nonnegative integer
sequence. as_of <= synthetic intake; publication <= availability; observation start
<= end <= availability <= cutoff. D1 publication <= receipt <= availability <= cutoff.
Both D1 and envelope ready_sequence must be <= outer visible sequence, including
equal timestamp cases. Additionally D1 availability and sequence must be within
the frozen D2 cutoff. Points and observed technical `*_at_utc` cannot be after
that decision cutoff, even if they precede the report's outer assessment cutoff.
`scheduled_*_at_utc` extensions remain future metadata, never evidence of observation
or availability. Missing timestamps remain unknown. Predecessor cutoff cannot roll
back time or sequence. No state is retained between assessments; later corrections
cannot modify a previous returned report. Report hash covers its complete public
content except report_hash itself. The caller may mutate its own returned dict;
D4 provides reproducibility, not a tamper-proof report archive.

## Independent checks and final precedence

Each check is PASS/FAIL/UNKNOWN: PARSEABLE, SCHEMA_VALID, SEMANTICALLY_COMPATIBLE,
PROVENANCE_SUPPORTED, AVAILABILITY_SUPPORTED, SHADOW_READY. Per-D1/D2/D3 status is
local to that candidate; it is not an override of global schema/provenance checks.
Fixed reason codes include explicit missing required field names. All safely
evaluated reasons are retained. Unsafe size/format/path input stops before deeper
assessment; malformed object shapes fail closed with INVALID_FIELD_SHAPE rather
than leaking source-provided exception messages. Unassessed checks remain unknown.

Deterministic first applicable final decision:

1. BOUNDARY_VIOLATION — path, limits, unsafe format, policy or non-synthetic input.
2. SCHEMA_INCOMPATIBLE — malformed JSON, missing/unsupported schemas, integrity or shape.
3. PROVENANCE_UNVERIFIED — unsupported/missing provenance declaration.
4. AVAILABILITY_UNKNOWN — missing, contradictory or ineligible time/sequence/clock.
5. INSUFFICIENT_EVIDENCE — semantic or D1/D2/D3 compatibility gaps.
6. SHADOW_READY — all checks pass for this explicitly synthetic profile only.

SHADOW_READY proves no trade permission, source authentication, historical forecast,
actual ledger membership, delivery eligibility or authorization for live integration.

## Report and privacy

Public report contains contract_version; policy, snapshot identity, source identity,
cutoff and input fingerprints; content hash; normalized synthetic intake time and
clock fingerprint; check and candidate results; fixed reason codes/warnings; final
decision and report_hash. Hashes identify exact private source and policy versions
without echoing arbitrary identifiers or fields. Identity lookup requires the retained
private bytes. Unsafe early rejection returns a reduced report with unknown checks.
The fixture bundle is the complete schema example; no private source strings, paths,
credentials, raw recipient IDs or exception texts appear in the public result.
Hashes are not encryption and may permit correlation/dictionary guesses; do not
use this reporting API to publish real secret-bearing inputs. Only synthetic input
is authorized. Arbitrary strings prefixed synthetic are declarations, not proof.

## Source safety and recovery

Only bounded JSON objects; reject duplicate keys, nonfinite numbers, executable
formats, excessive depth and node counts. Strings never execute. File APIs reject
runtime paths lexically before filesystem access, UNC/network paths, `..`, outside
root paths, non-json suffixes, nonregular files, symlinks and Windows reparse points
on the explicit path's ancestry. Open mode is rb. Before/open/after identity,
size and mtime changes fail closed. mtime is used only to detect mutation, never
to establish availability. Missing/corrupt files produce fixed rejection reports.
Recovery is a new explicit assessment after the caller corrects its fixture;
there are no retries, watchers, services or partial ledger commits.

Security limitation: filesystem validation is not an OS sandbox or race-proof
Windows handle traversal. The fixture root and ancestors must be protected from
hostile concurrent replacement (including swap-and-restore). Static reparse
escapes and detected changes are rejected; aliases/hardlinks and unobservable
concurrent changes require trusted fixture preparation. Use assess_bytes for the
smallest boundary. This is not an authorization to open arbitrary user files.

## Verification

Implementation tests cover the 38 requested categories; persistence is intentionally
absent, with stateless fresh-process replay tested instead. Tests include metadata
symlink/reparse injection and an actual temporary directory junction on Windows
(symlink on POSIX), no writes to D1/D2/D3 sentinel files, import isolation, source
immutability, privacy, temporal contradictions, lifecycle and retry cases.

Run from the isolated checkout with an existing isolated temp root:

```powershell
python -B -c "import tempfile,unittest,platform,sys; tempfile.tempdir=r'C:\Users\User\AppData\Local\Temp\MarketRadar_D4_OfflineTests'; print(sys.version); print(platform.platform()); r=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromName('test_offline_shadow_boundary')); print('D4',r.testsRun,'failures',len(r.failures),'errors',len(r.errors),'skipped',len(r.skipped)); raise SystemExit(not r.wasSuccessful())"
python -B -c "import tempfile,unittest,platform,sys; tempfile.tempdir=r'C:\Users\User\AppData\Local\Temp\MarketRadar_D4_OfflineTests'; print(sys.version); print(platform.platform()); r=unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('.', pattern='test_*.py')); print('FULL',r.testsRun,'failures',len(r.failures),'errors',len(r.errors),'skipped',len(r.skipped)); raise SystemExit(not r.wasSuccessful())"
```

2026-10-22: exact tested implementation `5e3d227772df73b6ec60c910787716b86268ef55`:
**79/79 D4**, **809/809 full offline regression**, 0 failures, 0 errors, 0 skips.
CPython 3.12.10 (MSC v.1943 64-bit AMD64), Windows-11-10.0.26200-SP0;
existing dependencies. Full suite ran in 34.240 seconds. Implementer execution,
not independent reviewer validation. The result commit changes documentation only.
Windows sandbox default temporary-directory access failed
during preliminary tests; isolated temp storage is required in this environment.
No dependency changes or live services are involved. Linux execution is not claimed.

## Conditions before any future runtime work

Separate authorization and independent D4 acceptance; approved source-export mapping
and immutable raw evidence; authenticated availability/clock authorities; approved
freshness policies; real ledger membership and prefix proofs; latest predecessor
and lifecycle history verification; independent provenance validation; recipient,
deduplication and retry policies; protected snapshot root/OS access isolation and
privacy review. These are unimplemented conditions, not a plan to start D5 now.
Existing PA/HTF and news-permission separation remain unchanged. No runtime access,
MT5, Telegram, production collector, trading action or deployment is authorized.
