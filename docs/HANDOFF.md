# MarketRadar handoff — C3.3.5

Date: 2026-10-20
Status: **IMPLEMENTED / AWAITING INDEPENDENT REVIEW**
Branch: `feature/c3-3-5-news-lifecycle`
Base/main: `048763eefaa094a611cec64f89bab287b54bd0d9`
Implementation SHA: recorded by the following documentation-only commit.

C3.3.4 is accepted and closed in the base. C3.3.5 adds an isolated news lifecycle,
semantic deduplication and informational notification ledger. No live transport
or integration, technical scenario or trading-permission change is included.

## Files

New:
- `news_lifecycle_engine.py`
- `news_notification_policy.py`
- `news_lifecycle_store.py`
- `news_delivery_state.py`
- `test_news_lifecycle_engine.py`
- `test_news_notification_policy.py`
- `test_news_lifecycle_store.py`
- `test_news_delivery_state.py`
- `tests/fixtures/c335/policy.json`
- `tests/fixtures/c335/README.md`
- `docs/C3_3_5_CONTRACT.md`

Updated: `docs/HANDOFF.md`. No other base files are part of this change.

## Preflight and workspace

Main was verified at the accepted base, including before publication. The feature
branch started there. Development/regression use separate `work/c335_lifecycle`.
The earlier C3.3.4 development copy contained accepted Python code but an old
handoff. The working Radar copy lacked the isolated C3.3.4 files and had a different
C3.3.3 test file associated with local fixtures. Neither earlier copy was overwritten
or synchronized. Existing source modules are preserved against GitHub. External
temporary SQLite databases and backups are excluded from publication.

## Verification

Full offline regression: **524/524 passed**, zero failures/errors:
467 existing tests and 57 new. Python 3.12.10, Windows, 2026-10-20.
Existing installed dependencies, isolated copy, synthetic fixtures. No fresh
installation, Linux/CI run or independent reviewer result is claimed.
Unittest discovery/external-temp setup and test coverage are documented in
[C3_3_5_CONTRACT.md](C3_3_5_CONTRACT.md).

Coverage includes identity/migration, materiality, correction/contradiction,
per-recipient idempotency, immutable evidence, chronology/replay, SQLite rollback
and concurrency, subprocess crashes, retry/unknown outcomes, expiry, corruption,
backup, complete scenario preservation and no-network boundaries.

## Decisions

- NEW, DEVELOPING, RESOLVED, EXPIRED are episode states. MATERIAL_UPDATE is an event.
- Stable aliases/member history; ambiguous identity yields no notification.
  Merge/split requires an explicit audited decision.
- Semantic fields and stable references drive versioning, not display text.
- Contradictions may notify without a setup, but need a delivered hypothesis for
  that recipient and later admissible targeted evidence. PA conflict is not disproof.
- SQLite commits transitions and outbox atomically. Unknown delivery never auto
  retries; a recorded risk-acknowledged decision is required. No exactly-once claim.
- Lifetime, freshness, suppression and retry parameters are explicit/versioned.
  Fixture values are synthetic, not production recommendations.

## Notion changes reviewed

[11 — Market Reasoning Engine: Product Vision & Codex Contract](https://app.notion.com/p/3fe5e4544f3a8114a87ed8cf0e8fdf0c)
was updated on 2026-10-19. It confirms the reasoning/attention focus and separates
full analysis from short notifications while preserving this block's scope.
It does not authorize PA/HTF or permission changes. Older hub/roadmap counters
are historical; accepted GitHub defines the code baseline. Notion was read only.

## Limitations and next step

The subsystem validates contracts, not source independence, clock authenticity,
significance or human identity decisions. Replay requires real availability
metadata. Split history is conservatively inherited and needs external review.
Serialize initial DB provisioning. Subscription backfill and live receipt
reconciliation are not implemented. See the contract for exact boundaries.

Block D still requires trustworthy available_at_utc, an approved C3.3.4
max_age_seconds, approved C3.3.5 policies, provenance/identity authority and
separately authorized transport/integration.

Next: independent review of implementation SHA and offline rerun. Do not mark
C3.3.5 accepted or merge main before review and user acceptance. C3.3.6 offline
validation follows under a separate task.

**Do not start Radar, connect MT5, call Telegram, place orders, change permissions,
deploy or modify/synchronize the working Radar directory.**
