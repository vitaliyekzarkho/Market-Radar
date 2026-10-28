# Recorded-offline conformance test plan

All cases below are PLANNED / NOT RUN. This is specification design, not implementation acceptance. Use fabricated test receipts clearly marked as test fixtures in the test harness; never present them as genuine recorded-market evidence. A production verifier must not accept test trust anchors.

| ID | Input / action | Required result |
|---|---|---|
| S01 | Correct shape with UNKNOWN historical times | Schema valid; historical eligibility DEFERRED |
| S02 | synthetic domain / unsupported version / extra keys / naive or invalid date / negative uncertainty | Reject; no writes |
| S03 | KNOWN time missing receipt, or UNKNOWN with non-null time | Schema rejects |
| A01 | Valid audited capture + ready receipts with exact bytes and bounded clocks | Proven capture/availability only for named consumer |
| A02 | snapshot_utc, mtime, candle close or replay time only | UNKNOWN availability; no eligibility |
| A03 | Wrong issuer/hash/source/consumer/kind or unresolved reference | Reject integrity conflict; unresolved/untrusted evidence UNKNOWN; no eligibility |
| A04 | Hash-only artifact, unverifiable signature or clock health missing | UNKNOWN; no invented precision |
| T01 | Availability upper bound equal to cutoff / one microsecond later | Eligible for selection at equality / excluded later |
| T02 | Reversed clock intervals / overlapping unprovable order | REJECTED / UNKNOWN respectively |
| T03 | Historical broker offset unavailable, DST ambiguity | UNKNOWN event chronology, no current-offset substitution |
| D01 | All required inputs plus original completion and establishment receipts | Derived temporal closure validated; eligibility still policy dependent |
| D02 | One of five mandatory roles missing, stale or UNKNOWN | Propagate UNKNOWN/STALE, DEFERRED; missing not zero |
| D03 | Max(input availability) known but no computation receipt | Retrospective computation only; historical establishment UNKNOWN |
| D04 | DAG cycle, orphan, undeclared used optional input | Reject or incomplete closure; never eligibility |
| D05 | Caller omits policy-required role | Reject policy coverage mismatch |
| C01 | Old/replacement in both orders and every permutation of 3-version chain | Same as-of head and decision |
| C02 | Superseded observation arrives last | Does not regain current status |
| C03 | Correction proven after cutoff | Earlier as-of version retained; prior report immutable |
| C04 | Replacement availability UNKNOWN / replacement stale | HEAD_UNKNOWN/DEFERRED; no obsolete fallback |
| C05 | Competing replacements, cycle, foreign-source predecessor | Reject/quarantine; no journal/projection changes |
| I01 | Exact duplicate before/after restart | Same result, no duplicate events |
| I02 | Same ID changed hash or request | Atomic rejection; history/projection unchanged |
| I03 | Crash before commit / after commit before response | Rollback / idempotent recovery; no fabricated timestamp |
| I04 | Same ledger concurrent process / different dataset same ledger | Lock refusal / ownership refusal |
| P01 | Mandatory conflict / optional absent unused input | WAIT/DEFERRED / explicitly missing, not fabricated neutral |
| P02 | Missing TTL policy / stale anchor / age equal to max / age greater | Reject policy / DEFERRED / fresh / STALE |
| G01 | Gold PWL -> IDM with proven old target availability | Valid progression under D2, same hypothesis if lifecycle allows |
| G02 | Same target with missing establishment receipt | Structural candidate preserved; availability UNKNOWN |
| N01 | High score and no technical permission | No permission manufactured; notification follows explicit policy |
| N02 | News opposite direction | Explanation only; exact technical bytes unchanged |
| N03 | Replay produces plausible alert, original generation receipt missing | Original alert timing UNKNOWN; no delivery claim |
| X01 | Input attempts network path, traversal, reparse swap, transport or credential access | Fail closed under isolated runner; zero allowed external actions |
| V01 | Recorded input sent to unchanged synthetic APIs | Existing rejection preserved |
| V02 | All accepted D1-D5 and integration tests | Actual full regression, record commands/environment/counts; no inherited PASS |
| R01 | Genuine admitted multiple sequential observations/scenarios | Exact ordering and lineage, deterministic semantic results across restarts |

Verification levels must be reported separately: schema validation, semantic verifier, module tests, full regression, genuine-data replay and side-effect enforcement. Zero errors in logs alone is not evidence of no external actions. Use failing transport/acquisition doubles, import guards and filesystem/network confinement with observed denied attempts. Run only isolated offline tests; do not launch working Radar.
Acceptance requires exact implementation SHA/build hashes, pinned policy/dataset digests, actual commands, dependency versions, per-case outcomes and complete logs. Contract approval itself satisfies none of these execution gates.
