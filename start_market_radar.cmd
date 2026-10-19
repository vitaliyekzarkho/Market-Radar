@echo off
cd /d "%~dp0"
:restart
".venv\Scripts\python.exe" run_radar.py --all-hours --interval 60
timeout /t 10 /nobreak >nul
goto restart
