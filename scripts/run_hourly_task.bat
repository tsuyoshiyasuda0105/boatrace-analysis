@echo off
REM Hourly task: race results refresh (every 2h during race times)
REM
REM Purpose: re-fetch today's race results so confirmed L4 / payouts are
REM updated. predictions are not re-generated here (already cached by
REM MorningTask in the morning; results don't affect predictions).
cd /d C:\boat_project\boatrace-analysis

set LOGDIR=C:\boat_project\boatrace-analysis\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set TS=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG=%LOGDIR%\hourly_%TS%.log

echo. >> "%LOG%"
echo === Hourly task started %date% %time% === >> "%LOG%"

REM 1. Race data -> Supabase (results, payouts, etc.)
.venv\Scripts\python.exe scripts\daily_collect.py >> "%LOG%" 2>&1

REM 2. Race data -> local SQLite (keep both in sync so future morning
REM tasks and local backtests have consistent data)
.venv\Scripts\python.exe scripts\daily_collect.py --local >> "%LOG%" 2>&1

echo === Hourly task finished %date% %time% === >> "%LOG%"
