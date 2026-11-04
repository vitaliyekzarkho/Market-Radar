# Market Radar — active observation handoff, 4 November 2026

Current working branch: `work/live-observation-2026-11-04`.
Version: `v0.5.2-integrated-runtime-dev.4-tick-wait`.
Previous published handoff: `1d2e6bfb772ab132348c5ac1a7c3994260d95eeb`.
Accepted D1–D5 main ancestor: `a3c4f27f5f2f9307e7e4c645b54e55ee18668caa`.
This publication records the locally deployed source. It does not change main or deploy anything to another computer. GitHub synchronizes code and this handoff, not chat memory, credentials or running processes.

## Operational state and observation start
The integrated runner receives OANDA MT5 GOLD.pro, admits closed source-order bars, uses existing PA/HTF/reasoning/scenario logic, persists C/D/R stages and applies alert policy/Telegram gates. Read-only market access, no trade execution.
Continuous observation was resumed on 4 November at 18:03:55 UTC. A complete cycle passed, Telegram confirmed a genuine eligible notification at 18:09:02 UTC, and the next cycle began at 18:09:38 UTC. No test alert was forced. Runtime and delivery evidence remain private.
The interval between the 2 November controlled check and 4 November restart is not proven continuous observation and must not count as a full forward-test week.

## Integrated changes since 28 October
- Existing runner consumes historical admission; typed source-time enables price/order analysis without fabricated historical UTC.
- Source-specific auxiliary admission, provenance and fail-closed UTC-dependent context/reaction windows; no new scoring engine.
- C/D/R capture, scenario, policy, notification and delivery journals; silent/null-primary observations remain valid.
- Bounded independent clock proofs, preserved raw epochs/receipts; no global historical broker-hour correction.
- User-authorized administrative migration archives exact legacy bytes by SHA-256, marks LEGACY_UNVERIFIED without authority, initializes current observation without unverified invalidation, and suppresses migration-induced reversal alerts. Authorization is bound to a private legacy hash and is NOT distributed here.
- Snapshot capture polls up to 15 seconds for three distinct real ticks. Missing ticks are never fabricated. Backward ticks fail closed. Continuous runner waits at least 60 seconds on the exact FRESH_TICKS_REQUIRED admission gap; other integrity errors remain blocking. --once still returns failure for a gap.
- Single-instance protection, notification deduplication and 08:00–20:00 Europe/Dublin schedule remain.

## Evidence / tests
Private full suite after migration: 1235/1235 passed, zero errors/failures/skips (30 October).
Private full suite with tick waiting/recovery: 1243/1243 passed on 2 November, 230.735 seconds, Windows/Python 3.12.10. A final backward-duplicate tick guard was subsequently checked by eight focused tests; do not describe the earlier full run as another run after that last guard edit.
Public portable-profile publication results are recorded in PUBLICATION_TEST_RESULTS.json. This is a different subset; private recorded tests cannot be reproduced from this repository alone. Real migration fixtures and native captures intentionally remain local.
Runtime source hashes are in DEPLOYED_SOURCE_MANIFEST_2026-11-04.json. They hash deployed bytes; Git may normalize line endings. Test files come from the corresponding development copy. Packaging adds jsonschema to portable requirements and applies the Windows-only MetaTrader dependency marker.

## Limitations and next work
- Historical UTC/session mapping remains UNKNOWN when not independently evidenced. Related causal/volume/rates/reaction features may be DEFERRED; C/D/R may report DEGRADED without an exception. Not every planned C/D/R capability is proven live.
- First 4 November cycle took about 342 seconds, not the configured 60 seconds. Execution is serial. Measure audit/receipt overhead before claiming low-latency operation; do not delete history to speed up.
- A delivered notification does not establish entry quality, forecast timeliness or profitability.
- Active monitoring now means collecting actual scenarios, eligible alerts, silence, deferrals and invalidations. Paper results must use actual as-of timestamps and costs; no retrospectively invented fills. An automated paper-execution/PnL engine is not claimed.
- Preserve accepted PA/HTF methodology and manual trade permission. No asset expansion or automatic orders.

## Development / rollback
Use a separate clone and verify_portable.py. Do not run run_radar.py --once as an offline test. Do not overwrite the operating PC directory from Git. Local private backups and archived legacy state exist; stop the verified worker before an approved deployment/rollback, retain new receipts/journals and never restore stale notification state over current state.
