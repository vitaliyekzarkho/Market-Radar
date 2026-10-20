# MarketRadar handoff — C3.3.6

Date: 2026-10-20
Status: **C3.3.6 — IMPLEMENTED / AWAITING INDEPENDENT REVIEW**
Base: `8af2c7439fd63a2bad2fcf4945ae0f26b5db5d38`
Branch: `feature/c3-3-6-offline-validation`
Implementation SHA: recorded by the following documentation-only commit.

C3.3.5 remains ACCEPTED / CLOSED in the base. This feature validates contracts;
it does not deploy them or establish historical forecast quality.

## Files

Added:
- `offline_validation.py`
- `historical_evidence_assessment.py`
- `test_offline_validation.py`
- `test_historical_evidence_assessment.py`
- `tests/fixtures/c336/manifest.json`
- `tests/fixtures/c336/sequence.json`
- `tests/fixtures/c336/README.md`
- `tests/fixtures/c336/.gitattributes` (stable LF for hashed fixture JSON)
- `docs/C3_3_6_CONTRACT.md`

Updated only `docs/HANDOFF.md` among existing tracked files.
Accepted source modules and tests are unchanged. Development uses a separate
checkout, `work/c336_validation`, created from the exact accepted GitHub base.

## Validation results

Full regression: **559/559 passed**, 0 failures/errors (530 existing + 29 new).
Python 3.12.10, Windows, existing installed dependencies, 2026-10-20.
Used Python -B with unittest discovery and an explicit writable temporary directory
outside every repository. No fresh dependency installation or Linux/CI result is
claimed. See C3_3_6_CONTRACT.md for commands and exact interfaces.

Synthetic replay: **PASS**, 19 operation cases, nine invariants. Two complete
runs, restart after every operation and each cutoff-prefix comparison match.
Every persisted semantic field is compared; substantive IDs/timestamps are retained.
Semantic report hash:
`0b9ac8badb7e2f83a0e5a75927ff130782b24fce01428b1e57dbeea7aa5860b0`

Historical assessment: **INSUFFICIENT_EVIDENCE**. Evaluated 50 private source
entries. Both XAUUSD reference anchors were found, but input availability,
initial formation and delivery chronology remain unestablished. No PWL touch or
advance recognition is claimed. Before/after SHA-256 inventory confirmed 173
private archive files unchanged. Private archives and generated reports remain
outside the repository and are not published.

## Architecture and limitations

Two modules separate synthetic replay from read-only legacy evidence assessment.
One composed chain calls the accepted driver/grouping/interpretation/reaction/
validation/PA/lifecycle interfaces. Later correction, contradiction and delivery
cases are declared synthetic boundary inputs. This is not exhaustive coverage of
every live or macro/FOMC path, nor a production adapter. The technical scenario is
supplied and preserved, not recalculated.

Strict gates check explicit availability, ordering and candle close eligibility.
No publication, observation, file modification or report time is substituted for
availability. Fixture attestations are declared synthetic, not independently
verified facts. Historical timestamp presence alone does not authenticate history.
Scoped parseability PASS never implies historical-timeliness PASS.

The legacy historical assessor is intentionally conservative: it cannot certify a
new trusted ledger format. Concurrent archive modification prevents reproducible
assessment; use fixed private archives. No production database is opened or migrated.

## Next step

Independent review of this exact implementation, fixture coverage and contract;
then a fresh offline regression. Do not merge into main or mark C3.3.6 accepted
until explicitly approved. No accepted-module incompatibility required a source
change during this implementation.

Block D still owns live adapters, provenance authority, production freshness and
lifecycle/retry policy, Telegram transport and runtime integration.

**Do not start Radar, connect MT5, send Telegram messages, place trades, change
PA/HTF or permissions, or modify/synchronize the working Radar directory.**
