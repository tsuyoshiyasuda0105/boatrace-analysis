# Switch all Boatrace scheduled tasks to run completely hidden via VBS wrapper.
#
# 2026-05-16 rewrite: replaced `cmd /c schtasks` (which had broken quote
# escaping — argument paths starting with letters like "M" were re-parsed
# as commands and produced "M は内部コマンドまたは外部コマンドとして認識
# されていません" errors) with native PowerShell Set-ScheduledTask. No
# more cmd-level quote hell.
#
# Why this script exists separately:
#   Modifying scheduled tasks is a persistence operation — AI agents
#   should not perform autonomously. Run this ONCE after reviewing.
#
# Prereq:
#   scripts\run_hidden.vbs must be ASCII-only (fixed 2026-05-16). The
#   original UTF-8 version silently failed under wscript.exe on
#   Japanese Windows (CP932 codepage).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\update_tasks_hidden.ps1
#
# To revert one task back to direct bat call:
#   $t = Get-ScheduledTask -TaskName "<TaskName>"
#   $t.Actions = New-ScheduledTaskAction -Execute "<path-to-bat>"
#   Set-ScheduledTask -InputObject $t

$ErrorActionPreference = 'Continue'
$vbsPath = 'C:\boat_project\boatrace-analysis\scripts\run_hidden.vbs'

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
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $task) {
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
    $current = $task.Actions[0].Execute
    Write-Host "  current Execute: $current"

    # Build new action: wscript.exe "<vbs>" "<bat>"
    # Argument value MUST itself contain the embedded quotes around each path,
    # otherwise wscript treats the whole string as one argument with spaces.
    $argValue = "`"$vbsPath`" `"$batPath`""
    $newAction = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument $argValue

    try {
        # Replace Actions in-place and persist via Set-ScheduledTask. This
        # avoids cmd-level quoting (which caused the prior 'M' parsing error).
        $task.Actions = @($newAction)
        Set-ScheduledTask -InputObject $task -ErrorAction Stop | Out-Null
        Write-Host "  -> wscript.exe $argValue" -ForegroundColor Green
        $success++
    } catch {
        Write-Host "  FAILED: $_" -ForegroundColor Red
        $failed += "$taskName ($_)"
    }
}

Write-Host ''
Write-Host '============================================================'
Write-Host "  Done. $success/$($tasks.Count) tasks switched to hidden."
if ($failed.Count -gt 0) {
    Write-Host '  Failures:' -ForegroundColor Red
    foreach ($f in $failed) { Write-Host "    - $f" -ForegroundColor Red }
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
