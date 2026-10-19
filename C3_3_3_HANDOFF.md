# C3.3.3 — Intermarket hypothesis validation handoff

Date: 2026-10-19
Source: October 19 ZIP snapshot (before Codex's subsequent local cleanup).

## Implemented
- New `story_intermarket_validation_engine.py` compares the mechanism candidates from `fast_news_interpretation_engine.py` with completed measurement windows produced by `reaction_window_engine.py`.
- Conservative result statuses: supported / contradicted / mixed / insufficient_data.
- Explicit story_id == reaction event_id check, no arbitrary choice among multiple instruments mapped to the same channel.
- Missing or incomplete candles never count as evidence.
- Qualitative conditions such as `conditional` and `not_strongly_bullish` cannot be verified from price direction alone.
- No permissions for entry, direction switching, scoring, or causal confirmation.

## Verification
- Eight new tests pass.
- Entire October 19 ZIP snapshot with additions: 433/433 unittest tests pass in isolated environment.
- This does not verify live MT5, broker data or Telegram.

## Usage example
python story_intermarket_validation_engine.py --interpretations data/news/fast_news_interpretations.json --reaction data/news/reaction_windows.json --story-id <EXACT_STORY_ID> --window post_60m --output data/news/story_intermarket_validation.json

Reaction report event_id MUST equal story_id. Generate the reaction report using the correct event anchor; for diesel-supply story the confirmed analysis anchor is 2026-10-09 18:46 UTC, not earlier teaser at 15:36 UTC.

## Remaining before C3.3.3 completion
- Obtain actual oil, nominal yields, USD, gold observations from suitable sources and explicitly record source/proxy provenance.
- Align market clocks and test Friday/weekend/no-tradable-bar behavior end to end.
- Validate thresholds per asset and whether comparing percentage yield moves is appropriate; for yields, basis-point moves may be required.
- Distinguish a direction-consistency result from a verified causal chain and HTF/PA evidence.
- Integrate with C3.3.4 only after measured data are available and reviewed.

## Codex integration instruction
Compare these two new files against the latest local cleaned local MarketRadar project. Preserve newer local edits. Run tests locally, then commit to GitHub. Do not restore removed obsolete files or replace local runtime data. Notion rules unchanged.
