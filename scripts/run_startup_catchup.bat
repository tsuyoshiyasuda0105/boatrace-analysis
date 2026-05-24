@echo off
REM ============================================================
REM サーバー(ローカルPC)起動時のタスク・キャッチアップ
REM   Task Scheduler の ONSTART トリガから呼ばれる前提
REM   (install_all_tasks.ps1 が BoatraceStartupCatchup として登録)。
REM   PC がダウン/スリープで実行されなかったタスクを検出して実行する。
REM ============================================================
cd /d C:\boat_project\boatrace-analysis
if not exist logs mkdir logs

set TS=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG=logs\startup_catchup_%TS%.log

echo. >> "%LOG%"
echo === Startup catchup launched %date% %time% === >> "%LOG%"

.venv\Scripts\python.exe scripts\startup_catchup.py >> "%LOG%" 2>&1

echo === Startup catchup finished %date% %time% === >> "%LOG%"
exit /b 0
