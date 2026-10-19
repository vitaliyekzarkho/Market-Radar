# Source publication — 2026-10-19
Source: current cleaned MarketRadar PC project, including C3.3.3 validator, historical bridge and MT5 export utility.
- 117 Python files imported. All production Python modules are byte-identical to the PC source.
- Only test_C3_3_3_validate_diesel.py changed for publication: fixture directory points to tests/fixtures/c333 and the fixture-based test is named synthetic.
- Synthetic fixtures replace private runtime JSON in the public test suite. Historical results are not established by these fixtures.
- Launcher and optional autostart installer included as source; neither executed.
- README, contributor instructions, dependency pins, ignore rules and project status updated.
- Broker data, credentials, logs, archives, caches and virtual environments excluded.
- Original isolated integration: 437/437 tests passed. Public source suite: 437/437 offline tests passed on Python 3.12.10 with the existing local dependencies.
- No trading algorithm, PA/HTF or permission logic changes; no live execution.


