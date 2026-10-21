# D3 — Offline Notification & Delivery Audit

Status: **IMPLEMENTED / AWAITING INDEPENDENT REVIEW**
Contract: `d3_v1`; date: 2026-10-21.
Base: accepted D2 main `54f92effad0515757ca8bfd9ae5263f41e5aa14d`.
Branch: `feature/d3-offline-notification-audit`.

## Scope and trust boundary

`offline_notification_audit.py` records synthetic offline facts. There is no
transport callable, Telegram formatting/client, network worker, scheduler, market
collector, broker/order interface or runtime adapter. D1/D2 source, tests, database
schemas and PA/HTF permissions are unchanged. This ledger does not implement D4-D6.

The recorded chain is D1 prefix/evidence -> exact D2 scenario/evaluation -> separate
eligibility -> generated message -> prepared attempt -> declared start -> outcome
or uncertainty/reconciliation. A hypothesis existing, being technically valid,
deserving an informational alert and permitting trade entry are separate facts.
An ELIGIBLE informational observation can retain entry=false. Nothing grants entry.

## Public API

`NotificationAudit(path, decisions=d2, clock=callable, clock_policy=...,
private_root=...)` requires a separate private SQLite path outside runtime/Git,
distinct from D1/D2 stores. Existing unrelated schema/clock policy fails without
migration. One connection per thread; standard-library dependencies only.

- `assess(key, evaluation_id, hypothesis_id, episode_id, event_type, recipients,
  policy, reason, predecessor_notification_id=None, resolution=None)` writes an
  eligibility decision and registers the immutable policy if new. Inputs after
  key are keyword-only.
- `generate(key, eligibility_id, message)` writes the exact structured/canonical
  message only for an ELIGIBLE assessment. It does not prepare or send anything.
- `prepare(key, notification_id, recipients, transport)` records an attempt
  reservation with ordinal, hash and retry-policy reference. Transport is only
  a declared `synthetic:` name, never an executable client.
- `delivery(key, attempt_id, event_type, evidence=None, recipients=...)` records
  a synthetic state transition. It never initiates delivery or probes a provider.
- `recover(key)` explicitly marks committed STARTED attempts with no recorded
  outcome UNKNOWN. It does not alter PREPARED attempts or authorize retry.
- `events(maximum_sequence=None)` and `projection(maximum_sequence=None)` return
  validated history or deterministic rebuilt state. `close()` releases SQLite.

Each write optionally accepts `before_commit` for offline fault-injection tests.
This is not a production callback and must not perform external side effects.
Same key/request replays the original result, including times/IDs; different content
under the same key raises a conflict. New recovery invocations need new keys.
Replaying historical results is not a fresh claim of current upstream availability.

## SQLite and event model

Tables: `d3_meta(schema, clock_policy)` and
`d3_events(seq PRIMARY KEY, key UNIQUE NOT NULL, body, hash)`.
UPDATE/DELETE triggers protect both. BEGIN IMMEDIATE serializes operation checks,
policy registration and event insertion; UNIQUE keys protect operation identity.
SQLite synchronous=FULL is used. Physical durability depends on the OS/storage.

Envelope fields: sequence, deterministic event_id/key, kind, schema/producer version,
timestamp, full clock provenance, causal predecessor event IDs, correlation,
request hash, complete payload/hash and predecessor hash. Each payload includes its
event ID, sequence and recorded_at_utc. All semantic fields survive replay comparisons.
Projection maps immutable policies, eligibility decisions, notifications and attempt
state/history. Mutable projections are built in memory from authoritative events.

Events:

- POLICY_REGISTERED; ALERT_ELIGIBILITY_ASSESSED.
- NOTIFICATION_GENERATED.
- DELIVERY_ATTEMPT_PREPARED; DELIVERY_ATTEMPT_STARTED.
- DELIVERY_ACKNOWLEDGED; DELIVERY_FAILED; DELIVERY_OUTCOME_UNKNOWN.
- DELIVERY_RECONCILED; RECOVERY_RECORDED.

Hash linkage/SQLite triggers detect ordinary corruption but are not tamper-proof
against administrators replacing history or rewriting all hashes. No authenticated
timestamp service, independent checkpoint authority or global clock is implemented.

## Exact upstream binding

D3 reads the named D2 evaluation and its named scenario under a D2 read snapshot.
It records D2 event ID/hash, evaluation hash and scenario hash. D2 technical_context
must equal the referenced full scenario. Missing evaluation is an error, never a
lookup of current/latest. The D1 prefix hash and sequence are verified under a D1
read snapshot; selected evidence is re-resolved at the exact frozen cutoff and must
match. Missing/corrupt bytes or changed prefix fails closed. No D1/D2 writes occur.

Eligibility and notifications retain evaluation/scenario IDs, D1 cutoff, manifest
fingerprint, instrument/timeframe, hypothesis ID/state/role, source timestamps,
policy and causation references. Generation and attempt preparation recheck the
upstream binding. Actual outcomes/recovery use the already recorded local attempt
history; recording an outcome does not require inventing newer upstream context.
There is no cross-database write or distributed atomicity claim.

The implementation uses accepted D2 projection/events and D1 integrity selection;
resolution attestations use D1's existing `_payload` reader, as D2 already does for
news. This private-helper dependency is documented and combined regression-tested;
a future public reader API requires separate review.

## Synthetic eligibility policy

The full policy is immutable under policy_id, including its versioned synthetic
retry policy. Reusing any retry policy_id with different contents also fails.
Required policy fields are classification=synthetic, `synthetic:` policy_id,
allowed_roles, allowed_events, allowed_states, require_entry_permission boolean,
episode_max_age_seconds and retry policy. No production defaults are provided.

Possible results:

| Outcome | Meaning |
| --- | --- |
| ELIGIBLE | This declared informational observation meets the synthetic policy |
| SUPPRESSED_DUPLICATE | No semantic change, including unchanged terminal context |
| DEFERRED | D2 action is WAIT; no generation permitted |
| INELIGIBLE | Role/event/state/episode or explicit permission requirement excludes it |
| UNKNOWN | Mandatory D2 knowledge is insufficient |

All decisions retain reasons and policy content, not just successful alerts.
Unknown D2 evidence does not manufacture permission. Conflicting news normally
retains D2 WAIT and thus DEFERRED; it cannot change direction or permissions.
Invalidated hypotheses cannot start ordinary alerts. Explicit terminal lifecycle
records are assessed by their resolution/expiry contract, not treated as trade entry.
An explicit policy may require entry=true; absence produces INELIGIBLE, never true.

## Lifecycle, materiality and deduplication

C3.3.5 concepts are retained: NEW, DEVELOPING, RESOLVED, EXPIRED are states;
MATERIAL_UPDATE is an event transitioning to DEVELOPING, not another persistent
state. D3 does not replace C3.3.5 fact verification, episode migration or notification
engine, nor claim full production policy equivalence. It audits declared synthetic
scenario-specific transitions using the narrower rules below.

Stream identity includes scenario_id, hypothesis_id and explicit episode_id.
Primary, nested and alternative histories are distinct; a nested bullish message
cannot overwrite/suppress primary bearish history. Semantic notification identity
includes the stream, frozen recipients, semantic context and lifecycle state; it is
not a title/text/price key. Policy/evaluation IDs alone do not establish materiality.

Semantic context retains the exact targeted hypothesis (including state/identity),
role, news status, and presence/value pairs for Point A, Point B, FTA, TA,
invalidation, liquidity objectives, HTF and market structure. Technical changes are
declared by the synthetic technical authority upstream; D3 does not infer verified
material market facts. Every assessment requires a supplied reason. Unchanged
context under repeated evaluation does not generate a notification. Message wording,
source count, raw reference-price ticks and changed evidence IDs alone do not qualify.

A material update requires the exact latest generated predecessor. It stores exact
before/after values and presence flags for every changed technical field, plus
hypothesis/status changes and the declared reason. Generation rechecks the predecessor
so two independently eligible assessments cannot race to generate duplicate messages.

Resolution requires an explicit reason from verified_correction,
documented_cancellation or authorized_manual and an exact selected D1 evidence
payload with a matching resolution attestation (verified=true, relation=resolves,
matching reason). These are synthetic upstream attestations, not independent source
authentication. Elapsed time/feed absence never resolves a fact. Expiry instead
uses the episode lifetime frozen at its first generated notification. No implicit
inactivity/cooldown or production freshness policy is invented here.

Terminal episodes cannot reopen. A later episode needs a new explicit ID, an exact
link to the latest terminal predecessor and changed semantic context. An unchanged
terminal snapshot is suppressed. A new episode cannot bypass an active predecessor
or silently change recipients. Within an episode, changing recipients is rejected
even between assessments. Subscription/backfill migration is not implemented.
After a terminal notification, old active notifications cannot prepare new attempts;
already recorded attempts can still record their factual outcomes.

## Recipient and generated-message contract

Recipient descriptor is exactly `{set_id: 'synthetic:...', fingerprint: SHA256}`.
It contains no actual addresses/chat IDs. It is an opaque caller attestation, not
proof of a real set's membership. D3 checks exact equality at assessment, linked
episodes, preparation and each transition; mismatches are atomic failures.
Publishing or hashing real recipients into public fixtures is outside scope.

`message` accepts supplied title, summary and optional action_labels only. D3
retains that original structured message and derives instrument/timeframe, optional
reference price, hypothesis role/direction, A/B, FTA/TA, invalidation, observation
reason, uncertainty, original technical permissions and provenance from the exact
assessment. Missing values stay null; the retained snapshot/presence flags preserve
whether a technical field existed. No fabricated price/target/action label is added.
The canonical JSON string and SHA-256 hash are stored with the complete content.
No Telegram formatting or rich-link behavior is implemented.

## Attempt state machine and retry

PREPARED -> STARTED -> ACKNOWLEDGED | FAILED | UNKNOWN.
UNKNOWN -> RECONCILED with explicit evidence/decision.

Stable delivery ID binds notification and recipients; attempt ID also binds ordinal.
Preparation stores the idempotency key, recipient fingerprint, transport name,
notification hash, eligibility ID and frozen retry policy. Every later event links
the prior attempt event and preserves its payload/recipient binding. Separate events
and timestamps retain preparation, start and completion/reconciliation, never one
overloaded 'sent' field.

ACKNOWLEDGED requires a synthetic provider acknowledgment ID. It means recorded
provider acceptance only. recipient_delivery_proven and recipient_read_proven remain
false; no provider claim is promoted to proof of recipient receipt/reading. FAILED
requires a failure classification. UNKNOWN requires an explicit uncertainty reason.
Missing acknowledgment never implies failure. Outcome metadata is declared synthetic
evidence, not a live provider query or authenticated external timestamp.

Retry has explicit synthetic max_attempts, allow_failed_retry and
allow_reconciled_unknown_retry booleans. PREPARED, STARTED, UNKNOWN and ACKNOWLEDGED
block a new attempt. FAILED may permit one under the policy. UNKNOWN first requires
reconciliation specifying provider_accepted, confirmed_not_accepted or still_unknown,
an evidence reference, explicit authorize_retry and (for retry) duplicate-risk
acknowledgment. provider_accepted cannot authorize retry. Even after reconciliation,
the policy and attempt limit must permit a new preparation. D3 never executes it
automatically. Exactly-once external delivery is not claimed.

## Clock and crash/restart boundaries

Synthetic clock requires aware UTC, session_id, monotonic, uncertainty_seconds and
health_id with an explicit maximum uncertainty policy. Reject negative/nonfinite
measurements, excessive uncertainty, local wall rollback, within-session monotonic
rollback and events earlier than their causal predecessors. Across sessions only
ledger sequence/local UTC checks are used; no global synchronization is claimed.

D1 availability, D2 creation/evaluation, eligibility, generation, preparation,
start, outcome and reconciliation remain distinct. Later market movement/corrections
do not rewrite earlier messages. Generation time is the actual synthetic recording
boundary, never inferred from an earlier market observation. Historical cutoff
tuples and source claims do not authenticate historical timeliness.

Each write is one SQLite transaction; first policy registration and eligibility are
atomic. Crash before commit leaves no partial event/projection; after commit the
original event survives. Explicit recovery changes committed STARTED attempts to
auditable UNKNOWN and appends a recovery summary atomically. PREPARED remains
PREPARED: no start can be inferred. Recovery does not send, reconcile or retry.
A process crash between recorded start and actual result remains uncertain.

## Verification and remaining decisions

Tests: `test_offline_notification_audit.py`; synthetic policy fixture:
`tests/fixtures/d3/policy.json`; synthetic scenario reuses the accepted D2 fixture.
No accepted tests are modified. Tested implementation SHA:
`b9f49d55d31468958d3a8c1f43a0f23fcf900abb`.
2026-10-21: D3 **57/57**, full offline regression **727/727** (670 existing + 57 new),
0 failures/errors/skips. CPython 3.12.10 64-bit, Windows-11-10.0.26200-SP0,
existing installed dependencies. This is the implementer's run, not independent
reviewer execution. HANDOFF records exact commands. The following commit updates
only these verification documents; implementation remains the tested SHA.

Coverage includes all 30 requested categories, linked terminal episodes, recipient
immutability, policy conflicts, preparation/generation rollback, real process exits
before/after start commit, concurrent duplicate preparation and complete five-prefix
replay with all IDs/timestamps/hashes/recipients/causal references preserved.
Transport absence is checked by a module import allowlist and rejection of a
nonsynthetic transport name; no real service is contacted by tests.

From an isolated checkout, use Python -B and an explicit private writable temp root:

```python
import tempfile, unittest
tempfile.tempdir = external_temp_directory
result = unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('.', pattern='test_*.py'))
assert result.wasSuccessful()
```

Limits: synthetic authorities and recipient attestations, no production provenance
verification, external retention dependency, and no approved production notification,
freshness, clock, recipient/backfill or retry policy. No clean-environment/Linux
verification or historical forecasting performance is implied. Real transport receipt
validation and recipient delivery/read evidence remain absent. D3 needs independent
review; no merge, D4 implementation or live operation is authorized by this contract.
