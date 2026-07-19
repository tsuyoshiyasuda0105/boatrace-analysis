@echo off
setlocal EnableExtensions EnableDelayedExpansion
if exist C:\boat_project\boatrace-analysis\.pc_schedule_paused exit /b 0
REM Morning task: data collect + predict + sync + morning alerts (06:30 daily)
cd /d C:\boat_project\boatrace-analysis

set LOGDIR=C:\boat_project\boatrace-analysis\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set TS=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG=%LOGDIR%\morning_%TS%.log
set ERRORS=0

echo. >> "%LOG%"
echo === Morning task started %date% %time% === >> "%LOG%"

if /I not "%BOATRACE_TASK_TRIGGER%"=="self_heal" (
  call :run_step "self_heal_today_data" ".venv\Scripts\python.exe" "scripts\self_heal_today_data.py"
) else (
  echo [step] self_heal_today_data skipped (trigger=self_heal) >> "%LOG%"
)
call :run_step "backfill_official_supabase" ".venv\Scripts\python.exe" "scripts\backfill_official.py" "--start" "%date:~0,4%-%date:~5,2%-%date:~8,2%" "--end" "%date:~0,4%-%date:~5,2%-%date:~8,2%"
call :run_step "backfill_official_local" ".venv\Scripts\python.exe" "scripts\backfill_official.py" "--start" "%date:~0,4%-%date:~5,2%-%date:~8,2%" "--end" "%date:~0,4%-%date:~5,2%-%date:~8,2%" "--local"
call :run_step "daily_collect_supabase" ".venv\Scripts\python.exe" "scripts\daily_collect.py"
call :run_step "daily_collect_local" ".venv\Scripts\python.exe" "scripts\daily_collect.py" "--local"
call :run_step "tides_supabase" ".venv\Scripts\python.exe" "scripts\fetch_and_import_jma_tides.py" "--year-from" "%date:~0,4%" "--year-to" "%date:~0,4%" "--only-missing" "--timeout" "30"
call :run_step "tides_local" ".venv\Scripts\python.exe" "scripts\fetch_and_import_jma_tides.py" "--db" "C:\boat_project\boatrace-analysis\data\boatrace.db" "--year-from" "%date:~0,4%" "--year-to" "%date:~0,4%" "--only-missing" "--timeout" "30"
call :run_step "cache_predictions" ".venv\Scripts\python.exe" "scripts\cache_predictions.py" "--today" "--sync"
call :run_step "prewarm_strategy_pages_morning" ".venv\Scripts\python.exe" "scripts\prewarm_strategy_pages.py" "--mode" "morning-check"
call :run_step "send_l4_alerts_morning" ".venv\Scripts\python.exe" "scripts\send_l4_alerts.py" "--mode" "morning"
call :run_step "send_l4_alerts_confirmed" ".venv\Scripts\python.exe" "scripts\send_l4_alerts.py" "--mode" "confirmed"
call :run_step "check_data_quality" ".venv\Scripts\python.exe" "scripts\check_data_quality.py"

if %ERRORS% GTR 0 (
  echo [step] record_task_run morning failure >> "%LOG%"
  .venv\Scripts\python.exe scripts\record_task_run.py morning failure >> "%LOG%" 2>&1
) else (
  echo [step] record_task_run morning success >> "%LOG%"
  .venv\Scripts\python.exe scripts\record_task_run.py morning success >> "%LOG%" 2>&1
)

echo === Morning task finished %date% %time% === >> "%LOG%"
exit /b %ERRORS%

:run_step
set "STEP_NAME=%~1"
shift /1
set "STEP_CMD="
:collect_args
if "%~1"=="" goto run_collected_step
set "STEP_CMD=!STEP_CMD! "%~1""
shift /1
goto collect_args
:run_collected_step
echo [step] %STEP_NAME% start >> "%LOG%"
call !STEP_CMD! >> "%LOG%" 2>&1
set "STEP_EXIT=%errorlevel%"
if %STEP_EXIT% GEQ 1 (
  echo [warn] %STEP_NAME% failed with %STEP_EXIT% >> "%LOG%"
  set /a ERRORS+=1
) else (
  echo [step] %STEP_NAME% done >> "%LOG%"
)
exit /b 0
