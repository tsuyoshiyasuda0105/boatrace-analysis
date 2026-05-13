@echo off
REM Morning task: data collect + predict + sync + L4 alert (06:30 daily)
cd /d C:\boat_project\boatrace-analysis

set LOGDIR=C:\boat_project\boatrace-analysis\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set TS=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG=%LOGDIR%\morning_%TS%.log

echo. >> "%LOG%"
echo === Morning task started %date% %time% === >> "%LOG%"

REM 1. Today's race data collection (programs / previews / results / payouts)
.venv\Scripts\python.exe scripts\daily_collect.py >> "%LOG%" 2>&1

REM 2. Predict today's races and sync to Supabase
.venv\Scripts\python.exe scripts\cache_predictions.py --today --sync >> "%LOG%" 2>&1

REM 3. L4 alert email send (subscribers receive notifications)
.venv\Scripts\python.exe scripts\send_l4_alerts.py >> "%LOG%" 2>&1

echo === Morning task finished %date% %time% === >> "%LOG%"
