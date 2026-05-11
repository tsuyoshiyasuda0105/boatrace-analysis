$TaskName = "BoatraceOddsScheduler"
$BatPath = "C:\boat_project\boatrace-analysis\scripts\run_odds_scheduler.bat"

Write-Host "============================================================"
Write-Host "Boatrace Odds Scheduler Setup (using schtasks.exe)"
Write-Host "============================================================"

# Admin check
$currentUser = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$isAdmin = $currentUser.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Warning "Not running as Administrator. Task registration may fail."
    Write-Host "Please re-run PowerShell as Administrator and try again."
}

# Verify bat exists
if (-not (Test-Path $BatPath)) {
    Write-Error "Batch file not found: $BatPath"
    exit 1
}

# Remove existing task if any
schtasks /Query /TN $TaskName 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Removing existing task '$TaskName'..."
    schtasks /Delete /TN $TaskName /F | Out-Null
}

# Register new task: run every minute, indefinitely
# /SC MINUTE /MO 1  = every 1 minute
# /ST 00:00         = start time
# /DU 9999:00       = duration (effectively infinite)
# /RU SYSTEM        = run as SYSTEM (no logon required)
# /RL HIGHEST       = highest privileges

Write-Host "Registering task..."
$result = & schtasks /Create /TN $TaskName /TR "`"$BatPath`"" /SC MINUTE /MO 1 /RL HIGHEST /F 2>&1

Write-Host $result

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "============================================================"
    Write-Host "[OK] Task '$TaskName' registered."
    Write-Host "  Trigger: every 1 minute"
    Write-Host "  Action:  $BatPath"
    Write-Host "  Log:     C:\boat_project\boatrace-analysis\logs\odds_scheduler.log"
    Write-Host ""
    Write-Host "Verify:"
    Write-Host "  schtasks /Query /TN $TaskName"
    Write-Host ""
    Write-Host "View log after 5 minutes:"
    Write-Host "  Get-Content C:\boat_project\boatrace-analysis\logs\odds_scheduler.log -Tail 20"
    Write-Host ""
    Write-Host "Remove:"
    Write-Host "  schtasks /Delete /TN $TaskName /F"
    Write-Host "============================================================"
} else {
    Write-Error "Task registration failed. Exit code: $LASTEXITCODE"
    Write-Host "Please run this script as Administrator."
    exit 1
}
