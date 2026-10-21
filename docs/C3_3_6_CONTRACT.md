# C3.3.6 — Offline Validation

Status: **ACCEPTED / CLOSED**
Contract: `c336_v1`
Base: `8af2c7439fd63a2bad2fcf4945ae0f26b5db5d38`
Branch: `feature/c3-3-6-offline-validation`

## Purpose and limits

Two separate modes assess software contracts and legacy historical evidence.
Synthetic success is not evidence of market prediction, trading performance,
causality, independent source verification or readiness for live deployment.
Accepted modules are reused without modification.

The synthetic suite contains one actual composed chain through
`build_driver_contract`, `cluster_events`, `interpret_story`,
`build_reaction_report`, `evaluate_story`, `explain_scenario`, `Lifecycle.process`
and the offline Delivery ledger. It starts with normalized synthetic news records
and explicit synthetic M15 candles, not network collectors. Remaining steps inject
declared synthetic lifecycle boundary inputs to isolate corrections, contradiction,
deduplication, recipient rejection, terminal states and delivery uncertainty.
These boundary fixtures are not a production mapping of all news families.

This is contract-level end-to-end validation of the selected chain, not exhaustive
coverage of every macro/FOMC path or historical replay of the complete live Radar.
The technical scenario is supplied as an immutable fixture; PA/HTF is not
recalculated. Primary bearish, nested bullish and conditional reversal IDs remain
distinct. Independent review must assess this coverage boundary.

## Interfaces

`offline_validation.py`:

- `load_fixture(manifest_path)` validates manifest classification/provenance,
  relative path containment and SHA-256 of exact fixture bytes; returns a new
  parsed fixture and manifest fingerprints.
- `chronology(record, cutoff)` requires aware published/observed/available times
  in order, and evidence availability no later than input availability.
- `chain_input(spec, cutoff)` is the explicit synthetic adapter described above.
- `replay(fixture, work_dir, restart_after=(), until=None)` executes operations in
  a fresh temporary SQLite DB beneath an explicit external directory. It returns
  timeline and final persisted state, then removes the temporary DB.
- `validate(manifest_path, work_dir)` runs the full sequence twice, restarts after
  every operation, and compares each cutoff prefix with the full run. It returns
  a canonicalizable report with PASS/FAIL, cases, invariants and semantic hash.
- `summary(report)` returns a short human-readable summary.

`historical_evidence_assessment.py`:

- `assess_archives(archive_root=None)` takes an explicit private `data` root.
  Without it, sources are MISSING; no discovery of the working Radar or synthetic
  fallback occurs. It returns a report and never writes the source tree.
- `assess_source(root, relative)` reads contained JSON/JSONL/CSV, checks structure,
  complete declared timestamp triples and source stability during each read.
- `xau_case(history)` checks recorded reference-anchor presence and identifies
  missing evidence. It never reconstructs earlier decisions.

Reports are returned as Python values. Serialize with
`news_notification_policy.canonical(report)` to an explicitly selected destination
outside the repository. The APIs do not install dependencies or start subprocess
pipelines. No transport, broker connection or collector is invoked.

Example in an isolated checkout (paths are supplied by the operator):

```python
from offline_validation import validate, summary
from historical_evidence_assessment import assess_archives
from news_notification_policy import canonical

synthetic = validate("tests/fixtures/c336/manifest.json", external_temp_directory)
historical = assess_archives(explicit_private_data_directory)
print(summary(synthetic))
print(summary(historical))
# Persist canonical(synthetic/historical) only to explicit external destinations.
```

## Fixture and time model

The fixture manifest requires contract_version, classification=synthetic,
provenance=declared_synthetic_test_attestations, file and sha256. The public JSON
sequence includes the full technical scenario, synthetic policy, evidence
references, explicit timestamps, operations and expected outcomes.
Fixture-local `.gitattributes` pins JSON to LF so byte hashes survive Windows
and Unix Git checkouts without changing repository-wide settings.
Input records and caller objects are never mutated.

Operation order is `(aware UTC at, integer order, unique operation id)`.
Repeated lifecycle input IDs remain distinct from operation IDs. Equal timestamps
are resolved by declared order and then ID, not filesystem/list iteration order.
Malformed timestamps and missing availability are rejected, never guessed.

The chain gate checks event availability before interpretation, interpretation
before validation, and validation before lifecycle availability. M15 candle
close must precede its explicit availability, which must precede validation.
Unclosed/naive/future candles are rejected before the accepted reaction engine.
Validation evidence timing comes from the actual reaction/validation envelope.
Synthetic fact verification/independence is explicitly declared in the fixture;
it is not inferred from source count or the unverified initial news story.
The real chain's raw story remains unverified even where the boundary fixture
supplies a separately declared synthetic fact attestation.

Later corrections create later versions. Each report timeline stores its own
persisted snapshot; previous results are not rewritten.
Policy values are synthetic and do not authorize production freshness/retry rules.

## Invariants and comparison scope

The complete fixture requires 19 named cases. Missing cases or absent composed
chain cannot yield an all-invariants PASS.

- Determinism: two complete runs compare all semantic and persisted fields.
- Restart: reopen the same DB after every operation; compare complete result with
  uninterrupted replay, including outbox, attempts, IDs, journals and policies.
- No look-ahead: replay each temporal prefix without future operations and compare
  it with the same prefix in the complete run.
- Idempotency: a duplicate input leaves intent count unchanged.
- Recipient integrity: a changed recipient set must produce the expected error.
- Temporal rejection: expected rejected inputs leave every persisted field intact,
  including the decision clock.
- Delivery safety: after timeout, another begin cannot create an attempt or change
  unknown into a known outcome.
- Scenario integrity: full input and returned scenario equality, distinct primary,
  nested and alternative hypotheses, unchanged technical permissions.
- Evidence integrity: unverified validation is rejected; fixture classification and
  chronology gates prevent treating historical data as synthetic proof.

Snapshot comparison retains all columns of all 12 namespaced tables; rows are
sorted canonically, without dropping IDs, timestamps or payload fields. Physical
SQLite page layout and the external temporary path are not semantic outputs.

## Historical evidence semantics

Read-only sources include news archive, stories/interpretations, event history,
GOLD.pro W1/D1/H4/H1/M15 CSVs, USD/yields/volume, last Telegram result/state, four
identified causal pack manifests/events and available pack raw CSVs.
No raw message content, recipient mapping or account identifier is echoed.
Reports still contain archive fingerprints and derived metadata: treat them as
private and review before sharing.

Quality:

- VERIFIED applies to a narrowly established property such as parseability.
- PARTIAL applies to readable legacy sources without authenticated availability.
- MISSING means absent files or no explicit archive root.
- UNTRUSTED covers malformed, inconsistent, escaping or changing sources.

Per-source verdicts distinguish FAIL, INSUFFICIENT_EVIDENCE and scoped property
PASS. A parseability PASS does not validate content authenticity or timeliness.
Even complete self-declared timestamp triples cannot authenticate a historical
decision. Overall historical verdict stays INSUFFICIENT_EVIDENCE for these legacy
formats; this mode is not a general trusted-ledger certification API.

Missing fields are counted at the archive record level. Nested market observation
timestamps are not promoted to version availability. An event record has different
semantics from an OHLC row: missing publication fields in a CSV are reported, not
repaired or interpreted as proof of data corruption.

XAUUSD checks the two preflight reference records, nested-candidate presence and a
later price record. It returns explicit gaps for initial formation, technical and
news/USD/yields availability, complete scenario versions and delivery chronology.
PWL reached and bullish recognition before movement remain NOT_ESTABLISHED.
Finding a PWL target is not evidence that price touched it.

The implementation detects changes during each file read, but does not lock a
production tree or prove cross-file atomicity. Historical reproducibility requires
the same fixed archive bytes; changing live sources are not a frozen archive.

## Verification — 2026-10-20

Python 3.12.10, Windows, existing local dependencies.
Full offline regression: **559/559 passed**, 0 failures/errors
(530 existing + 29 new). No accepted tests or source modules changed.

Command from the isolated checkout:

```python
import tempfile, unittest
tempfile.tempdir = external_temp_directory  # writable, outside all repositories
result = unittest.TextTestRunner().run(unittest.defaultTestLoader.discover("."))
assert result.wasSuccessful()
```

Use Python `-B`. The synthetic validation report passed 19 cases and all nine
invariants, including exact restart comparison and all cutoff prefixes.
Semantic output SHA-256:
`0b9ac8badb7e2f83a0e5a75927ff130782b24fce01428b1e57dbeea7aa5860b0`.
Fixture content SHA-256:
`3492af33143f0aeff9964f75175a936e47e66c420e97b65c8094b345064ddb36`.

Private archive assessment inspected 50 source entries. All had overall
INSUFFICIENT_EVIDENCE for historical timeliness; both XAU reference records were
found, without establishing prediction timing. A separate before/after hash
inventory verified 173 private files unchanged. Neither private inputs nor
generated reports are committed.

This is implementer validation, not independent review, a clean dependency
installation or Linux/CI verification. Missing historical evidence does not fail
the synthetic suite. Report fixtures do not claim market success.

## Next step and exclusions

Independent code review APPROVED for implementation `926e19155a3475438b0a6f72d238bee3129cf5b0`
and branch HEAD `ddc2ca5603df2e8535d5b351193afc06b8ea8add`.
Closure authorized 2026-10-21. The implementer's 2026-10-20 result of 559/559
on Windows/Python 3.12.10 is accepted; no new or independent regression is claimed.
Only acceptance documentation changes; implementation and dependencies are unchanged.
Next: independently reviewed Block D technical plan. No live deployment authorization.

Block D retains live adapters, provenance authority, production policy approval,
Telegram transport and runtime integration. No trading/PA/HTF behavior, permissions,
working Radar data, services or strategic Notion document is changed.
