@echo off
REM Odds scheduler launcher (every minute via Task Scheduler).
REM Uses pythonw.exe (console-less) so no cmd window flashes.
REM Previous VBS chain failed silently on this host - direct pythonw avoids it.

cd /d C:\boat_project\boatrace-analysis
if not exist logs mkdir logs

start "" /b .venv\Scripts\pythonw.exe scripts\odds_scheduler.py >> logs\odds_scheduler.log 2>&1
exit /b 0