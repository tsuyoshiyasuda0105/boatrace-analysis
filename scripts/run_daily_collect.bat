@echo off
REM Daily collect: runs daily_collect.py for final daily refresh (23:30)
REM Refreshes both Supabase (Render UI) and local SQLite (backtest)
cd /d C:\boat_project\boatrace-analysis
if not exist logs mkdir logs

set TS=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG=logs\daily_collect_%TS%.log

echo. >> "%LOG%"
echo === Daily collect started %date% %time% === >> "%LOG%"

REM 1. Supabase (default) - .env DATABASE_URL applied automatically
.venv\Scripts\python.exe scripts\daily_collect.py >> "%LOG%" 2>&1

REM 2. Local SQLite (--local explicitly skips DATABASE_URL)
.venv\Scripts\python.exe scripts\daily_collect.py --local >> "%LOG%" 2>&1

REM 3. DB 容量監視 (backlog item 12): 日次で Supabase 使用量チェック
REM    400 MB 超で warning、480 MB 超で error。Web バナーで通知される。
.venv\Scripts\python.exe scripts\db_size_check.py >> "%LOG%" 2>&1

echo === Daily collect finished %date% %time% === >> "%LOG%"
exit /b 0
