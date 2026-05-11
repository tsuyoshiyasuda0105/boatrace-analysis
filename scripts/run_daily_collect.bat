@echo off
REM Daily collect: runs daily_collect.py against Supabase + local
REM Scheduled to run at 06:00 daily

cd /d C:\boat_project\boatrace-analysis
if not exist logs mkdir logs

REM Step 1: Local SQLite (for scheduler/paper_trade)
.venv\Scripts\python.exe scripts\daily_collect.py >> logs\daily_collect_local.log 2>&1

REM Step 2: Supabase Postgres (for Render UI)
REM IMPORTANT: replace YOUR_PASSWORD with your actual Supabase password
REM Or load from .env file
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%a in (`type .env ^| findstr DATABASE_URL`) do (
        set "%%a=%%b"
    )
)
.venv\Scripts\python.exe scripts\daily_collect.py >> logs\daily_collect_supabase.log 2>&1

exit /b 0
