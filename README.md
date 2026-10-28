# Current working snapshot — 28 October 2026

This branch contains the current development copy for PC/laptop work. **CHANGES REQUIRED: full PA/HTF not restored.** Read [HANDOFF](docs/HANDOFF.md) and [laptop setup](docs/LAPTOP_SETUP.md). Older status text below describes the original accepted import, not the current development gate.

# Market Radar

Read-only market observation and scenario analysis for XAU/USD (MT5 GOLD.pro), with PA/HTF context, news hypotheses, intermarket observations and Telegram notifications. No automated trade execution.

## Current snapshot — 19 October 2026
The repository now includes the current Python source and tests, including C3.3.3 intermarket validation and the historical diesel bridge. Technical integration is verified; C3.3.3 research validation is still incomplete. C3.3.4 has not been started in this import. Trading logic, PA/HTF and trade permissions were not changed.

See [status and roadmap](docs/PROJECT_STATUS_2026-10-19.md), [C3.3.3 handoff](C3_3_3_HANDOFF.md), [bridge notes](README_C3_3_3_BRIDGE.md) and [publication report](docs/SOURCE_IMPORT_2026-10-19.md).

## Development setup
Python 3.12 is the verified interpreter. Create an isolated environment and install dependencies:

```sh
python -m venv .venv
python -m pip install -r requirements.txt
python -m unittest discover -p "test_*.py"
```

Activate the virtual environment before the pip/test commands (Windows PowerShell: `.\.venv\Scripts\Activate.ps1`; Linux: `source .venv/bin/activate`). Requirements pin the versions present in the verified local environment; a clean installation has not yet been verified.

Windows with a configured MT5 terminal is required for live broker integration. Web/Linux environments can edit code and run offline tests; they do not reproduce the local MT5 runtime. Never run live collection or Telegram delivery as part of ordinary tests.

## PC and web workflow
Clone this repository into a separate development folder; do not overwrite an existing runtime directory. Use a branch for each change, run tests, then commit and push. In the other environment, pull the accepted changes before continuing. GitHub stores committed changes; it does not automatically synchronize unsaved local files or chat memory.

```sh
git clone https://github.com/vitaliyekzarkho/Market-Radar.git
cd Market-Radar
git switch -c work/my-change
```

Keep a short handoff in the relevant PR or project status document: changed behavior, tests, remaining work. The approved Notion specification remains authoritative for trading requirements.

## Private local configuration
Credentials are read from environment variables by the adapters. Supply your own values locally; do not paste secrets into commits, issues or documentation. No credentials, MT5 account state, broker exports, runtime data, virtual environments or archives are included. Public test fixtures are synthetic. See [AGENTS.md](AGENTS.md) for contributor safeguards.
