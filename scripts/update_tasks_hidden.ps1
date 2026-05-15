# Switch all Boatrace scheduled tasks to run completely hidden via VBS wrapper.
#
# Why this script exists separately:
#   Modifying scheduled tasks is a persistence operation — AI agents
#   should not perform autonomously. Run this ONCE after reviewing.
#
# Prereq:
#   scripts\run_hidden.vbs must be ASCII-only (fixed 2026-05-16, see
#   commit history). The original UTF-8 version silently failed under
#   wscript.exe on Japanese Windows (CP932 codepage).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\update_tasks_hidden.ps1
#
# To revert one task back to direct bat call:
#   schtasks /Change /TN "<TaskName>" /TR "C:\path\to\<bat>"

$ErrorActionPreference = 'Continue'
$vbsPath = 'C:\boat_project\boatrace-analysis\scripts\run_hidden.vbs'

# Map task name -> launcher bat. Matches commits on 2026-05-16:
#   - run_odds_scheduler.bat / run_l4_alert.bat reverted to direct python call
#     (no `start /min` wrapper) so VBS truly hides everything.
$tasks = [ordered]@{
    'BoatraceMorningTask'    = 'C:\boat_project\boatrace-analysis\scripts\run_morning_task.bat'
    'BoatraceHourlyResults'  = 'C:\boat_project\boatrace-analysis\scripts\run_hourly_task.bat'
    'BoatraceDailyCollect'   = 'C:\boat_project\boatrace-analysis\scripts\run_daily_collect.bat'
    'BoatraceL4Alert'        = 'C:\boat_project\boatrace-analysis\scripts\run_l4_alert.bat'
    'BoatraceResultsPolling' = 'C:\boat_project\boatrace-analysis\scripts\run_poll_results.bat'
    'BoatraceOddsScheduler'  = 'C:\boat_project\boatrace-analysis\scripts\run_odds_scheduler.bat'
}

if (-not (Test-Path $vbsPath)) {
    Write-Error "VBS wrapper not found: $vbsPath"
    exit 1
}

Write-Host '============================================================'
Write-Host '  Rewriting Boatrace tasks to run hidden via VBS wrapper'
Write-Host '============================================================'
Write-Host ''
Write-Host "VBS wrapper: $vbsPath"
Write-Host ''

$success = 0
$failed = @()

foreach ($taskName in $tasks.Keys) {
    $batPath = $tasks[$taskName]
    Write-Host "--- $taskName ---"

    # Sanity: task must exist
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $existing) {
        Write-Host '  (task not found, skipping)' -ForegroundColor Yellow
        $failed += "$taskName (not found)"
        continue
    }

    # Sanity: launcher bat must exist
    if (-not (Test-Path $batPath)) {
        Write-Host "  WARN: bat file not found: $batPath" -ForegroundColor Yellow
        $failed += "$taskName (bat missing)"
        continue
    }

    # Show current action for visibility
    $current = $existing.Actions[0].Execute
    Write-Host "  current Execute: $current"

    # /TR is task-run. Quoted paths with embedded quotes for schtasks.
    $newTR = "wscript.exe `"$vbsPath`" `"$batPath`""
    $result = & cmd /c "schtasks /Change /TN `"$taskName`" /TR `"$newTR`" 2>&1"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  -> $newTR" -ForegroundColor Green
        $success++
    } else {
        Write-Host "  FAILED: $result" -ForegroundColor Red
        $failed += "$taskName ($result)"
    }
}

Write-Host ''
Write-Host '============================================================'
Write-Host "  Done. $success/$($tasks.Count) tasks switched to hidden."
if ($failed.Count -gt 0) {
    Write-Host "  Failures: $($failed -join ', ')" -ForegroundColor Red
}
Write-Host '============================================================'
Write-Host ''
Write-Host 'Current state:'
Get-ScheduledTask -TaskName 'Boatrace*' -ErrorAction SilentlyContinue |
    ForEach-Object {
        [PSCustomObject]@{
            Task   = $_.TaskName
            State  = $_.State
            Hidden = ($_.Actions[0].Execute -eq 'wscript.exe')
        }
    } | Format-Table -AutoSize
