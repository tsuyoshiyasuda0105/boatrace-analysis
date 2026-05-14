@echo off
REM Launcher that immediately spawns the inner worker via VBS (hidden)
REM and exits. The cmd.exe window flashes only briefly (~0.2 sec)
REM and disappears since the parent process exits immediately.
REM
REM Background: BoatraceOddsScheduler scheduled task requires admin
REM privileges to modify (Set-ScheduledTask returns Access denied),
REM so we cannot rewrite its action via PowerShell. Instead, we
REM hide the console at the .bat level via self-launching VBS.

start "" /b wscript.exe "%~dp0run_hidden.vbs" "%~dp0run_odds_scheduler_inner.bat"
exit /b 0
