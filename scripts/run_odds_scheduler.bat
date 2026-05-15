@echo off
REM Odds scheduler launcher (every minute via Task Scheduler).
REM Task Scheduler calls this through wscript.exe + run_hidden.vbs
REM so cmd is completely hidden — no visible window in the taskbar.
REM Use Get-ScheduledTaskInfo BoatraceOddsScheduler to confirm activity,
REM or tail logs\odds_scheduler.log.
cd /d C:\boat_project\boatrace-analysis
if not exist logs mkdir logs
.venv\Scripts\python.exe scripts\odds_scheduler.py >> logs\odds_scheduler.log 2>&1
exit /b %errorlevel%
