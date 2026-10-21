# Block D — Controlled Integration Plan

Status: **PLANNED / AWAITING REVIEW**
Date: 2026-10-21
Accepted main: `5bf112b1648ac6186399e93edcd9a53dc8ca642e`
Scope: technical contracts and proposed execution sequence only. No implementation
or live deployment is authorized by this document.

## 1. Baseline and boundaries

C3.3.6 is ACCEPTED / CLOSED. Implementer result: 559/559, Windows/Python
3.12.10, 2026-10-20; not independently rerun for this documentation change.
Synthetic replay passed 19 cases and 9 invariants. Historical/XAUUSD:
INSUFFICIENT_EVIDENCE. Neither historical forecast timeliness nor trading
performance is established. Synthetic attestations are not production provenance.

Proposed flow:
immutable evidence versions -> immutable technical scenario versions ->
as-of evaluation/decision events -> eligibility event -> notification intent ->
delivery attempt -> transport outcome/reconciliation.

PA/HTF remains the sole authority for technical direction and permissions.
News is a separate explanation with supports/conflicts/insufficient_data/
not_evaluated; it cannot grant permission, reverse direction, replace a scenario,
or promote legacy confirmed_driver into independently demonstrated causality.
UNKNOWN describes unavailable knowledge; WAIT describes a deferred integration
action. Neither overwrites the technical scenario's existing permissions.
An informational alert is never an order or trading authorization.

No edits to accepted engines, run_radar.py or run_cycle.py are proposed here.
The working C:\Users\User\MarketRadar directory is outside the execution scope.
Initial milestones operate on synthetic fixtures in a separate checkout/database.
Strategic product philosophy remains in Notion.

## 2. Timestamp contract and authority

All instants use timezone-aware UTC with retained original source representation,
precision, authority, uncertainty and clock-health reference. Naive, malformed or
unexplained future timestamps are quarantined, not silently converted. A missing
timestamp is null plus a reason, never another field's fallback.

| Field | Authority and meaning | Validation |
| --- | --- | --- |
| published_at_utc | Publisher's claim about this exact content version | Retain claimed value, source timezone and revision identity. Publication is not system availability. Future claims outside known uncertainty require quarantine. Scheduled event time is a separate field. |
| observed_at_utc | Trusted acquisition boundary, after successful receipt of this exact payload | Record completion, not polling-start time; hash bytes and retain acquisition/run ID. Re-observation is a new receipt event. |
| available_at_utc | Ledger publication boundary after validated bytes are durably stored | Cannot precede receipt or dependency readiness; never inferred from publication, file mtime, candle close or report time. Only committed READY records can be selected. |
| evaluated_at_utc | Evaluator completion clock | Fixed as_of cutoff precedes completion; exact selected dependency versions and policy recorded. |
| created_at_utc | Local creation of this artifact/event | Immutable creation time, not historical first-seen time. An imported legacy object is created now. |
| eligibility_decided_at_utc | Completion of explicit alert eligibility decision | Links scenario, evaluation, policy and as_of cutoff. Eligibility does not imply a notification exists. |
| notification_generated_at_utc | Durable notification-intent creation boundary | Preserve message/template version and semantic key. No inference of delivery. |
| attempt_recorded_at_utc | Durable attempt reservation before external invocation | Crash after reservation may leave outcome unknown, including whether invocation happened. |
| dispatch_started_at_utc | Local transport invocation observation, when available | Distinct from reservation; absence does not prove no send. |
| transport_ack_observed_at_utc | Local observation of authoritative provider receipt | Store opaque receipt privately; distinguish provider-claimed time from local observation. |
| delivery_confirmed_at_utc | Durable acceptance of a validated transport receipt | Means transport confirmation only, never human reading or successful trading action. |

Also retain market event time/window, candle open/close, source observation period,
and source correction time as separate domain fields. A pre_event_hypothesis
may precede the scheduled event; post_event interpretation must meet its event
chronology. Future validation cannot support either mode.

### Publication barrier and as-of queries

Use two phases: durable evidence payload storage, then a serialized READY event.
Assign available_at at the READY publication boundary after payload commit
acknowledgement. Readers see it only after the READY transaction commits.
Every decision captures an atomic cutoff tuple
(as_of_utc, maximum_visible_ledger_sequence), not UTC alone. A READY timestamp
alone does not prove visibility before its commit: the sequence cutoff is mandatory.
After a crash between phases, stored-but-not-READY evidence remains ineligible.
Recovery publishes a new READY event at recovery time, never backdates it.

Selection requires READY sequence <= cutoff sequence AND available_at <= as_of,
plus source-specific validity and freshness. Derived artifacts require every
dependency eligible at the same cutoff; output availability cannot precede
completion or dependencies. Keep unavailable/rejected candidates in a separate
audit record, without feeding them to the evaluator. Equal UTC timestamps use
ledger sequence, not filename order. Corrections never rewrite earlier cutoffs.

Clock records include wall UTC, monotonic counter, boot/session identity, measured
offset/uncertainty and clock-health policy version. Monotonic counters order a
single boot, not different hosts. Clock rollback, unknown synchronization or
excessive uncertainty yields UNKNOWN/WAIT for time-sensitive decisions.
Do not repair old timestamps by shifting them. Across restarts/hosts, use ledger
sequence and explicit uncertainty; no fabricated global clock precision.

## 3. Evidence versions and provenance

Proposed records (schema names are design contracts, not implemented tables):

- evidence_version: immutable ID, logical source item ID, source revision ID,
  content hash, schema version, private payload locator, source/provider identity,
  instrument/timeframe/units, observation window, acquisition ID, timestamps,
  quality flags, predecessor/correction relation and authenticity evidence.
- evidence_ready: evidence version, validation report, publication sequence,
  availability authority and clock-health reference.
- evidence_edge: exact parent/child versions and transformation role.
- policy_version: immutable serialized policy and hash, engine/schema versions,
  approved scope and approval reference.
- source_attestation: verification method, original publisher/redistribution
  lineage, independence basis and uncertainty. independent=True alone is not proof.

Every scenario decision links an input manifest containing exact technical candle/
indicator/context versions, news story/event/interpretation/validation versions,
USD evidence, yields and volume inputs, or explicit missing markers for each.
Store source semantics: USD breadth proxy is not DXY; daily yields are not intraday
rates; tick volume is not exchange traded volume. Closed candles require close
time, observed closed state and actual version availability. Later candle revisions
produce later versions. Do not silently replace missing DXY/rates with proxies.

Hashes identify bytes and detect changes; they do not authenticate publisher claims
or independently prove historical time. A hash chain alone is not a trusted clock.
Keep payloads and recipient mappings private outside Git; public tests use synthetic
IDs. Evidence resolution must fail explicitly if payload is unavailable or its
hash mismatches, rather than fetch a current replacement.

Legacy imports retain original claimed timestamps and historical availability
UNKNOWN. Import-time availability only permits present evaluations; it cannot
certify an earlier forecast. Reconstructed/replayed hypotheses are labelled replay,
never original live decisions.

## 4. Immutable technical scenario and hypothesis versions

scenario_version includes stable scenario_id, unique version_id, predecessor ID,
canonical full original payload/hash, schema and technical-engine version,
instrument, technical as_of cutoff and exact input manifest.

Preserve without loss: Point A and Point B (price/time/definition), direction,
score, primary hypothesis, nested hypotheses, conditional alternatives, stable
hypothesis IDs and their relationships, HTF context, technical permissions,
FTA/TA targets, invalidation conditions and any source fields unknown to the
integration schema. Missing required fields remain explicit UNKNOWN; never invent
A/B or infer permission from prose. Preserve raw representation as well as
normalized fields so normalization does not erase source distinctions.

Formation and revision are separate append-only events. First observation of an
existing legacy scenario is OBSERVED_EXISTING with unknown formation time.
Only an authoritative formation event can establish first formation. Revisions
reference old and new versions with reasons and evidence manifest. A nested
bullish candidate does not overwrite a bearish primary; a conditional alternative
is not an activated reversal. Activation/invalidation requires the technical
authority's own explicit version/event. News revisions create new explanations,
not fabricated technical revisions.

An evaluation references one immutable scenario version plus one news assessment
version. Tests compare the full original scenario across absent, supporting,
conflicting and insufficient news. Direction, score, targets, A/B, invalidation
and permissions must remain exactly equal or canonically equal with a documented
field-specific normalization. Unknown fields must survive round trips.

## 5. Evaluation, staleness and eligibility

An evaluation event stores evaluation_id, scenario_version_id, as_of tuple,
input manifest, policy hash, engine revision, evaluated_at, explanation,
quality reasons and outcome. Separate news status from overall knowledge state.

Policies must specify required/optional evidence by evaluation type and source,
max_age_seconds, age anchor, market-session/calendar handling, permissible clock
uncertainty, candle finality and correction handling. No production numeric
defaults are approved here. Publication age, availability age and underlying
market-observation age are distinct; fresh ingestion cannot rejuvenate stale data.

| Condition | Required integration behavior |
| --- | --- |
| Missing mandatory input or unknown availability | UNKNOWN knowledge; WAIT eligibility with reason |
| Stale required input | WAIT; preserve technical version |
| Optional news absent/stale | not_evaluated or insufficient_data with explicit reason; no technical change |
| Conflicting news/technical evidence | Record conflicts, retain both hypotheses and inputs; never auto-reverse |
| Invalid provenance or clock | Quarantine input; WAIT for decisions requiring it |
| Duplicate source redistribution | Preserve lineage; no extra independent corroboration |
| Late correction | New version and later evaluation; earlier decision stays unchanged |

Eligibility is its own event (ELIGIBLE, INELIGIBLE, WAIT) under a versioned policy.
It does not imply the technical trade is allowed. Required-input rules determine
whether a technical-only informational notification is permitted without optional
news; this must be approved per notification class. Record no-change evaluations
and WAIT outcomes, not only changed alerts, to avoid history gaps.

## 6. Append-only decisions and notifications

Authoritative event envelope: event_id, event_type, ledger_sequence, aggregate ID,
schema version, created_at, clock record, producer/code revision, correlation/
causation IDs, canonical payload hash and previous-record hash. Enforce insert-only
source events, unique idempotency keys and schema validation. Corrections are
compensating events; mutable projections are rebuildable and non-authoritative.
Storage permissions, backup/restore integrity and independently retained checkpoint
hashes strengthen auditability; this is not a claim of tamper-proof storage.

Distinct event types include HYPOTHESIS_FORMED, HYPOTHESIS_REVISED,
SCENARIO_OBSERVED_EXISTING, EVALUATION_COMPLETED, ALERT_ELIGIBILITY_DECIDED,
NOTIFICATION_GENERATED, DELIVERY_ATTEMPT_RECORDED, DISPATCH_OBSERVED,
DELIVERY_CONFIRMED, DELIVERY_FAILED, DELIVERY_UNKNOWN and RECONCILIATION_DECIDED.

Generation creates a durable outbox intent. Semantic identity includes scenario/
hypothesis and material revision, notification class, policy version and frozen
recipient-set/subscription version. Retries of one input with changed recipients
must reject explicitly, preserving the C3.3.5 contract. Later subscribers require
an explicit new subscription/backfill decision, never an implicit retry.
Do not use policy changes alone to generate repeated alerts without a documented
migration/backfill rule.

The D ledger supplements accepted C3.3.5 state rather than silently migrating its
tables. An offline bridge maps exact C3.3.5 input/intent/attempt IDs to D events.
Atomic local outbox creation and ledger append, or a durable idempotent bridge
with reconciliation, is required before any runtime use. Choose and test one
transaction boundary during D3 review; cross-database atomicity is not assumed.

## 7. Restart, idempotency and transport uncertainty

Unique keys cover acquisition version, scenario revision, evaluation cutoff/policy,
notification intent and attempt number. Same key/same payload is a replay;
same key/different payload is a conflict requiring quarantine. Use a single writer
or explicit transactional concurrency control and monotonic ledger sequence.
Replay must preserve semantic IDs, timestamps and decisions, not regenerate them.

Crash tests cover pre-commit rollback, stored-but-not-READY, ready-before-evaluation,
intent-before-attempt, reserved-attempt-before-call, provider-success-before-local-
receipt and receipt-before-projection. Unknown send outcome remains UNKNOWN.
No automatic retry after uncertainty: require explicit reconciliation and duplicate
risk acknowledgement as in C3.3.5. Late receipts link to their original attempt.
A confirmed failure permits retry only under an approved retry policy.

Exactly-once external delivery is not promised. Provider acceptance does not prove
recipient reading. Persist a recovery audit event, verify schema/checkpoint integrity,
rebuild projections, and require clock-health restoration before eligible activity.

## 8. Establishing before/after movement

A later analysis record links original scenario/evaluation/intent/attempt/receipt
IDs to independently versioned market evidence and an approved movement definition:
instrument/feed, price basis, threshold or target-touch definition, window,
granularity, and uncertainty. Predefine evaluation rules; retain rule versions.
A quoted PWL target is not proof of a touch.

Classify formation, revision, generation, dispatch and transport confirmation
separately as BEFORE / AFTER / INDETERMINATE relative to the movement interval.
BEFORE requires the event's latest possible time to precede the movement's earliest;
AFTER requires its earliest to follow the movement's latest. Overlap, missing
timestamps, unauthenticated history or coarse candles yield INDETERMINATE.
Preserve event-time and availability-time separately. Future outcome evidence may
enter this later analysis only, never the earlier decision manifest.
Timeliness does not demonstrate profitability or causality.

## 9. Read-only compatibility boundary

D1-D3 have no runtime adapter, network client, Telegram transport or broker import.
Use explicit fixture roots and separate private ledger paths; reject runtime paths.
No defaults discover the working Radar directory.

Any later D4 adapter needs separate authorization and an approved snapshot contract.
Read only approved exports/snapshots; require a source generation ID/manifest for
cross-file consistency. If unavailable, declare consistency UNKNOWN and WAIT.
Do not infer atomicity from individually successful reads. Do not open a live SQLite
file with immutable assumptions or change its journal mode; use an approved stable
snapshot. Output goes only to the new ledger, never to source files/state.

Shadow comparison must show original PA/HTF results and permissions unchanged.
No callback into run_radar.py/run_cycle.py, no feedback to technical selection,
no MT5 connection, order API or Telegram call. Runtime acquisition and any eventual
notification transport require later, separate review and explicit authorization.

## 10. Small reviewable milestones

| Milestone | Deliverable | Acceptance gate |
| --- | --- | --- |
| D1 — Offline evidence ledger | Schema, READY barrier, provenance, clocks, append-only records and deterministic synthetic fixtures | Prefix cutoff, missing availability, late correction, same-time ordering, rollback, crash recovery and hash mismatch tests; no runtime imports |
| D2 — Offline scenario/decision ledger | Full scenario versions, hypothesis lineage, evaluation and policy/eligibility contracts | Full scenario preservation, primary/nested distinction, UNKNOWN/WAIT, source-specific staleness and no-future-input tests |
| D3 — Offline notification audit bridge | C3.3.5 ID mapping, transaction strategy, outbox/attempt/receipt events and simulated transport | Duplicate/recipient conflicts, all crash windows, unknown no-retry, replay/projection rebuild and before/after uncertainty tests |
| D4 — Read-only shadow boundary | Separately approved snapshot adapter and compatibility report | Explicit authorization first; consistent snapshots, read-only source hashes, isolation and zero transport/broker calls |
| D5 — Controlled shadow assessment | Separately authorized observation protocol and audited as-of evidence coverage | Review coverage/clock/provenance gaps and comparison results; no alerts sent, no trade control |
| D6 — Optional controlled notification transport | Separate design/review and explicit opt-in authorization | Approved recipients/privacy/retry/freshness policy, kill switch, canary scope and reconciled receipts; no automated trading |

Each milestone uses its own feature branch and independent review. D1-D3 can be
implemented only after this planning contract is accepted. D4-D6 are future gates,
not authorization in this plan. No automatic progression from passing tests.

## 11. Failure modes and acceptance evidence

Acceptance must include deterministic synthetic replay, as-of prefix equivalence,
restart after every durable boundary, concurrent duplicate reservation, failed
transaction atomicity, clock rollback/uncertainty, malformed time, future evidence,
late source corrections, stale market data freshly received, missing proxy metadata,
recipient-set changes, unavailable payloads and tampered hashes.

Include a negative test proving a late-imported historical record cannot become
an early original decision. Include a test proving future market outcome changes
neither earlier evaluation nor eligibility. Compare all semantic ledger fields.
Record exact tested SHA, environment, commands, test counts and fixture hashes.
Keep synthetic, archived historical, shadow and transport evidence separate.
No historical success claim until original trustworthy evidence exists.

## 12. Decisions required before later execution

Independent review must approve this schema and milestone scope. Before runtime,
product/technical owners must approve:
- Required versus optional inputs per informational notification class and behavior
  when news, yields, USD or volume is missing/conflicting.
- Source-specific freshness/market calendars, max_age_seconds, time uncertainty
  bounds and publisher/source-independence trust policy.
- Movement/target-touch definitions and evaluation windows for timeliness analysis.
- Private ledger location, access, retention/backups and payload deletion policy;
  deletion must leave an explicit tombstone/availability limitation, not false replayability.
- Recipient subscription/backfill rules, transport confirmation semantics,
  retry/reconciliation authority and optional canary limits.

These are recorded decisions, not guessed production defaults. They do not block
publishing this plan for review. No secrets or real recipient identities belong
in the repository. No change to trade permissions is proposed or authorized.
