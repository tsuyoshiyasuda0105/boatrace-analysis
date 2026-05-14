@echo off
REM Inner worker for odds_scheduler. Called from run_odds_scheduler.bat
REM via run_hidden.vbs so this never opens a console window.
cd /d C:\boat_project\boatrace-analysis
if not exist logs mkdir logs
.venv\Scripts\python.exe scripts\odds_scheduler.py >> logs\odds_scheduler.log 2>&1
exit /b 0
