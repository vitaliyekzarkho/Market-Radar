# C3.3.4 isolated explanation adapter

Base: published commit 10f2f821e649eb39b165b2b045d4e53fac3ce5c8.
The user closed C3.3.3 as a historical research block, retaining its limitations.
This implementation is a separate development copy, not deployed to the runtime or GitHub.

## Contract

`news_pa_context_adapter.explain_scenario` accepts the selected technical branch
(its top-level `direction` must be bullish or bearish), optional interpretation and
validation envelopes, an explicit decision cutoff `as_of_utc`, and an explicit
freshness policy `max_age_seconds`. It returns a deep copy of the complete branch
under `scenario` plus a separate `news_context`. No technical fields are rewritten.

Each envelope has `available_at_utc` and `payload`. The interpretation payload is
one output of `fast_news_interpretation_engine.interpret_story`; the validation
payload is the output of `story_intermarket_validation_engine.evaluate_story`.
The report must have event and observation timestamps. Missing availability must
not be filled with publication time: availability means when that version was
first knowable, including acquisition and processing. Inputs with unknown timing
remain insufficient. Upstream versioned storage and honest availability timestamps
are required; timestamps alone cannot prove that historical revisions are genuine.

```python
from news_pa_context_adapter import explain_scenario

result = explain_scenario(
    selected_branch,
    {"available_at_utc": interpretation_first_known, "payload": interpretation},
    {"available_at_utc": validation_first_known, "payload": validation},
    as_of_utc=decision_time,
    max_age_seconds=approved_freshness_limit,
    legacy_role=existing_news_role,
)
```

## Semantics

- Legacy roles become labelled claims only. `confirmed_driver` never implies
  independently established causality. Legacy claims cannot override validation.
- `supports` / `conflicts`: all supplied mechanisms have complete supported
  observations and a directional Gold expectation agreeing/opposing the branch.
- A contradicted mechanism does not prove the reverse direction. Mixed mechanisms,
  conditional expectations, missing observations and failed checks give
  `insufficient_data`.
- Missing evaluation, future input or stale observation gives `not_evaluated`.
  These statuses are neutral to the technical scenario.
- Freshness uses observation time, not recent ingestion of an old observation.
  The expiry boundary is inclusive. No universal freshness threshold is invented.
- Identity, window, timestamps, original bridge provenance and observations are
  retained for audit. Duplicate/missing mechanism IDs and changed expectations
  cannot reuse a validation as support.
- Context permissions for entry, direction switching, score and causal confirmation
  are always false. Original scenario permissions remain unchanged.

This adapter does not recompute PA/HTF, scores, routes or targets. It does not attach
itself to the existing reasoner's roles: the old role mismatch remains in the old
path until an explicitly reviewed integration. Connecting the new chain to
`run_radar.py` remains block D. Telegram lifecycle changes remain outside scope.

## Verification

Tests use synthetic inputs and real `evaluate_story` output. They check the four
acceptance variants against an identical serialized technical scenario, input
immutability, output isolation, role semantics, identity, version expectations,
future/stale timestamps, timezone boundaries, incomplete windows and invalid data.
No broker connections, Telegram delivery or live pipeline are used.
