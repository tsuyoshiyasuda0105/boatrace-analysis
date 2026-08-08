# 朝の自動データ取得 + 予測 + Supabase 同期タスク追加
# ユーザー権限で実行可能 (管理者不要)
#
# 実行:
#   powershell -ExecutionPolicy Bypass -File scripts\install_morning_tasks.ps1

$base = "C:\boat_project\boatrace-analysis\scripts"

# 朝タスクのバッチを作成
$morningBat = @"
@echo off
REM 朝の自動データ取得 + 予測 + 同期 (06:30 起動想定)
cd /d C:\boat_project\boatrace-analysis

set LOGDIR=C:\boat_project\boatrace-analysis\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set TS=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG=%LOGDIR%\morning_%TS%.log

echo. >> "%LOG%"
echo === Morning task started %date% %time% === >> "%LOG%"

REM 1. 今日のデータ取得
.venv\Scripts\python.exe scripts\daily_collect.py >> "%LOG%" 2>&1

REM 2. 今日の予測計算
.venv\Scripts\python.exe scripts\cache_predictions.py --today --sync >> "%LOG%" 2>&1

REM 3. L4 アラートメール送信
.venv\Scripts\python.exe scripts\send_l4_alerts.py >> "%LOG%" 2>&1

REM 4. タスク実行を task_runs に記録 (起動時キャッチアップの判定根拠)
.venv\Scripts\python.exe scripts\record_task_run.py morning success >> "%LOG%" 2>&1

echo === Morning task finished %date% %time% === >> "%LOG%"
"@

$batPath = "$base\run_morning_task.bat"
Set-Content -Path $batPath -Value $morningBat -Encoding ASCII

Write-Host "Created: $batPath"
Write-Host ""

# Task Scheduler に登録 (ユーザー権限)
$taskName = "BoatraceMorningTask"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Task already exists, removing and re-creating..."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute $batPath
$trigger = New-ScheduledTaskTrigger -Daily -At "06:30"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Boatrace morning data collect + predict + alert (06:30 daily)"

Write-Host ""
Write-Host "========================================"
Write-Host "BoatraceMorningTask installed at 06:30 daily"
Write-Host "========================================"
Write-Host ""
Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo | Format-Table TaskName, NextRunTime

# 既存の評価アラート用タスクも追加 (5分毎、L4アラート送信)
$alertBat = @"
@echo off
cd /d C:\boat_project\boatrace-analysis
.venv\Scripts\python.exe scripts\send_l4_alerts.py >> logs\alert.log 2>&1
"@
$alertBatPath = "$base\run_l4_alert.bat"
Set-Content -Path $alertBatPath -Value $alertBat -Encoding ASCII

$alertTaskName = "BoatraceL4Alert"
$existing = Get-ScheduledTask -TaskName $alertTaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $alertTaskName -Confirm:$false
}

$action2 = New-ScheduledTaskAction -Execute $alertBatPath
# 1 分毎、無期限 (T-5min オッズ取得直後にメール送信するため短く)
$trigger2 = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings2 = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $alertTaskName `
    -Action $action2 `
    -Trigger $trigger2 `
    -Settings $settings2 `
    -Principal $principal `
    -Description "Boatrace L4 alert sender (every 5 minutes)"

Write-Host ""
Write-Host "BoatraceL4Alert installed (every 5 minutes)"
Get-ScheduledTask -TaskName $alertTaskName | Get-ScheduledTaskInfo | Format-Table TaskName, NextRunTime
