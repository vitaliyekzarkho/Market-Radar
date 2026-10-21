# D1 — Offline Evidence & Availability Ledger

Status: **D1 — ACCEPTED / CLOSED** (offline use, 2026-10-21)
Contract: `d1_v1`; date: 2026-10-21.
Accepted Block D main: `a812c9b32371fa8b8da0df92d977f7a0ed3e8a89`.
Branch: `feature/d1-offline-evidence-ledger`.

## Scope

Standalone Python standard-library SQLite implementation in
`offline_evidence_ledger.py`. No accepted module is imported or changed. This is
synthetic/offline infrastructure, not a trusted production acquisition service.
No D2 hypotheses, D3 notifications, D4 adapters, D5 monitoring, D6 transport,
market interpretation, collection, broker access or trading permission exists here.

## API and storage

`Ledger(path, clock=..., policy=..., private_root=...)` opens a dedicated database.
The root is explicit; repository and working Radar paths are rejected. Payload
references resolve beneath that private root, excluding runtime/repository paths.
An existing unrelated database or different policy/schema is rejected; no migration.
The caller owns private file access controls and retention. Do not put DBs, payloads,
archives or account data into Git. Tests create only synthetic temporary files.

- `ingest(acquisition_id, metadata, payload=bytes)` stores exact inline bytes.
  Alternatively `relative_path=...` stores a hash-verified private reference.
- `publish(version_id, recovery_id=None)` performs the separate READY transaction.
- `cutoff(as_of_utc)` captures a committed sequence under a serialized transaction.
- `select(source_id, item_id, cutoff)` returns ELIGIBLE with full version/READY, or
  UNKNOWN with explicit reason. No fallback to an older version on payload failure.
- `events()` validates the full hash chain and returns every semantic event.
- `projection(maximum_sequence=None)` deterministically rebuilds versions, receipts,
  READY, rejected events and recovery events. No mutable persisted cache is used.
- `close()` releases the connection. One connection per thread/process is required.

Schema: `d1_meta(contract, policy)` and
`d1_events(seq PRIMARY KEY, event_id UNIQUE, key UNIQUE, kind, body, hash)`.
The canonical JSON body contains all authoritative data. UPDATE/DELETE triggers
protect both tables. BEGIN IMMEDIATE serializes writers, including duplicate-key
checks; UNIQUE constraints provide an additional database-level barrier. SQLite
FULL synchronous commit is used. Database durability still depends on OS/hardware.

Each event contains sequence, deterministic event_id, key, kind, schema_version,
producer/version, timestamp/authority/missing reason, complete clock record,
causation/correlation references, payload, canonical payload hash and predecessor
hash. IDs and hashes are SHA-256 over exact/canonical representations. Canonical
JSON sorts keys and forbids nonfinite values. Events are STORED, RECEIVED, READY,
QUARANTINED, CORRECTED and RECOVERY. CORRECTED links predecessor and replacement;
it does not remove the older version. Schema has no destructive supersession.

Metadata preserves item_id, source_id, source_revision, schema_version, exact
content_hash, optional predecessor_version_id, instrument/timeframe/units/window,
published/observed timestamp envelopes, quality, validation_status and attestation.
Extra metadata is retained. Validation status `valid` means caller-declared input
validation, not proven source authenticity. The journal preserves the attestation
and its uncertainty rather than promoting it to a verified fact.

Version ID hashes metadata excluding the receipt envelope; repeated observation
with unchanged version metadata creates a RECEIVED event, not a new version.
The first stored version retains its initial receipt/acquisition/clock. Each later
receipt retains its own observed envelope. A new version of the same source item
requires predecessor lineage; forks are rejected, so a correction must extend the
latest stored version. Changes to version metadata, including publisher claims,
are new versions even if content bytes are unchanged. Repeating an acquisition key
with identical request is a no-op; a different request is an explicit conflict.
Conflicts leave the prior receipt intact. Content is verified even on replay.

## Transaction and recovery boundary

1. Ingest transaction validates timestamps, lineage and payload. It inserts STORED,
   optional CORRECTED, and RECEIVED atomically; then commits. Invalid ingestion
   writes a deduplicated QUARANTINED event with reason/request fingerprint and no
   eligible version. An unavailable validated event clock remains null/UNKNOWN.
2. Publish transaction rechecks the persisted payload and synthetic clock. Only
   validation_status=valid may publish. A predecessor must already be READY before
   its correction, preventing late publication of an ancestor from resurrecting it.
3. Assign available_at_utc at this publication boundary, after the storage commit.
   Insert READY (plus RECOVERY when explicitly requested) and commit. The method
   returns only after commit. The timestamp is sampled before READY commit, so it
   alone does not establish visibility; committed sequence cutoff is mandatory.

No other connection can read uncommitted READY. Failure before commit rolls back
the entire publish transaction, including RECOVERY. A process exit after storage
leaves unavailable data; recovery explicitly republishes with the current clock,
never an old observation/publication timestamp. After READY commit, restart retains
the original availability. Concurrent duplicate publish returns the same committed
READY; it does not allocate a second event. The optional `before_commit` callback
is an offline fault-injection seam, never a production transport hook.

Clock/validation failures during publication raise and leave state unchanged;
they are not falsely recorded with a trusted timestamp. Ingest quarantine provides
the rejected-evidence journal. Invalid API shapes and acquisition conflicts are
exceptions, not accepted evidence or automatic retry instructions.

## Time and cutoff semantics

Timestamp input requires an explicit zero-offset timezone; naive, non-UTC and
malformed timestamps are rejected. Original claimed timezone/precision/authority
and supplied uncertainty are retained in metadata. Publication can be null with
an explicit missing_reason. Receipt cannot be missing. Publication <= receipt <=
current synthetic clock; observation window start <= end <= receipt. Scheduled
future event times must be a separate metadata field, not future publication.
The conservative check does not use uncertainty to allow future source claims.

Injected clock fields: at_utc, session_id, monotonic, uncertainty_seconds, health_id.
The policy must explicitly declare classification=synthetic, a `synthetic:` ID and
max_uncertainty_seconds. No production tolerance/freshness default is provided.
Reject wall-clock rollback relative to the last accepted event, excessive/invalid
uncertainty, and monotonic rollback within a session. A new session may restart its
monotonic counter. Ledger sequence, not cross-boot monotonic time, orders history.

Cutoff is `(as_of_utc, maximum_visible_ledger_sequence)`. Capturing a future cutoff
is rejected. Queries reject an uncommitted/future sequence and project only the
recorded prefix; READY must also have available_at <= as_of. Among eligible versions,
the latest READY sequence wins. Equal UTC timestamps retain sequence ordering.
Stored versions without READY in the prefix report AVAILABILITY_UNKNOWN_AT_CUTOFF;
later availability reports AVAILABLE_AFTER_AS_OF. Unseen items return
NO_ELIGIBLE_VERSION. Later events are not added to earlier exclusion lists, keeping
complete prior results stable. Cutoff tuples belong to this ledger; they are not
portable authenticated tokens or proof that an external decision actually occurred.

Legacy availability remains UNKNOWN. New ingestion/READY can establish only local
availability now. No file mtime, publication, candle close or old report substitutes
for availability. Adding a later market outcome cannot alter an earlier prefix.

## Payload integrity and provenance limits

Inline payload bytes are immutable in the authoritative event. External references
are rehashed at ingestion, publication and selection. Missing or changed selected
payload yields PAYLOAD_UNAVAILABLE_OR_CORRUPT, never a silent older replacement.
External deletion can make a previous selection unreplayable; immutable external
retention remains the caller's obligation. It cannot change the recorded history.

Attestation requires verification_method, claimed_source_identity, lineage and
uncertainty. `independent=True` remains an upstream claim. Redistribution lineage
is retained without computing corroboration. No production source authentication
or causality inference is performed. Hash verification proves bytes, not publication
time. Append-only triggers/hash chaining are not tamper-proof against a database
administrator who can remove triggers and rewrite all hashes or truncate history.
Independent trusted checkpoints and acquisition authorities remain future work.

## Verification

Synthetic suite: `test_offline_evidence_ledger.py`, with
`tests/fixtures/d1/evidence.json`. Tests cover all 18 requested case categories,
complete semantic replay equality, concurrent publication, two separate-process
crashes around READY commit, rollback/isolation, lineage forks, external payload
loss/corruption and journal tampering. Full regression on 2026-10-21:
**603/603 passed**, 0 failures/errors/skipped (559 existing + 44 D1).
Exact tested implementation SHA: `1c78c9f9bb10b4b995e042b78903f2ac7099339d`.
The following commit updates documentation only. HANDOFF records the exact command.

Environment: Windows, Python 3.12.10, existing installed dependencies. New module
requires only Python standard library. Tests use an explicit writable temporary
directory outside every Git repository because sandbox-default temp was inaccessible
to SQLite. No dependency installation or clean-environment/Linux verification claimed.

Commands (run from isolated checkout, supply your own private temp root):

```python
import tempfile, unittest
tempfile.tempdir = external_temp_directory
suite = unittest.defaultTestLoader.discover('.', pattern='test_*.py')
result = unittest.TextTestRunner().run(suite)
assert result.wasSuccessful()
```

Use Python `-B`. Focused run substitutes
`unittest.defaultTestLoader.loadTestsFromName('test_offline_evidence_ledger')`.

## Acceptance and exclusions

Independent code review APPROVED for HEAD `82577797a8d9cbc623edc7a5f7ad6e9d3e7acc54`.
Implementation and tests are accepted unchanged. The 603/603 result is the
implementer's Windows/Python 3.12.10 run, not independent test execution.
No production clock/source/freshness policy or runtime approval is inferred.
Scenario versioning, eligibility, notification generation/delivery and live adapters
remain D2-D6. C3.3.6 historical/XAUUSD remains INSUFFICIENT_EVIDENCE; no historical
forecast timeliness, trading performance or live readiness is established.
