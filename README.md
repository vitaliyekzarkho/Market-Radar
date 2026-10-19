# Market Radar

A read-only market analysis and scenario-monitoring system for XAU/USD (MT5 GOLD.pro), with HTF structure, scenario lifecycle, intermarket context, causal news interpretation and Telegram notifications.

**Status (19 October 2026):** active live-shadow validation; October news block C is in progress. Next development step is **C3.3.3 — observed intermarket reaction**, followed by C3.3.4–C3.3.6, integration block D, then deferred technical audit.

> No automated trade execution. Market narratives and news hypotheses are not trading signals. Market evidence, risk decisions and execution remain with the operator.

## Project handoff

- [Project status and roadmap](docs/PROJECT_STATUS_2026-10-19.md)
- [Importing the October 19 source snapshot](docs/IMPORT_FROM_ZIP.md)
- [Canonical Notion technical hub](https://app.notion.com/p/3d75e4544f3a816a9f4af8a22df4a024)

## Source-code availability

This repository is being populated from the reviewed October 19 source archive. **The documentation commits do not yet contain the full Python source tree.** Runtime logs, broker data and credentials are excluded from publication. Verify source version and run tests after the import.

## Development rules

- Approved Notion trading specifications take precedence over earlier chat notes.
- Do not modify scenario scoring, PA rules or trading direction based on an unconfirmed news hypothesis.
- Keep secrets and runtime data out of Git.
- Prefer reviewed commits and tests over automatic pushes of incomplete experiments.
