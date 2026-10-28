# Compatibility and required changes — proposal only

Baseline a3c4f27f5f2f9307e7e4c645b54e55ee18668caa; synthetic behavior remains unchanged. No application source is modified in this package.

| Component | Existing API that stays unchanged | Required separately reviewed addition |
|---|---|---|
| D1 offline_evidence_ledger | Ledger constructor, ingest, publish, cutoff, select; synthetic clock/ready semantics | RecordedEvidenceLedger (separate domain/storage) with receipt verification and dual historical/replay clocks; recorded publication records actual import readiness without manufacturing historical availability. Historical selection uses verified authority and correction graph. |
| D2 offline_scenario_ledger | ScenarioLedger.record/evaluate and accepted hypothesis formation/revision rules | RecordedScenarioLedger wrapper/implementation accepting recorded evidence interface; full dependency closure and establishment UNKNOWN; same lifecycle invariants. Existing synthetic-only _policy must not be bypassed. |
| D3 offline_notification_audit | NotificationAudit.assess/generate/prepare/delivery/recover | RecordedOfflineAssessment interface for historical eligibility and preview only. No transport endpoints. Existing synthetic checks remain. No fake recipients or conversion to synthetic policies. |
| D4 offline_shadow_boundary | assess_bytes/assess_files and existing policy/schema validation | Separate assess_recorded_bytes/files with new schema, authority registry, file isolation and conservative temporal checks. Existing _policy synthetic rejection stays. |
| D5 offline_shadow_assessment | replay(raw, ledger_context, stop_after), accepted synthetic replay grammar | replay_recorded with recorded input/version checks and historical correction selection. Reuse verified algorithms only through explicit domain-safe interfaces and regression coverage; never loosen synthetic guards. |
| offline_integration | Existing synthetic CLI/fixture paths | Separate recorded coordinator after approval, explicit domain/policy, no fallback. |

Do not merely replace classification strings: D1 publish currently assigns available_at from clock and synthetic_ledger_ready_barrier; D4 requires synthetic fixture/ready attestations; D5 and D2/D3 also enforce synthetic policies. Recorded implementations need authority semantics throughout the call chain.

Legacy MT5 reader, feature modules, scenario_state, reasoning, event_monitor, alert_policy and notification_builder remain accepted logic. Use pure feature/scenario calls, preserve delivery resolution, structural identity/invalidation freezing, previous state and exact technical values. Do not call main/run_radar, import live acquisition, or write production paths. Wrapper metadata carries missingness and source times without turning absent volume into zero. Derived recomputation time is not original scenario establishment. A news adapter never changes PA/HTF or grants permission.

Implementation sequence (not authorized here): (1) schema and authority verifier conformance; (2) recorded D1 selection/persistence; (3) recorded D2 lineage/lifecycle; (4) recorded D4/D5 and D3 offline eligibility; (5) lossless legacy bridge; (6) captured-data admission audit, deterministic replay and full offline regression. Each step independently reviewable, synthetic regression mandatory. No automatic migration of existing ledgers. Any API sharing must demonstrate identical synthetic output/error behavior on accepted fixtures.

No local data audit is needed to approve the fail-closed design. Which concrete captures qualify remains unresolved and must be reported per dataset; absence of receipts may leave historical eligibility UNKNOWN indefinitely. No arbitrary TTL or clock tolerance is approved here: a later policy must explicitly supply and justify them before replay eligibility can be assessed.
