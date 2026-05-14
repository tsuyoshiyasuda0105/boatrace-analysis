@echo off
REM Morning task: data collect + predict + sync + L4 alert (06:30 daily)
cd /d C:\boat_project\boatrace-analysis

set LOGDIR=C:\boat_project\boatrace-analysis\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set TS=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG=%LOGDIR%\morning_%TS%.log

echo. >> "%LOG%"
echo === Morning task started %date% %time% === >> "%LOG%"

REM 1a. Race data → Supabase (.env DATABASE_URL applied automatically)
.venv\Scripts\python.exe scripts\daily_collect.py >> "%LOG%" 2>&1

REM 1b. Race data → local SQLite (DATABASE_URL cleared) so cache_predictions can use it
REM Important: cache_predictions.py uses local SQLite (DATABASE_URL.pop), so the
REM same data must exist there before predictions can be generated.
set "_SAVED_DB=%DATABASE_URL%"
set "DATABASE_URL="
.venv\Scripts\python.exe scripts\daily_collect.py >> "%LOG%" 2>&1
set "DATABASE_URL=%_SAVED_DB%"

REM 2. Predict today's races and sync to Supabase
.venv\Scripts\python.exe scripts\cache_predictions.py --today --sync >> "%LOG%" 2>&1

REM 3. Morning L4 candidate email (prediction-based, before odds confirmed)
.venv\Scripts\python.exe scripts\send_l4_alerts.py --mode morning >> "%LOG%" 2>&1

REM 4. Also try confirmed L4 (in case some races already have T-5/T-15 odds)
.venv\Scripts\python.exe scripts\send_l4_alerts.py --mode confirmed >> "%LOG%" 2>&1

echo === Morning task finished %date% %time% === >> "%LOG%"
