# Install all Boatrace scheduled tasks at once
# Run as Administrator: powershell -ExecutionPolicy Bypass -File scripts\install_all_tasks.ps1

$base = "C:\boat_project\boatrace-analysis\scripts"

$tasks = @(
    @{
        Name = "BoatraceOddsScheduler"
        Bat = "$base\run_odds_scheduler.bat"
        Schedule = "MINUTE"
        Modifier = "1"
        Desc = "Odds T-15/T-5/T-1 snapshot collector (every minute)"
    },
    @{
        Name = "BoatraceDailyCollect"
        Bat = "$base\run_daily_collect.bat"
        Schedule = "DAILY"
        StartTime = "06:00"
        Desc = "Daily race data collection (06:00 every day)"
    },
    @{
        Name = "BoatraceSyncSupabase"
        Bat = "$base\run_sync_to_supabase.bat"
        Schedule = "DAILY"
        StartTime = "23:30"
        Desc = "Sync local data to Supabase (23:30 every night)"
    }
)

Write-Host "============================================================"
Write-Host "Boatrace All Tasks Setup"
Write-Host "============================================================"

# Admin check
$currentUser = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentUser.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Not running as Administrator. Please re-run as Administrator."
    exit 1
}

foreach ($task in $tasks) {
    $name = $task.Name
    $bat = $task.Bat

    if (-not (Test-Path $bat)) {
        Write-Warning "Skipping $name : batch file not found at $bat"
        continue
    }

    Write-Host ""
    Write-Host "[$name]"

    # Remove existing
    schtasks /Query /TN $name 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Removing existing task..."
        schtasks /Delete /TN $name /F | Out-Null
    }

    # Build schtasks command
    $cmd = @("schtasks", "/Create", "/TN", $name, "/TR", "`"$bat`"",
             "/SC", $task.Schedule, "/RL", "HIGHEST", "/F")
    if ($task.Modifier) {
        $cmd += @("/MO", $task.Modifier)
    }
    if ($task.StartTime) {
        $cmd += @("/ST", $task.StartTime)
    }

    Write-Host "  Registering: $($cmd -join ' ')"
    & $cmd[0] $cmd[1..($cmd.Length-1)]
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] $name registered"
    } else {
        Write-Error "  Failed to register $name (exit $LASTEXITCODE)"
    }
}

# ----- Startup catch-up task (ONSTART trigger) -----
# Re-runs missed daily_collect / morning / hourly / poll_results on PC startup
# (in case the PC was down/asleep at the scheduled time).
# 3-minute delay (/DELAY 0003:00) to allow network to come up after boot.
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads .ps1 without a
# BOM as the system ANSI codepage, so non-ASCII (e.g. Japanese) comments here
# get mis-decoded and can swallow the following line.
$catchupName = "BoatraceStartupCatchup"
$catchupBat  = "$base\run_startup_catchup.bat"
Write-Host ""
Write-Host "[$catchupName]"
if (Test-Path $catchupBat) {
    schtasks /Query /TN $catchupName 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Removing existing task..."
        schtasks /Delete /TN $catchupName /F | Out-Null
    }
    schtasks /Create /TN $catchupName /TR "`"$catchupBat`"" /SC ONSTART /DELAY 0003:00 /RL HIGHEST /F
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] $catchupName registered (ONSTART +3min)"
    } else {
        Write-Error "  Failed to register $catchupName (exit $LASTEXITCODE)"
    }
} else {
    Write-Warning "  Skipping $catchupName : batch file not found at $catchupBat"
}

# ----- Make tasks run hidden (no console window popups) -----
# schtasks registers tasks that launch the .bat directly, so a console
# window pops up on every run (e.g. OddsScheduler every minute). Rewrite
# each task to run via wscript.exe + run_hidden.vbs so no window appears.
# This MUST run after task (re)creation, otherwise re-installing reverts
# tasks to the visible .bat action.
Write-Host ""
Write-Host "[Hiding task windows]"
$hideScript = "$base\update_tasks_hidden.ps1"
$fixOdds    = "$base\fix_odds_scheduler_hidden.ps1"
if (Test-Path $hideScript) {
    & $hideScript
    # OddsScheduler runs every minute and may be busy mid-run; retry it.
    if (Test-Path $fixOdds) { & $fixOdds }
} else {
    Write-Warning "  update_tasks_hidden.ps1 not found; tasks will show a window."
}

Write-Host ""
Write-Host "============================================================"
Write-Host "Verification:"
Write-Host "  schtasks /Query | findstr Boatrace"
Write-Host ""
Write-Host "View logs:"
Write-Host "  Get-Content C:\boat_project\boatrace-analysis\logs\odds_scheduler.log -Tail 20"
Write-Host "  Get-Content C:\boat_project\boatrace-analysis\logs\daily_collect_local.log -Tail 20"
Write-Host "  Get-Content C:\boat_project\boatrace-analysis\logs\sync_to_supabase.log -Tail 20"
Write-Host "============================================================"
