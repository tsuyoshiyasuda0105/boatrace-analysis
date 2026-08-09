@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_DIR=%%~fI"
if exist "%REPO_DIR%\.pc_schedule_paused" exit /b 0

REM PC nightly prepare: builds next-day local SQLite state and syncs the diff to Supabase.
REM Intended schedule: 25:00 JST (01:00 next day).

cd /d "%REPO_DIR%"
if not exist logs mkdir logs

set LOG=logs\pc_nightly_prepare.log
set PYTHON_EXE=
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "C:\boat_project\boatrace-analysis\.venv\Scripts\python.exe" set "PYTHON_EXE=C:\boat_project\boatrace-analysis\.venv\Scripts\python.exe"

if not defined PYTHON_EXE (
    echo Python executable not found>> "%LOG%"
    exit /b 1
)

echo.>> "%LOG%"
echo === pc_nightly_prepare started %date% %time% === >> "%LOG%"

if exist .env (
    for /f "usebackq tokens=1,* delims==" %%a in (`type .env ^| findstr DATABASE_URL`) do (
        set "%%a=%%b"
    )
)

"%PYTHON_EXE%" scripts\pc_nightly_prepare.py >> "%LOG%" 2>&1
set EXIT_CODE=%ERRORLEVEL%

echo === pc_nightly_prepare finished %date% %time% exit=%EXIT_CODE% === >> "%LOG%"
exit /b %EXIT_CODE%
