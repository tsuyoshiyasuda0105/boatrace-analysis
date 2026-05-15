# Retry-style fix for BoatraceOddsScheduler — the 1-min task that
# update_tasks_hidden.ps1 may fail to modify because it's actively running
# at the moment of Set-ScheduledTask ("アクセスが拒否されました。").
#
# Strategy: retry up to 8 times, waiting 8 seconds between attempts.
# 8 attempts * (1 attempt + 8s wait) covers ~70 seconds, guaranteed to hit
# a quiet window between the 60-second triggers.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\fix_odds_scheduler_hidden.ps1

$ErrorActionPreference = 'Continue'
$taskName = 'BoatraceOddsScheduler'
$vbsPath  = 'C:\boat_project\boatrace-analysis\scripts\run_hidden.vbs'
$batPath  = 'C:\boat_project\boatrace-analysis\scripts\run_odds_scheduler.bat'
$maxRetries = 8
$waitSec = 8

if (-not (Test-Path $vbsPath)) { Write-Error "VBS missing: $vbsPath"; exit 1 }
if (-not (Test-Path $batPath)) { Write-Error "bat missing: $batPath"; exit 1 }

$argValue = "`"$vbsPath`" `"$batPath`""
$newAction = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument $argValue

for ($i = 1; $i -le $maxRetries; $i++) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $task) { Write-Error "Task not found: $taskName"; exit 1 }

    $info = Get-ScheduledTaskInfo $taskName
    $currentExec = $task.Actions[0].Execute
    Write-Host "[attempt $i/$maxRetries] state=$($task.State) lastRun=$($info.LastRunTime) currentExec=$currentExec"

    if ($currentExec -eq 'wscript.exe') {
        Write-Host '  Already hidden. Done.' -ForegroundColor Green
        exit 0
    }

    try {
        $task.Actions = @($newAction)
        Set-ScheduledTask -InputObject $task -ErrorAction Stop | Out-Null
        Write-Host "  -> wscript.exe $argValue" -ForegroundColor Green
        Write-Host "  Success on attempt $i." -ForegroundColor Green
        exit 0
    } catch {
        Write-Host "  FAILED: $_" -ForegroundColor Yellow
        if ($i -lt $maxRetries) {
            Write-Host "  Waiting ${waitSec}s before retry..." -ForegroundColor DarkGray
            Start-Sleep -Seconds $waitSec
        }
    }
}

Write-Host ''
Write-Error "Could not modify $taskName after $maxRetries attempts."
exit 1
