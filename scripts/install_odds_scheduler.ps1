$TaskName = "BoatraceOddsScheduler"
$BatPath = "C:\boat_project\boatrace-analysis\scripts\run_odds_scheduler.bat"

Write-Host "============================================================"
Write-Host "Boatrace Odds Scheduler Setup"
Write-Host "============================================================"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing task '$TaskName'..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

if (-not (Test-Path $BatPath)) {
    Write-Error "Batch file not found: $BatPath"
    exit 1
}

$action = New-ScheduledTaskAction -Execute $BatPath -WorkingDirectory "C:\boat_project\boatrace-analysis"

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1)
$repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650)).Repetition
$trigger.Repetition = $repetition

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -RestartCount 0 -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Boatrace odds T-15/T-5 snapshot collector" -User $env:USERNAME -RunLevel Highest

Write-Host ""
Write-Host "============================================================"
Write-Host "[OK] Task '$TaskName' registered."
Write-Host "  Trigger: every 1 minute (next 10 years)"
Write-Host "  Action: $BatPath"
Write-Host "  Log:    C:\boat_project\boatrace-analysis\logs\odds_scheduler.log"
Write-Host ""
Write-Host "Check command:"
Write-Host "  Get-ScheduledTask -TaskName $TaskName"
Write-Host "  Get-ScheduledTaskInfo -TaskName $TaskName"
Write-Host ""
Write-Host "Remove command:"
Write-Host "  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host "============================================================"
