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

REM 3. データ品質再チェック (hourly でリトライ取得後の状態を更新)
.venv\Scripts\python.exe scripts\check_data_quality.py >> "%LOG%" 2>&1

REM 4. l4_daily_summary 増分同期 (backlog item 19: 日中の確定レースを反映)
REM    過去 3 日分を再計算 → 当日途中で確定したレースが ROI ダッシュボードに
REM    反映される。daily_collect.bat (23:30) との二重実行を兼ねるため安全。
.venv\Scripts\python.exe scripts\sync_l4_summary_to_supabase.py --recent-days 3 >> "%LOG%" 2>&1

REM 5. タスク実行を task_runs に記録 (起動時キャッチアップの判定根拠)
.venv\Scripts\python.exe scripts\record_task_run.py hourly success >> "%LOG%" 2>&1

echo === Hourly task finished %date% %time% === >> "%LOG%"
