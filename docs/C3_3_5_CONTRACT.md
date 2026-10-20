# C3.3.5 — News lifecycle, semantic deduplication and notification state

Status: **C3.3.5 — ACCEPTED / CLOSED**  
Base: `048763eefaa094a611cec64f89bab287b54bd0d9`  
Branch: `feature/c3-3-5-news-lifecycle`  
Verification date: 2026-10-20.

## Scope and trust boundary

This isolated subsystem consumes normalized, versioned evidence contracts. It does
not collect news, classify text, establish causality, verify source independence
or send messages. Existing collectors, grouping, C3.3.4, technical engines and
entry points are unchanged. There is no production mode.

The caller supplies opaque immutable claim, hypothesis, mechanism and evidence
references. A meaning change requires a changed reference or semantic field.
`verified` and `independent` are upstream attestations, not findings independently
established here. Outlet count cannot establish independence. Block D needs a
reviewed adapter and provenance authority. Chronology checks cannot authenticate
clocks; publication/collection times are never substituted for availability.
Block D must verify provenance before accepting `independent=True`; forwarding
that flag from an untrusted source is not an approved integration contract.

## Modules and API

| Module | Responsibility |
| --- | --- |
| news_notification_policy.py | Explicit frozen Policy, UTC/canonical serialization, fingerprints, intents |
| news_lifecycle_store.py | SQLite transactions, uniqueness, namespaces, history, external backup |
| news_lifecycle_engine.py | Normalization, identity, episodes, material transitions, outbox |
| news_delivery_state.py | Offline delivery ledger with no transport |

Create `Store(external_path, namespace="opaque_name", mode="offline" or "replay")`,
then `Lifecycle(store, policy)` and `Delivery(store)`. Close the store when done.
Database parent directories must exist. Databases and new backup destinations
must be outside the source directory and any detectable Git working tree.

- `Lifecycle.process(raw, recipients, now=aware_iso_time, scenario=None)` returns
  identity status, resolved story/episode/version and newly queued notification IDs.
  It deep-copies the entire scenario unchanged, without persisting it.
- `Lifecycle.expire(now=...)` advances bounded expiry.
- `Lifecycle.migrate(migration_id=..., parents=[...], children={alias: [members]},
  authorization_ref=..., now=...)` records a reviewed merge/split.
- `Store.outbox()` returns decoded rows; `Store.backup(new_external_path)` uses
  SQLite backup without replacing an existing destination.

## Input contract

Required: input_id, upstream_story_id, nonempty member_event_ids, published_at_utc,
observed_at_utc, available_at_utc, boolean significant, change_kind, assessment,
fact_status, hypothesis_status, hypothesis_id, fact_refs, mechanism_refs, evidence.
Optional: contradicts_hypothesis_id, resolution_reason, display_text.
Unknown fields fail. Display text is discarded before hashing or persistence.

Identifiers are restricted opaque references, never URLs, credentials, raw text,
broker/account identifiers or actual chat IDs. Recipients start with `recipient_`.
Syntax validation is not a general secret detector: callers must provide genuinely
opaque identifiers. Recipient mappings do not belong in this database.

Each evidence record has exactly ref, origin_ref, subject_ref, relation, kind,
boolean verified, boolean independent, observed_at_utc, available_at_utc.
Evidence reference payloads are immutable within a namespace.

| Dimension | Values |
| --- | --- |
| Assessment | supports, conflicts, insufficient_data, not_evaluated |
| Fact | unverified, corroborated, verified, corrected, retracted, cancelled |
| Hypothesis | proposed, supported, contradicted, insufficient_data, not_evaluated |
| Identity result | resolved, ambiguous, requiring_review |
| Change kind | initial, development, repeat, redistribution, recalculation, fact_correction, hypothesis_contradiction, context_update, resolution |
| Evidence kind | fact, validation, correction, resolution |
| Evidence relation | supports, contradicts, corrects, resolves, context |

Supported/conflicting assessments and supported/contradicted hypotheses require
verified validation evidence. Verified/corroborated facts require verified fact
evidence; corroboration additionally requires attested independence. Contradiction
must target the original hypothesis with verified validation/contradicts evidence.
Corrections must address a current or previous fact with verified
correction/corrects evidence. Broader interpretation and resolution scope remain
upstream validator responsibilities.

## Identity and episodes

Canonical identity is minted deterministically from namespace and first accepted
input ID, not the changing upstream cluster. Aliases and historical members resolve
later versions. Missing members never erase lineage. Late earlier publications
do not replace identity. Multiple active matches yield ambiguous, with no intent.
A retired alias without a resolvable active member yields requiring_review.
Review-only inputs are persisted; submit a new version after identity resolution.

There is no heuristic merge. Explicit migration requires active parents, new child
aliases, complete disjoint historical member partitions and an authorization
reference. Parent history remains accessible through edges. Queued/failed parent
intents are superseded; delivered/attempted/unknown outcomes remain. Child snapshots
avoid repeating identical delivered/uncertain semantic history per recipient.
Queued predecessor work may generate a child MATERIAL_UPDATE.
Split ancestry conservatively inherits predecessor communication history; precise
attribution and the correctness of the mapping require external review.

Persistent states: NEW, DEVELOPING, RESOLVED, EXPIRED. MATERIAL_UPDATE is a journal
event. Version starts at 1 and increments per accepted material transition.
The journal preserves previous/current snapshots and input references.

Semantic fields are assessment, fact/hypothesis status, hypothesis/contradiction
IDs, sorted fact/mechanism references and significance. Fingerprints hash these
fields, never message text. Ordering, wording, outlet count or changed evidence
references alone are nonmaterial. Repeats, redistribution and recalculation do not
advance state or extend lifetime. Added facts require matching independently
verified fact/supports attestations. Fact removal/replacement requires the explicit
correction/resolution path. Mechanism/interpretation changes require changed
semantic fields or stable version references.

If a proposed fact-set change lacks the required attestations, the accepted
semantic snapshot retains both previous fact_refs and fact_status. Independently
admissible changes to the other dimensions can still advance the semantic version
and produce an intent. This does not prove that an interpretation is independent
of those rejected facts: that remains the upstream validation contract.
The original normalized input remains immutable, FACT_CHANGE_REJECTED records
proposed and retained fact fields, and the result exposes rejected_semantic_fields.
A rejected fact-only change does not advance version or lifetime. Fingerprints
and transition journals use the accepted snapshot, not the rejected fact proposal.

Resolution requires reason verified_correction, documented_cancellation or
authorized_manual plus verified resolution/resolves evidence. Its scope and
authorization are upstream attestations. Elapsed time and feed absence never
resolve facts. Terminal episodes stay terminal; a later material development
creates a linked episode. An unchanged terminal snapshot does not reopen it.

## Notification semantics

Intents include story/episode IDs, version/fingerprint, kind, recipient, idempotency
key, reason code, previous/current assessments, hypothesis/contradicted-hypothesis
IDs, evidence refs, availability/creation/expiry times, policy version, frozen
delivery policy and informational_only=true.

Kinds: FIRST_SIGNIFICANT_STORY, MATERIAL_UPDATE, FACT_CORRECTION,
MARKET_HYPOTHESIS_CONTRADICTED, PA_HTF_CONTEXT_CONFLICT, EPISODE_RESOLVED.
An initially insignificant episode becoming significant emits MATERIAL_UPDATE.
Reason is a structured kind code; prose explanation/presentation remains block D.

Label: **MARKET RADAR — HYPOTHESIS UPDATE**. Disclaimer:
**Informational update — not a trading signal.**
Entry, exit, direction-switch, score and causal-confirmation permissions are false.
The caller's technical scenario remains unchanged.

Contradiction requires a delivered predecessor hypothesis for that recipient
(including reviewed ancestry), and matching evidence observed after that hypothesis
version became available. It does not require a new setup. Unsupported, misdirected
or uncommunicated claims produce no contradiction intent. PA/HTF disagreement is
not factual disproof. Corrections, contradictions and resolutions can notify
without significance.

Idempotency includes story, episode, version, kind and recipient. A-B-A transitions
remain distinct. Genuine later contradictions are not suppressed by similar text.
Ordinary MATERIAL_UPDATE may be delayed after a prior delivered update by the
suppression interval. Corrections/contradictions bypass it. TTL can expire delayed
work; suppression never extends validity.

Input IDs are immutable; identical retries return duplicate with no new intents.
The first call persists the sorted unique recipient_keys (including an empty set).
A retry with a different set raises ValueError and rolls back the entire transaction;
ordering and duplicate entries do not change the set. The constraint survives
restart. Legacy stored inputs without recipient_keys fail closed on retry and need
an explicitly reviewed offline migration; no recipient set is guessed from outbox
rows, since suppressed events may have no rows.
This is not a subscription/backfill API. Block D must supply its complete intended
recipient set in the first call; this module cannot detect a recipient omitted
from that initial call. A later subscription needs a separately designed backfill
operation, not a new input ID invented to bypass the guard.

## Time and configuration

Aware ISO timestamps normalize to UTC; equivalent offsets are accepted. Naive,
malformed, future or stale inputs fail. Publication <= observation <= availability
<= decision. Evidence observation <= evidence availability <= input availability.
Freshness checks evidence observation and input availability. Namespace decision
clocks cannot move backwards.

Publication, observation, availability, journal decision, creation, attempt and
acknowledgment are stored separately. Older available versions are journal-only
after a newer material version; they cannot rewrite prior decisions.
Replay uses its own namespace and explicit cutoff; unavailable evidence fails.
Normalization precedes duplicate lookup: an ancient duplicate outside the current
freshness policy is rejected rather than refreshing/backfilling a story.

Policy has no defaults. Required: version, episode_max_seconds,
inactivity_seconds, notification_ttl_seconds, suppression_seconds,
evidence_max_age_seconds, retry_max_attempts, retry_backoff_seconds,
attempt_timeout_seconds. Positive integers; suppression may be zero.
A version cannot be reused with different settings. Lifetime parameters are
frozen per episode; retry parameters per intent. Expiry is the earlier of maximum
age and inactivity since material change. Fixture values are synthetic only.

## Persistence and delivery

BEGIN IMMEDIATE and primary-key uniqueness atomically commit input, evidence,
identity, journal and outbox, or roll them back. Use one connection per processor.
Tests cover concurrent processors on an initialized DB; serialize first-time DB
provisioning. Invalid/corrupt/unsupported databases fail without reset. No schema
upgrade or downgrade facility is provided.

- `begin(id, now=...)` records an attempted send before returning its number.
- `acknowledge(id, number, outcome=..., now=..., receipt_ref=...)` accepts delivered,
  transient_failure, permanent_failure, timeout, unknown. Delivered needs an opaque
  receipt. Only known transient failures allow bounded exponential-backoff retry.
- Timeout is unknown. `recover(now=...)` makes expired attempt leases unknown
  after a crash, never automatically failed.
- `resolve_unknown` requires a decision reference and explicit duplicate-risk
  acknowledgment for retry. TTL/attempt limits still apply. Cancel is also recorded.
- A late delivered receipt can reconcile the current unknown attempt. Older
  attempts or receipts after explicit retry/cancel decisions require external
  manual reconciliation; they are rejected by this API.
- `dispose` cancels/supersedes queued/failed work, not uncertain/delivered outcomes.

No transport, raw exception strings, URLs, actual recipient IDs or secrets are
stored. No Telegram exactly-once guarantee is claimed. Production authorization,
transport, receipt reconciliation, retention and monitoring remain block D.

## Verification and next step

2026-10-20 revision: **530 passed, 0 failures, 0 errors** (467 existing + 63 new).
Python 3.12.10 on Windows, existing installed dependencies, isolated source copy,
synthetic fixtures, external disposable SQLite directory. This is a local
implementer result, not independent review, clean installation, Linux or live
verification. Use Python `-B` and unittest discovery. If the default temp directory
is restricted, set `tempfile.tempdir` to an existing writable directory outside
every repository before calling `unittest.defaultTestLoader.discover(".")`.

Coverage includes regrouping, late/missing members, merge/split, ambiguous identity,
repetition/redistribution, independent development/correction, delivered-recipient
contradictions and old/unrelated evidence, PA conflict, A-B-A, restart/duplicates,
multiple recipients, wording, chronology/replay, rollback/concurrency, actual
subprocess termination before commit and after attempted send, queued restart,
retry/unknown/expiry, corruption/backup, scenario preservation and network guards.
No tests invoke a real sender.

Six revision tests cover recipient set changes/atomic rollback, equivalent sets,
restart/empty sets, missing legacy contracts, mixed unsupported fact changes with
validated PA conflict or targeted hypothesis contradiction, and fact-only
nonmaterial changes. Before the fix, five of these tests failed against the
reviewed implementation; all pass after the fix.

Reviewed the 2026-10-19 update to
[11 — Market Reasoning Engine: Product Vision & Codex Contract](https://app.notion.com/p/3fe5e4544f3a8114a87ed8cf0e8fdf0c).
It preserves C3.3.5 scope. Full analysis, short attention messages and broader
hypothesis modeling do not authorize trading-methodology changes here.
Older hub/roadmap progress counters do not override the GitHub code baseline.

The user approved the code review. Fresh GitHub checkout verification of exact SHA
`39d220302c678e1777709c73d42406449f135976` passed 530/530 on 2026-10-20.
This was a separate-checkout rerun by Codex using existing Windows dependencies,
not an independently executed reviewer/CI test run. See HANDOFF.md for evidence.
The acceptance documentation does not change tested source code.

Next: C3.3.6 — Offline Validation in a separate branch. Block D requires
trustworthy availability/provenance, approved C3.3.4 max_age_seconds and C3.3.5
lifecycle/delivery policies, identity authority and separately authorized integration.
**Do not start Radar, MT5 or Telegram or synchronize the working Radar folder.**
