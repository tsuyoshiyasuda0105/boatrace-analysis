@echo off
REM Hourly task: race results refresh + Supabase sync (every 2h during race times)
cd /d C:\boat_project\boatrace-analysis

set LOGDIR=C:\boat_project\boatrace-analysis\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set TS=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG=%LOGDIR%\hourly_%TS%.log

echo. >> "%LOG%"
echo === Hourly task started %date% %time% === >> "%LOG%"

REM 1. Re-fetch today's race results (programs / previews / results / payouts)
.venv\Scripts\python.exe scripts\daily_collect.py >> "%LOG%" 2>&1

REM 2. Sync today's data to Supabase (delta sync, fast)
.venv\Scripts\python.exe scripts\sync_to_supabase.py --start %date:~0,4%-%date:~5,2%-%date:~8,2% --end %date:~0,4%-%date:~5,2%-%date:~8,2% >> "%LOG%" 2>&1

REM 3. Re-cache predictions (new races only, existing skipped)
.venv\Scripts\python.exe scripts\cache_predictions.py --today --sync >> "%LOG%" 2>&1

echo === Hourly task finished %date% %time% === >> "%LOG%"
