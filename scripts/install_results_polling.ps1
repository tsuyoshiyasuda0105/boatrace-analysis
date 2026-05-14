# 5分毎にレース結果を取得するタスクを追加
# 既存タスクは消さず、これだけ追加
# 実行: powershell -ExecutionPolicy Bypass -File scripts\install_results_polling.ps1

$base = "C:\boat_project\boatrace-analysis\scripts"

# 軽量バッチ (結果取得のみ、API 負荷を最小化)
$pollBat = @"
@echo off
REM 5-minute results polling: fetches today's race results into DB
cd /d C:\boat_project\boatrace-analysis

set LOGDIR=C:\boat_project\boatrace-analysis\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set TS=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG=%LOGDIR%\poll_results_%TS%.log

echo. >> "%LOG%"
echo === Poll started %date% %time% === >> "%LOG%"

.venv\Scripts\python.exe scripts\poll_results.py >> "%LOG%" 2>&1

echo === Poll finished %date% %time% === >> "%LOG%"
"@

$batPath = "$base\run_poll_results.bat"
Set-Content -Path $batPath -Value $pollBat -Encoding ASCII
Write-Host "Created: $batPath"

# 既存タスク削除 (idempotent)
$taskName = "BoatraceResultsPolling"
schtasks.exe /Delete /TN $taskName /F 2>$null | Out-Null

# schtasks.exe で登録 (PowerShell 5.1 互換)
# 注: /DU は付けない。 /DU を付けると Once トリガになって翌日以降に
# 自動再起動しない (2026-05-15 障害で確認)。 ここでは期間制限なしで
# 5分毎に常時動作させ、 poll_results.py 側で「レース時間外スキップ」
# ロジックで無駄打ちを防止する。
$cmd = "schtasks /Create /TN `"$taskName`" /TR `"$batPath`" /SC MINUTE /MO 5 /ST 08:30 /RL LIMITED /F"
Write-Host "Running: $cmd"
$result = cmd /c $cmd 2>&1
Write-Host $result
Write-Host ""
Write-Host "============================================================"
Write-Host "BoatraceResultsPolling installed:"
Write-Host "  Every 5 minutes from 08:30 for 14h30m (until ~23:00)"
Write-Host "  Logs: logs\poll_results_YYYYMMDD.log"
Write-Host "============================================================"

# 確認 (登録できたか)
schtasks.exe /Query /TN $taskName /FO LIST 2>&1 | Select-String -Pattern "TaskName|Status|Next Run|Last Run"
