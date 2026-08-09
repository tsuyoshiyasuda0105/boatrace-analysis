# Register the PC nightly prepare task at 01:00 daily (25:00 JST operationally).
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\install_pc_nightly_prepare_task.ps1

$repo = "C:\Users\tsuyo\OneDrive\ドキュメント\New project 2\boatrace-main-deploy"
$batPath = Join-Path $repo "scripts\run_pc_nightly_prepare.bat"
$taskName = "BoatracePcNightlyPrepare"

if (-not (Test-Path -LiteralPath $batPath)) {
    Write-Error "Batch not found: $batPath"
    exit 1
}

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute $batPath
$trigger = New-ScheduledTaskTrigger -Daily -At "01:00"
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Prepare next-day boatrace SQLite data and sync the diff to Supabase at 01:00 daily."

Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo | Format-Table TaskName, NextRunTime, LastRunTime
