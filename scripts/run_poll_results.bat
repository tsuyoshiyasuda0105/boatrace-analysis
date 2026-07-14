@echo off
if exist C:\boat_project\boatrace-analysis\.pc_schedule_paused exit /b 0

REM 5-minute results polling: fetches today's race results into DB

cd /d C:\boat_project\boatrace-analysis

set LOGDIR=C:\boat_project\boatrace-analysis\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set TS=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG=%LOGDIR%\poll_results_%TS%.log

echo. >> "%LOG%"
echo === Poll started %date% %time% === >> "%LOG%"

.venv\Scripts\python.exe scripts\self_heal_today_data.py >> "%LOG%" 2>&1

.venv\Scripts\python.exe scripts\poll_results.py >> "%LOG%" 2>&1

set EXITCODE=%ERRORLEVEL%

REM task_runs logging
if "%EXITCODE%"=="0" (
  .venv\Scripts\python.exe scripts\record_task_run.py poll_results success >> "%LOG%" 2>&1
) else (
  .venv\Scripts\python.exe scripts\record_task_run.py poll_results failure --detail "exit=%EXITCODE%" >> "%LOG%" 2>&1
)

echo === Poll finished %date% %time% === >> "%LOG%"
exit /b %EXITCODE%
