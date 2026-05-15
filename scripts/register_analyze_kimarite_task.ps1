# Register BoatraceAnalyzeKimarite — runs analyze_kimarite.py daily at 06:00,
# cmd window completely hidden (via run_hidden.vbs wrapper).
#
# Why this script exists:
#   Register-ScheduledTask is a persistence operation that AI agents should
#   not perform autonomously. Run this ONCE manually after reviewing.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\register_analyze_kimarite_task.ps1
#
# To remove later:
#   Unregister-ScheduledTask -TaskName 'BoatraceAnalyzeKimarite' -Confirm:$false

$ErrorActionPreference = 'Stop'
$vbsPath = 'C:\boat_project\boatrace-analysis\scripts\run_hidden.vbs'
$batPath = 'C:\boat_project\boatrace-analysis\scripts\run_analyze_kimarite.bat'

if (-not (Test-Path $vbsPath)) { throw "VBS wrapper missing: $vbsPath" }
if (-not (Test-Path $batPath)) { throw "Launcher bat missing: $batPath" }

$action   = New-ScheduledTaskAction -Execute 'wscript.exe' `
                                    -Argument "`"$vbsPath`" `"$batPath`""
$trigger  = New-ScheduledTaskTrigger -Daily -At 6:00am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
                                          -DontStopIfGoingOnBatteries `
                                          -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName 'BoatraceAnalyzeKimarite' `
                       -Action $action `
                       -Trigger $trigger `
                       -Settings $settings `
                       -Description 'Daily kimarite cross-tab analysis (hidden via VBS). Output in logs/analyze_kimarite.log.' `
                       -Force | Out-Null

Write-Host '[OK] BoatraceAnalyzeKimarite registered (daily 06:00, hidden)'
Get-ScheduledTask -TaskName 'BoatraceAnalyzeKimarite' |
    Select-Object TaskName, State |
    Format-Table -AutoSize
