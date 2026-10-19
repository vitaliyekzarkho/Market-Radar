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

## Review revision: chronology and observation provenance

The interpretation envelope now accepts `timing_mode`: default `post_event`, or
explicit `pre_event_hypothesis` for a hypothesis prepared for a scheduled event.
Default interpretation availability must not precede either its own payload
`event_time_utc` (when supplied) or the validation anchor. If the payload has no
event time, the validation anchor is the declared fallback, recorded in provenance.
The forecast mode permits preparation before the event but never permits future
validation data. Own event time must still be within the decision cutoff. Optional
`generated_at_utc` cannot follow interpretation availability; validation availability
cannot precede the interpretation version it claims to validate.

Optional observation bindings, both directly and in `provenance`, are checked:
`story_id`, `event_id`, `window`, `event_time_utc`, `as_of_utc`, and
`available_at_utc`. Identity/window must match the validation report; event and
as-of timestamps must be equal instants (timezone offsets are normalized).
Availability must fall between report observation time and validation availability.
Null, malformed or contradictory supplied bindings fail closed. These are the
supported binding fields; callers must normalize other source schemas first.

The current C3.3.3 validator emits observations without individual anchors. Such
observations inherit the report binding under its schema-v1 directional-consistency
contract. The result explicitly labels `caller_supplied_validator_output` and
`independent_origin_verification: false`. Contract markers are consistency checks,
not authentication. The caller must supply unmodified validator output and retain
the underlying source report; matching asset sets alone do not prove provenance.
No upstream validator or technical module is modified by this revision.

Revision regression: 467/467 passed, including 8 additional targeted tests
(30 adapter tests total). This remains a local implementing-agent result.

Tests use synthetic inputs and real `evaluate_story` output. They check the four
acceptance variants against an identical serialized technical scenario, input
immutability, output isolation, role semantics, identity, version expectations,
future/stale timestamps, timezone boundaries, incomplete windows and invalid data.
No broker connections, Telegram delivery or live pipeline are used.
