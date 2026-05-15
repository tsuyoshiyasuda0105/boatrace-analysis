@echo off
REM Inner worker for L4 alert. Called from run_l4_alert.bat (minimized).
cd /d C:\boat_project\boatrace-analysis
if not exist logs mkdir logs
.venv\Scripts\python.exe scripts\send_l4_alerts.py >> logs\alert.log 2>&1
exit /b 0
