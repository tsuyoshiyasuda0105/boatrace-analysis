# Update all Boatrace tasks to hidden VBS execution.
#
# Run:
#   powershell -ExecutionPolicy Bypass -File scripts\update_tasks_hidden.ps1
#
# Effect:
#   Tasks run in the background, no black cmd.exe window pops up.
#   Existing triggers/schedules are preserved.

$vbsPath = "C:\boat_project\boatrace-analysis\scripts\run_hidden.vbs"

$tasks = @{
    "BoatraceMorningTask"    = "C:\boat_project\boatrace-analysis\scripts\run_morning_task.bat"
    "BoatraceHourlyResults"  = "C:\boat_project\boatrace-analysis\scripts\run_hourly_task.bat"
    "BoatraceL4Alert"        = "C:\boat_project\boatrace-analysis\scripts\run_l4_alert.bat"
    "BoatraceResultsPolling" = "C:\boat_project\boatrace-analysis\scripts\run_poll_results.bat"
    "BoatraceOddsScheduler"  = "C:\boat_project\boatrace-analysis\scripts\run_odds_scheduler.bat"
    "BoatraceDailyCollect"   = "C:\boat_project\boatrace-analysis\scripts\run_daily_collect.bat"
}

Write-Host "============================================================"
Write-Host "Rewriting Boatrace tasks to run hidden via VBS wrapper"
Write-Host "============================================================"

foreach ($taskName in $tasks.Keys) {
    $batPath = $tasks[$taskName]
    Write-Host ""
    Write-Host "--- $taskName ---"

    $existing = schtasks.exe /Query /TN $taskName /FO LIST 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  (task not found, skipping)"
        continue
    }

    if (-not (Test-Path $batPath)) {
        Write-Host "  WARN: bat file not found: $batPath"
        continue
    }

    $newCmd = "wscript.exe `"$vbsPath`" `"$batPath`""
    Write-Host "  new command: $newCmd"

    $result = cmd /c "schtasks /Change /TN `"$taskName`" /TR `"$newCmd`" 2>&1"
    Write-Host "  $result"
}

Write-Host ""
Write-Host "============================================================"
Write-Host "Done. No restart needed. Next trigger runs hidden."
Write-Host "============================================================"
Write-Host ""
Write-Host "Current state:"
Get-ScheduledTask -TaskName "Boatrace*" -ErrorAction SilentlyContinue |
    Get-ScheduledTaskInfo |
    Format-Table TaskName, LastRunTime, LastTaskResult, NextRunTime -AutoSize
