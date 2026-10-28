# MarketRadar contributor instructions
- Read README.md and the current project status before changing code.
- Preserve approved trading methodology, PA/HTF, scoring and trade permissions unless the user explicitly authorizes a change.
- Preserve read-only MT5 integration; do not add automated order execution.
- Never commit credentials, account identifiers, broker exports, runtime data, environments or archives.
- Do not launch the live pipeline, install autostart, connect MT5 or send Telegram messages during ordinary verification.
- Run suitable offline tests in an isolated development copy. Record actual results and limitations.
- C3.3.3 technical integration exists; missing market evidence is not proof of causality. Do not declare C3.3.4 complete or infer trading authorization.
- Public C3.3.3 fixtures are synthetic. Historical verification requires separate local evidence.


## Current development handoff (28 October 2026)
Read docs/HANDOFF.md and docs/LAPTOP_SETUP.md first. The historical C3.3.4 note above is superseded by that handoff. This branch is a development snapshot, not production acceptance. Ordinary verification uses verify_portable.py. Real verification_inputs and runtime evidence remain private; never publish them to make tests pass. No native MT5 or Telegram run is authorized by repository presence.
