@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo MarketRadar: missing approved local Python environment.
  exit /b 2
)
".venv\Scripts\python.exe" -B "run_radar.py" --interval 60
exit /b %errorlevel%
