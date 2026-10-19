# Market Radar — project status (2026-10-19)

This document is a handoff snapshot, not a claim that every runtime component has been independently validated.

## Purpose and guardrails
Market Radar is a read-only market-observation and scenario engine, initially focused on XAU/USD (MT5 GOLD.pro). It explains HTF context, structural A→B routes, invalidation, conditional alternatives and news/intermarket context. It does not automatically execute trades. The user's approved Notion specification remains the authority for trading logic.

## Original seven-stage roadmap
1. Strategy formalization — fundamental principles approved; some machine criteria require further formalization.
2. MVP specification — approved for implementation.
3. Local data feasibility — GO for MT5 live shadow; direct DXY not available, USD breadth is a labelled proxy.
4. Telegram skeleton — implemented; noise/alert calibration ongoing.
5. Scenario engine v0 — implemented; historical replay and calibration ongoing.
6. Live shadow / manual execution — active validation phase.
7. Expansion — requires separate decision after pilot (EUR/USD, GER40/EU50).

## October development blocks
- C1: news contamination contract — reported completed.
- C2: historical economic calendar coverage — reported completed.
- C3.1: rapid news collection/archive — reported completed.
- C3.2: story clustering — reported completed.
- C3.3.1: story classification / mechanism candidates — reported completed.
- C3.3.2: causal transmission mapping — reported completed in prior chat; verify against code/tests.
- **C3.3.3 IN PROGRESS (validator and bridge implemented):** measure actual oil/yields/USD/Gold reaction to causal hypotheses, with event time, windows, data availability and weekend handling.
- C3.3.4: integrate news interpretation with PA/HTF.
- C3.3.5: story lifecycle and Telegram dedup.
- C3.3.6: historical/live tests.
- D: integrate the finished news engine into the technical radar.
- Technical audit follows C and D, including deferred AUDIT-01 suspected PWL/PML mapping.

## News causal contract
A news story can generate candidate mechanisms, not confirmed market causality. Without measured intermarket response and PA evidence, do not switch Gold direction, alter score, or authorize entry. Hypothesis != measured reaction; correlation != causation.

## October 19 source snapshot
The shared ZIP named `MarketRadar_chat_2026-10-19_110207.zip` contained 137 entries, including Python engines, tests, scripts, installation notes and a manifest. The ZIP itself is not a live synchronized source tree. Commit and verify its extracted contents before relying on GitHub as the complete source of truth.

## Canonical project references
- [Notion — Technical Hub](https://app.notion.com/p/3d75e4544f3a816a9f4af8a22df4a024)
- [Notion — Roadmap and Handoff](https://app.notion.com/p/3d75e4544f3a8113ab52f2fe0a824cb6)
- [Notion — Implementation, Live Validation & Backlog](https://app.notion.com/p/3e55e4544f3a81478455f23d53717e19)
- [Notion — Decisions and Open Questions](https://app.notion.com/p/3d75e4544f3a81a9b49ac4eeee8b388a)
- [Notion — Trading Logic Specification](https://app.notion.com/p/3d75e4544f3a8196981dfaaf74552d86)

## Coordination
- Codex: local edits, MT5 integration, runtime verification, git sync.
- ChatGPT: architecture, code review, causal reasoning, tests and GitHub changes when requested.
- GitHub: versioned code and change history.
- Notion: approved requirements and decisions.

Never commit credentials, account identifiers, personal information, or unreviewed broker data.

## Current code import update
The current cleaned PC source, including the C3.3.3 validator, bridge and export utility, is now included. Local integration passed 437/437 offline tests on Python 3.12.10. Public bridge tests use synthetic fixtures; actual broker/news snapshots remain local. Missing yields/inflation observations and incomplete 24h windows prevent closure of the research validation. No live MT5/Telegram verification was performed and the Radar was not started. Earlier snapshot counts above are historical.

