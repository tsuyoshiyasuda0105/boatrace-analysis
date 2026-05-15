@echo off
REM Odds scheduler launcher (every minute via Task Scheduler).
REM Launch as a MINIMIZED titled cmd window so the user can see it
REM in the taskbar and confirm the 1-min job is running.
REM Inner batch (run_odds_scheduler_inner.bat) runs the actual python.

cd /d C:\boat_project\boatrace-analysis
if not exist logs mkdir logs

REM /min = minimized window, titled "OddsScheduler" for taskbar visibility
start "OddsScheduler" /min cmd /c "scripts\run_odds_scheduler_inner.bat"
exit /b 0
