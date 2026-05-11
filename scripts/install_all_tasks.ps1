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
