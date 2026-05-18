# Switch 1-minute scheduled tasks from run_hidden.vbs (SW_HIDE) to
# run_minimized.vbs (SW_SHOWMINNOACTIVE) so the user can see them on the
# taskbar — backlog item 1 (2026-05-18).
#
# Why retry: BoatraceOddsScheduler fires every minute. If Set-ScheduledTask
# coincides with a running instance, it returns "アクセスが拒否されました".
# We retry up to 8 times with 8s waits (~70s window catches a quiet slot).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\switch_to_minimized.ps1

$ErrorActionPreference = 'Continue'
$vbsPath = 'C:\boat_project\boatrace-analysis\scripts\run_minimized.vbs'

$targets = @(
    @{ Name = 'BoatraceOddsScheduler';  Bat = 'C:\boat_project\boatrace-analysis\scripts\run_odds_scheduler.bat' },
    @{ Name = 'BoatraceL4Alert';        Bat = 'C:\boat_project\boatrace-analysis\scripts\run_l4_alert.bat' }
)

if (-not (Test-Path $vbsPath)) {
    Write-Error "run_minimized.vbs missing: $vbsPath"
    exit 1
}

$maxRetries = 8
$waitSec = 8

foreach ($t in $targets) {
    $taskName = $t.Name
    $batPath  = $t.Bat
    Write-Host "`n=== Switching $taskName ===" -ForegroundColor Cyan
    if (-not (Test-Path $batPath)) {
        Write-Warning "bat missing for $taskName : $batPath (skip)"
        continue
    }

    $argValue = "`"$vbsPath`" `"$batPath`""
    $newAction = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument $argValue

    $done = $false
    for ($i = 1; $i -le $maxRetries; $i++) {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if (-not $task) { Write-Error "Task not found: $taskName"; break }

        if ($task.Actions[0].Arguments -like "*run_minimized.vbs*") {
            Write-Host "  Already minimized. Skip." -ForegroundColor Green
            $done = $true
            break
        }

        try {
            $task.Actions = @($newAction)
            Set-ScheduledTask -InputObject $task -ErrorAction Stop | Out-Null
            Write-Host "  [attempt $i] -> wscript.exe $argValue" -ForegroundColor Green
            $done = $true
            break
        } catch {
            Write-Host "  [attempt $i] FAILED: $_" -ForegroundColor Yellow
            if ($i -lt $maxRetries) { Start-Sleep -Seconds $waitSec }
        }
    }

    if (-not $done) {
        Write-Error "Could not modify $taskName after $maxRetries attempts."
    }
}

Write-Host "`n=== Final state ===" -ForegroundColor Cyan
foreach ($t in $targets) {
    $task = Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
    if ($task) {
        Write-Host ("{0,-25} args={1}" -f $t.Name, $task.Actions[0].Arguments)
    }
}
