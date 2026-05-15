@echo off
REM L4 alert sender (every minute via Task Scheduler BoatraceL4Alert).
REM Launch as a MINIMIZED titled cmd window so the user can verify
REM the 1-min job is running via the Windows taskbar.
cd /d C:\boat_project\boatrace-analysis
if not exist logs mkdir logs
start "L4Alert" /min cmd /c "scripts\run_l4_alert_inner.bat"
exit /b 0
