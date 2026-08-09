@echo off
setlocal
if exist "C:\Users\tsuyo\OneDrive\ドキュメント\New project 2\boatrace-main-deploy\.pc_schedule_paused" exit /b 0

REM PC nightly prepare: builds next-day local SQLite state and syncs the diff to Supabase.
REM Intended schedule: 25:00 JST (01:00 next day).

cd /d "C:\Users\tsuyo\OneDrive\ドキュメント\New project 2\boatrace-main-deploy"
if not exist logs mkdir logs

set LOG=logs\pc_nightly_prepare.log
echo.>> "%LOG%"
echo === pc_nightly_prepare started %date% %time% === >> "%LOG%"

if exist .env (
    for /f "usebackq tokens=1,* delims==" %%a in (`type .env ^| findstr DATABASE_URL`) do (
        set "%%a=%%b"
    )
)

.venv\Scripts\python.exe scripts\pc_nightly_prepare.py >> "%LOG%" 2>&1
set EXIT_CODE=%ERRORLEVEL%

echo === pc_nightly_prepare finished %date% %time% exit=%EXIT_CODE% === >> "%LOG%"
exit /b %EXIT_CODE%
