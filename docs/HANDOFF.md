# Market Radar — handoff, 28 October 2026

## Start here
Working branch: `work/laptop-handoff-2026-10-28`.
Accepted GitHub main before this import: `a3c4f27f5f2f9307e7e4c645b54e55ee18668caa` (D1–D5).
This branch publishes the later local development snapshot for PC/laptop collaboration. It is not an acceptance merge or a production release.

## Current task / status
Restore Historical PA/HTF Admission — CHANGES REQUIRED.
Full PA/HTF has NOT been restored. Production monitoring and Telegram must not be enabled automatically.

The last isolated local build separates BAR_CLOSED, DATA_AVAILABLE_NOW and HISTORICALLY_AVAILABLE_AT. `live_history_admission.py` is a prototype for source-relative closure/present availability, NOT connected to the production UTC/session consumers. It does not invent historical UTC or new scoring.

Actual recorded reconstruction: 2495 of 2500 rows passed source-order closure, five current bars excluded, zero admitted at an earlier replay cutoff. This does not establish historical timezone/session correctness. Real captured input remains private and is NOT in this branch.

## Completed local work represented here
- C/D/R operational observer, recorded input/ledger/replay components, offline integration and safety helpers.
- Native no-primary repair: silent/null primary is a non-publishable observation; no fabricated direction/Point A/B.
- Time authority: raw epochs/content hashes, bounded clock evidence, 60-second expiry, receipts and deferred Decision Journal. The observation branch skips full analytics/Telegram.
- Historical admission prototype and a prepared whole-hour M15/H1 observation helper. The helper's native path has NOT been executed in the latest task.

## Evidence and limits
Last complete private offline suite on 28 October: 1181/1181 passed, 0 failures/errors/skips, Windows/Python 3.12.10. This is the original local build result, not a claim that all private fixtures exist in GitHub or that laptop setup has been independently reproduced.
Last native observation on 27 October: one cycle, 5.55 seconds, candidate offset +3600, no rollover, all final bars TIME_UNVERIFIED, journal DEFERRED. About 32.1 MB written in the isolated build. No Telegram/trades/monitoring enabled.
The earlier 14:00 M15/H1 rollover verifies only its measurement window, not a universal OANDA/DST history rule. No global MT5 minus one hour was installed.

## Next concrete work
1. Review current-analysis vs historical-replay contract; present analysis must not require unknown pre-capture availability.
2. Resolve verified UTC/session handling in market_features, session_context, key_levels and order_flow_structure without fake utc_time or a replacement engine.
3. Persist earliest verified receipt per content version; current prototype first-observed bound is capture-local.
4. Integrate admission into existing C/D/R and test full-chain session/freshness/corrections before claiming READY FOR FULL NATIVE RETEST.
5. Only after separate authorization execute a bounded whole-hour observation with external timeout; no automatic production launch.

## Laptop workflow
Read `docs/LAPTOP_SETUP.md`. Use `verify_portable.py` for the public synthetic profile. Never use `--once` as an offline-safety guarantee. Do not upload verification_inputs, accounts, runtime, secrets or archives. Git synchronizes committed files, not chat memory or uncommitted local changes.

Public laptop profile checked for this publication: verify_portable.py — 231/231 PASS, 0 failures/errors/skips on Windows/Python 3.12.10. This uses existing synthetic fixtures only; fresh dependency installation on the home laptop remains unverified.
