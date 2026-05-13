# 時間毎の レース結果取得 + Supabase 同期タスクを追加
# 既存タスクは消さず、これだけ追加
# 実行: powershell -ExecutionPolicy Bypass -File scripts\install_hourly_tasks.ps1

$base = "C:\boat_project\boatrace-analysis\scripts"

# 1時間毎タスク用バッチを作成
$hourlyBat = @"
@echo off
REM 1時間毎: 当日のレース結果取得 + Supabase 同期
cd /d C:\boat_project\boatrace-analysis

set LOGDIR=C:\boat_project\boatrace-analysis\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set TS=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG=%LOGDIR%\hourly_%TS%.log

echo. >> "%LOG%"
echo === Hourly task started %date% %time% === >> "%LOG%"

REM 1. 今日のレース結果再取得 (programs/previews/results/payouts)
.venv\Scripts\python.exe scripts\daily_collect.py >> "%LOG%" 2>&1

REM 2. 当日分のみ Supabase に同期 (差分のみ高速)
.venv\Scripts\python.exe scripts\sync_to_supabase.py --start %date:~0,4%-%date:~5,2%-%date:~8,2% --end %date:~0,4%-%date:~5,2%-%date:~8,2% >> "%LOG%" 2>&1

REM 3. 予測も再計算 (新規レースのみ。既存はスキップ)
.venv\Scripts\python.exe scripts\cache_predictions.py --today --sync >> "%LOG%" 2>&1

echo === Hourly task finished %date% %time% === >> "%LOG%"
"@

$batPath = "$base\run_hourly_task.bat"
Set-Content -Path $batPath -Value $hourlyBat -Encoding ASCII
Write-Host "Created: $batPath"

# Task Scheduler に登録
$taskName = "BoatraceHourlyResults"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Task already exists, removing..."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute $batPath
# 09:00, 11:00, 13:00, 15:00, 17:00, 19:00, 21:00, 23:00 (レース時間帯)
$triggers = @()
foreach ($hour in @(9, 11, 13, 15, 17, 19, 21, 23)) {
    $time = "{0:00}:00" -f $hour
    $triggers += New-ScheduledTaskTrigger -Daily -At $time
}
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Principal $principal `
    -Description "Boatrace hourly results refresh (09/11/13/15/17/19/21/23 daily)"

Write-Host ""
Write-Host "============================================================"
Write-Host "BoatraceHourlyResults installed at:"
Write-Host "  09:00, 11:00, 13:00, 15:00, 17:00, 19:00, 21:00, 23:00 daily"
Write-Host "============================================================"
Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo | Format-Table TaskName, NextRunTime
