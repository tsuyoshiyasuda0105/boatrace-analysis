@echo off
REM Daily sync: pushes local SQLite to Supabase
REM Scheduled to run at 23:30 (after all races finish)

cd /d C:\boat_project\boatrace-analysis
if not exist logs mkdir logs

REM Load DATABASE_URL from .env
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%a in (`type .env ^| findstr DATABASE_URL`) do (
        set "%%a=%%b"
    )
)

REM Sync last 7 days (covers any catch-up)
for /f %%i in ('powershell -Command "(Get-Date).AddDays(-7).ToString('yyyy-MM-dd')"') do set START_DATE=%%i
for /f %%i in ('powershell -Command "(Get-Date).ToString('yyyy-MM-dd')"') do set END_DATE=%%i

.venv\Scripts\python.exe scripts\sync_to_supabase.py --start %START_DATE% --end %END_DATE% >> logs\sync_to_supabase.log 2>&1

exit /b 0
