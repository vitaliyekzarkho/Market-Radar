# MarketRadar handoff — C3.3.4

Date: 2026-10-19
Status: **IMPLEMENTED / AWAITING INDEPENDENT REVIEW**
Branch: `feature/c3-3-4-news-pa-htf`
Base commit: `10f2f821e649eb39b165b2b045d4e53fac3ce5c8`

## Scope of this handoff

Only four files are added relative to the base:
- `news_pa_context_adapter.py`
- `test_news_pa_context_adapter.py`
- `docs/C3_3_4_ADAPTER.md`
- `docs/HANDOFF.md`

The first three are the previously tested C3.3.4 implementation, unchanged for
publication. This handoff is the fourth file. Existing source and trading logic
are unchanged. The main branch remains at the base commit.

## Verification

Full offline regression repeated on 2026-10-19 in an isolated development copy:
**459 tests passed, 0 failures, 0 errors**, including 22 new adapter tests and
437 existing tests. Python 3.12.10 on Windows, using the existing local project
dependencies. Test discovery: `python -m unittest discover -p "test_*.py"`.
The sandbox run set Python's temporary directory to a writable isolated directory.
This is the implementing agent's local result, not an independent reviewer or CI
result, and not a fresh dependency installation or Linux compatibility check.
Tests use synthetic fixtures. No Radar, MT5 connection or Telegram delivery was
started. No runtime logs, broker exports or credentials are part of this change.

## Architecture and decisions

- C3.3.3 is closed by user decision as a historical research block, with its
  evidence limitations retained. Older base documents describe the earlier status.
- C3.3.4 is an isolated explanatory adapter, not a PA/HTF or scenario recalculator.
- Legacy roles (including `confirmed_driver`) are labelled claims, separate from
  independent C3.3.3 validation. They never establish causality or trading approval.
- Outcomes are `supports`, `conflicts`, `insufficient_data`, `not_evaluated`.
- The complete selected technical scenario is deep-copied without edits; direction,
  score, targets, invalidation and original permissions remain unchanged.
- Explanatory context has no entry, direction-switch, scoring or causal permissions.
- Availability, observation timestamps, identity and window checks prevent using
  evidence outside the decision cutoff; stale or future evidence is not evaluated.
- Contradicted mechanisms do not imply an opposite trade. Ambiguous evidence stays
  insufficient. Audit identity, observations and bridge provenance are retained.

## Limitations and mandatory prerequisites for block D

1. A reliable source of `available_at_utc` for each input version is required.
   Publication time is not availability time. Upstream historical versioning and
   trustworthy timestamps must be established; the adapter cannot prove them.
2. An evidence-based freshness policy must be approved. The caller must explicitly
   supply `max_age_seconds`; no universal production threshold has been selected.

No connection to `run_radar.py` or `run_cycle.py` is included. Live integration is
block D. The existing reasoner's legacy-role mismatch is not patched by this
isolated addition. See `docs/C3_3_4_ADAPTER.md` for the exact API and semantics.

## Next step

The independent reviewer should inspect the exact branch commit, review the
contract, time logic, immutable scenario behavior and test coverage, and rerun
the offline regression. Report findings against that SHA. Do not mark C3.3.4
closed or merge into main until review is accepted. After acceptance, proceed to
C3.3.5 — News Lifecycle / Telegram Deduplication. Keep the working Radar unchanged
and do not start live services as part of this review.
