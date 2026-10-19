# Import the October 19 Market Radar snapshot

Source: `MarketRadar_chat_2026-10-19_110207.zip` (137 archive entries, root directory `MarketRadar/`).

## Procedure
1. On the development PC, make a backup of the current MarketRadar working directory.
2. Compare the ZIP with the current working directory; do **not** overwrite newer files automatically.
3. Review all text and configuration files for credentials and personal information before publishing.
4. Place the reviewed source files at repository root, preserving filenames and paths.
5. Keep local runtime data, broker exports, API credentials and Telegram tokens untracked.
6. Run `python -m unittest discover -p "test_*.py"` in the appropriate Python environment, plus the normal live-shadow smoke test on the machine with MT5.
7. Commit and push after reviewing `git status` and `git diff --cached`.

The archived snapshot was previously reported as passing 425 tests in an isolated environment. This is historical evidence, not a new CI result and not proof of live MT5/Telegram operation.

Do not merge unverified experimental branches into `main` automatically.

## Import completed — current PC source
The repository was populated from the newer cleaned PC folder, including C3.3.3, rather than blindly restoring the older archive. See [publication report](SOURCE_IMPORT_2026-10-19.md). Old installation documents and runtime files were not restored. Live smoke tests remain a separate explicitly authorized operation.

