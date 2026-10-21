# D2 — Immutable Scenario & Decision Ledger

Status: **IMPLEMENTED / AWAITING INDEPENDENT REVIEW**
Contract: `d2_v1`; date: 2026-10-21.
Base: accepted D1 main `2465cb7532c0be3dff259d48eb22ea78a56638e5`.
Branch: `feature/d2-scenario-decision-ledger`.

## Scope and authority

`offline_scenario_ledger.py` is a separate offline SQLite journal. It imports D1
canonical/hash/time utilities and reads D1 evidence; it does not alter D1 tables,
interfaces, engines or tests. There is no technical scenario calculation, live
adapter, hypothesis inference, alert generation, eligibility service, transport or
trading operation. Snapshots and technical transitions are explicitly supplied by
a synthetic technical authority with an engine version. This is not authentication
of a real technical engine. Unknown fields and permissions are never normalized away.

## Public API

`ScenarioLedger(path, evidence=d1_ledger, clock=callable,
clock_policy=synthetic_policy, private_root=...)` requires a new dedicated private
database outside repositories/runtime and separate from the D1 database. Existing
unrelated stores/schema/policy mismatches fail; no migration. One connection per
thread is required. It uses only Python standard library and accepted D1 helpers.

- `record(key, kind, scenario_id, scenario, cutoff, refs, authority,
  predecessor_version_id=None, hypothesis_id=None)` records one immutable snapshot
  and its technical lifecycle event atomically; keyword arguments are required.
- `evaluate(key, scenario_version_id, policy, engine_version, news=None)` records an
  evaluation of exactly that version's original cutoff. To evaluate newer inputs,
  create a new explicit scenario version, even if the technical payload is unchanged.
- `events(maximum_sequence=None)` returns validated ordered authoritative events.
- `projection(maximum_sequence=None)` reconstructs versions, latest-version index,
  evaluations and immutable policy registry from the journal.
- `close()` releases the connection.

Both write APIs have an optional `before_commit` fault-injection callback for
offline crash tests. It is not an integration hook and must not perform side effects.

## Persistence and immutable identities

Tables: `d2_meta(contract, clock_policy)` and
`d2_events(seq PRIMARY KEY, key UNIQUE, body, hash)`.
UPDATE/DELETE triggers protect authoritative rows. BEGIN IMMEDIATE serializes
duplicate checks/writes and UNIQUE keys enforce operation identity. FULL synchronous
commit is used; actual durability still depends on storage hardware/OS behavior.
No mutable persisted cache is authoritative. Restart requires no replay-time rewrite.

Event envelope: sequence, deterministic event_id, operation key, kind, schema and
producer versions, timestamp, complete clock record, request hash, correlation,
causation, complete payload/hash and predecessor hash. All semantic IDs, timestamps,
ordering and fields remain in replay comparisons. There is no normalization that
drops substantive differences. Hash chains/triggers are not tamper-proof against
an administrator rewriting the database; no trusted historical clock is claimed.

Same operation key and canonical request returns the original stored result without
new events or a new timestamp. Different request under that key raises a conflict.
Version/evaluation IDs hash their complete immutable payloads, including actual
recording/evaluation time. Repeating the same fixture and injected clocks gives the
same IDs. A new operation at a later time is not the old evaluation replay.

## Scenario and hypothesis contract

Required structure: instrument, timeframe, direction, permissions, primary object,
nested list and alternatives list. Every hypothesis has a unique stable id, direction
and optional parent_id. Parent references must exist and be acyclic. Primary direction
must equal the snapshot direction. Full original JSON payload and its hash are stored:
Point A/B, price/time/definitions, HTF, structure, FTA/TA, liquidity objectives,
invalidation, scores/confidence and all extensions survive without mutation.
Supplied Point A/B times must be aware UTC and not later than the D1 cutoff.
Missing optional technical fields are not reconstructed or inferred.

The journal stores scenario_id, version_id, predecessor, target hypothesis_id,
technical authority/engine version, created_at, exact D1 cutoff and full manifest.
Direction changes or changing the primary ID require explicit
direction_change_authorized=True from the technical authority. Parent changes require
topology_change_authorized=True. These are synthetic declarations, not authenticated
permissions. Existing IDs, including invalidated hypotheses, cannot disappear.
A bullish nested candidate never automatically replaces a bearish primary.

### Review correction — new-ID lifecycle integrity

Independent review of HEAD `43585e68d90ee75e91970ef748889f97bc21f70b`
found that a revision/invalidation targeting an existing hypothesis could silently
introduce other new IDs. The correction compares the complete incoming and previous
ID sets for every subsequent scenario version: `new_ids = incoming_ids - previous_ids`.
No new IDs permits normal revision/invalidation. Exactly one new ID requires
HYPOTHESIS_FORMED targeting that exact ID. More than one new ID is rejected; each
requires its own formation event. FORMATION cannot target an existing ID, and
existing IDs (including invalidated ones) cannot be removed. Failure appends no
event and leaves the complete projection unchanged. No formation event or timestamp
is generated automatically. Initial OBSERVED_EXISTING still permits multiple legacy
hypotheses with UNKNOWN formation times; later versions enforce the invariant.
Initial genuinely declared formation remains distinct from legacy observation.

Lifecycle events:

- HYPOTHESIS_FORMED: first declared formation, or a newly identified hypothesis in
  a later scenario version. An existing hypothesis cannot be declared newly formed.
- HYPOTHESIS_REVISED: extends the latest version with an existing target identity.
- HYPOTHESIS_INVALIDATED: extends the latest version and requires the targeted
  hypothesis to explicitly have state=invalidated in the supplied technical snapshot.
- SCENARIO_OBSERVED_EXISTING: initial legacy observation with unknown formation time.

First version must be FORMED or OBSERVED_EXISTING. Later versions must reference
the latest predecessor; forks and cutoff rollback fail. hypothesis_history retains
each ID's first observation and independently known formation. Only the explicit
formation target receives a formation timestamp; other observed candidates remain
UNKNOWN until a separately supported history exists. Legacy primary formation is
never inferred from a new nested hypothesis or revision. D2 does not backfill history.

## Frozen D1 evidence

refs explicitly contains five roles: technical, news, usd, yields, volume. Each is
null (explicit missing marker) or a source_id/item_id selector. Resolution happens
inside a D1 read transaction using the exact
(as_of_utc, maximum_visible_ledger_sequence). It never asks for current/latest data
outside that cutoff. Future/uncommitted sequences are rejected. Every manifest
stores selectors, complete selected version/READY objects or exclusion reasons, and
the hash of the exact D1 prefix. A different or rewritten prefix is rejected.

D2 cannot prove the caller captured a cutoff at a historical decision instant;
D1 cutoff tuples are not authenticated tokens. Actual D2 creation/evaluation time
is separate and cannot be backdated before cutoff or the previous D2 event. Late
recording of old evidence is not proof of an early forecast. D1 source authenticity
and synthetic clock limitations remain in force.

Evaluation re-resolves only the stored cutoff. Later corrections, changed publisher
claims and future market outcomes cannot substitute new inputs. It records both
original_manifest and current integrity assessment at that same cutoff. If an
external payload is lost, the original selected version remains in the historical
manifest, and the new assessment is UNKNOWN; there is no fallback. Replaying an
already committed evaluation returns its original historical record, not a fresh
claim that its external payload is still readable.

## News isolation

news is either absent or a wrapper:

```json
{"evidence_version_id": "selected D1 version", "interpretation": {"status": "supports", "explanation": "synthetic"}}
```

The interpretation must exactly match the JSON payload stored in that selected D1
news version, including unknown fields. D2 reuses D1's payload integrity helper to
read those bytes; it never fetches a replacement. This is an explicit dependency
on D1 `_payload`, covered by combined tests; a future D1 public read API would require
its own review. The wrapper avoids a self-referential version hash in payload bytes.

Supported statuses are supports, conflicts, insufficient_data and not_evaluated.
Missing/stale optional news yields neutral not_evaluated. No new interpretation may
be attached to historically old raw evidence unless that interpretation itself was
available as the selected D1 payload. A mismatch fails the transaction. News data
is stored separately and never merged into technical_context or permissions.

## Synthetic policy and outcomes

Every policy has classification=synthetic, a unique `synthetic:` policy_id,
wait_on_conflict boolean and explicit rules for all five roles. Each rule supplies:
source_id, accepted_verification_methods, required boolean, max_age_seconds and
age_anchor (observed, published, observation_end or available). No defaults or
production thresholds are supplied. Same policy ID with different content fails.
Policy registration and evaluation commit atomically.

Age is measured relative to the scenario's frozen as_of time, not the later replay
clock. A missing anchor, negative age, stale input, source mismatch or unacceptable
attestation method produces an explicit reason. Ingestion time must not be used to
rejuvenate old market observations; choose the appropriate anchor explicitly.
Accepting an unverified method in a synthetic policy does not verify the source.

- knowledge=UNKNOWN: mandatory input/provenance/interpretation is insufficient.
- knowledge=KNOWN: the supplied synthetic policy's mandatory checks passed; this
  does not mean real-world truth or independent source verification.
- action=WAIT: defer further integration because mandatory knowledge is missing,
  primary is technically invalidated, or conflict policy requires waiting.
- action=RECORDED: offline evaluation recorded; not alert eligibility or permission.
- technical_context.permissions: exact PA/HTF snapshot, unchanged in every case.

Conflicting news can therefore yield KNOWN + WAIT. A missing optional input can
yield KNOWN + RECORDED while retaining its own UNKNOWN assessment. Invalidating a
nested candidate does not invalidate the primary. No D3 notification event exists.

Every evaluation contains scenario version ID, original cutoff, manifests, complete
technical context, separate supplied interpretation/status, policy content/hash,
engine version, evaluated_at_utc, per-role age/knowledge, action, reasons and
uncertainty details. The evaluation never changes its source snapshot.

## Clock, transactions and recovery

Clock fields match D1: aware UTC, session_id, monotonic, uncertainty_seconds and
health_id. An explicit synthetic clock policy limits uncertainty. Reject invalid
clock measurements, wall rollback, within-session monotonic rollback and cutoff
later than evaluation/creation. Monotonic time is not compared across sessions.

A scenario snapshot/event is one transaction. Evaluation plus first policy
registration is one transaction. No cross-database writes occur: D1 is read and
immutable prefix hashes establish the link; there is no distributed transaction
claim. A crash before commit leaves neither partial evaluation nor policy record;
after commit both survive. Duplicate concurrent evaluation creates one event.
Projection rebuild uses the original event bytes and does not recalculate decisions.

## Verification and limitations

Lifecycle correction tested SHA: `822c6cadcbb48e3bbbf9b41c56f4fa1c79b0efd9`.
2026-10-21: focused lifecycle **14/14**, complete D2 **67/67**, full offline
regression **670/670**, all with 0 failures/errors/skips. CPython 3.12.10 64-bit,
Windows-11-10.0.26200-SP0, existing dependencies. Exact commands are in HANDOFF.
All new rejection tests compare the entire journal and projection before/after;
valid formation remains idempotent after restart. Accepted D1 is unchanged.
These fresh results supersede the earlier implementation results below for review
of the correction. Status remains IMPLEMENTED / AWAITING INDEPENDENT REVIEW.

Synthetic fixtures: `tests/fixtures/d2/scenario.json` and inline synthetic D1 inputs.
Earlier implementation tests: `test_offline_scenario_ledger.py` (53 tests).
Tested implementation: `7c1091c6e002099612369e654fae781c1d786a45`.
Full offline regression on 2026-10-21: **656/656 passed**, 0 failures/errors/skipped
(603 accepted + 53 D2), CPython 3.12.10 64-bit, Windows-11-10.0.26200-SP0,
existing dependencies. Implementer run, not independent execution. HANDOFF records
the exact command. The following commit changes only verification documentation.

Coverage includes formation/revision/invalidation/legacy observation; independent
nested formation; full original/unknown-field preservation; competing direction;
mandatory/optional gaps; stale USD/yields/volume; source identity/provenance rules;
future evidence/technical points; frozen interpretation bytes; exact permissions;
same-time ordering; request/policy conflicts; concurrent replay; atomic rollback;
real process crashes before/after commit; restart rebuild; external payload loss;
deterministic complete replay and independently executed historical prefixes.

Run from the isolated checkout with Python -B, supplying an explicit writable temp
directory outside Git repositories:

```python
import tempfile, unittest
tempfile.tempdir = external_temp_directory
result = unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('.', pattern='test_*.py'))
assert result.wasSuccessful()
```

No accepted code/tests or production policies change. D2 does not authenticate the
technical authority, recalculate PA/HTF, establish causality, provide notifications
or prove trading performance. C3.3.6 historical/XAUUSD remains INSUFFICIENT_EVIDENCE.
Production source trust, retention, freshness, clock policy and any D3-D6 integration
remain separate review/authorization decisions. Stop for independent D2 review.
