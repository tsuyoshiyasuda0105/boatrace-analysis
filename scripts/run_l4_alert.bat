@echo off
if exist C:\boat_project\boatrace-analysis\.pc_schedule_paused exit /b 0
REM L4 alert sender (every minute via Task Scheduler BoatraceL4Alert).
REM Task Scheduler calls this through wscript.exe + run_hidden.vbs
REM so cmd is completely hidden — no visible window in the taskbar.
REM Tail logs\alert.log to confirm activity.
cd /d C:\boat_project\boatrace-analysis
if not exist logs mkdir logs
.venv\Scripts\python.exe scripts\send_l4_alerts.py >> logs\alert.log 2>&1
exit /b %errorlevel%
