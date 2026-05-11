# ============================================================
# odds_scheduler.py を Windows Task Scheduler に毎分起動として登録
# ============================================================
# 実行方法:
#   1. PowerShell を「管理者として実行」で開く
#   2. cd C:\boat_project\boatrace-analysis
#   3. powershell -ExecutionPolicy Bypass -File scripts\install_odds_scheduler.ps1
# ============================================================

$TaskName = "BoatraceOddsScheduler"
$BatPath = "C:\boat_project\boatrace-analysis\scripts\run_odds_scheduler.bat"

Write-Host "============================================================"
Write-Host "Boatrace Odds Scheduler セットアップ"
Write-Host "============================================================"

# 既存タスクをチェック
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "既存タスク '$TaskName' を削除します..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# バッチファイル存在確認
if (-not (Test-Path $BatPath)) {
    Write-Error "バッチファイルが見つかりません: $BatPath"
    exit 1
}

# Action 設定 (バッチ実行)
$action = New-ScheduledTaskAction `
    -Execute $BatPath `
    -WorkingDirectory "C:\boat_project\boatrace-analysis"

# Trigger 設定 (毎分起動、無期限繰返し)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1)
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)).Repetition

# Settings (非表示・ログオン関係なく実行)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -RestartCount 0 `
    -MultipleInstances IgnoreNew

# 登録
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Boatrace odds T-15/T-5 snapshot collector (runs every minute)" `
    -User $env:USERNAME `
    -RunLevel Highest

Write-Host ""
Write-Host "============================================================"
Write-Host "[OK] タスク '$TaskName' を登録しました"
Write-Host "  Trigger: 毎分起動 (今後10年間)"
Write-Host "  Action: $BatPath"
Write-Host "  Log:    C:\boat_project\boatrace-analysis\logs\odds_scheduler.log"
Write-Host ""
Write-Host "確認コマンド:"
Write-Host "  Get-ScheduledTask -TaskName $TaskName"
Write-Host "  Get-ScheduledTaskInfo -TaskName $TaskName"
Write-Host ""
Write-Host "停止方法:"
Write-Host "  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host "============================================================"
