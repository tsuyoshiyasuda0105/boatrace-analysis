@echo off
REM 5-minute results polling: fetches today's race results into DB
cd /d C:\boat_project\boatrace-analysis

set LOGDIR=C:\boat_project\boatrace-analysis\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set TS=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG=%LOGDIR%\poll_results_%TS%.log

echo. >> "%LOG%"
echo === Poll started %date% %time% === >> "%LOG%"

.venv\Scripts\python.exe scripts\poll_results.py >> "%LOG%" 2>&1

echo === Poll finished %date% %time% === >> "%LOG%"
