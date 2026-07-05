# Register BoatraceBeforeinfoLive — scrape boatrace.jp beforeinfo every 10 min
# during race times, overwrite race_previews wind/wave/weather, re-predict and
# sync to Supabase. All hidden via run_hidden.vbs wrapper.
#
# Run ONCE manually:
#   powershell -ExecutionPolicy Bypass -File scripts\register_beforeinfo_live_task.ps1
#
# To remove later:
#   Unregister-ScheduledTask -TaskName 'BoatraceBeforeinfoLive' -Confirm:$false
#
# Strategy notes:
#   - Runs every 10 min (PT10M) from 08:00 to 22:00 (race hours).
#     Out of band runs early-exit because no races are "due".
#   - 5-9 min window: per-race scrape happens at most once per 8-10 min.
#     BAN risk is low because we only fetch races closing soon (1-3 races/run).
#   - Re-predicts the full day after writes (predict_date is batch-style).
#     ~30-90s of CPU per run during race hours.

$ErrorActionPreference = 'Stop'
$vbsPath = 'C:\boat_project\boatrace-analysis\scripts\run_hidden.vbs'
$batPath = 'C:\boat_project\boatrace-analysis\scripts\run_beforeinfo_live.bat'

if (-not (Test-Path $vbsPath)) { throw "VBS wrapper missing: $vbsPath" }
if (-not (Test-Path $batPath)) { throw "Launcher bat missing: $batPath" }

$argValue = "`"$vbsPath`" `"$batPath`""
$action   = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument $argValue

# Daily trigger at 08:00, repeating every 10 min until 22:00.
$start = (Get-Date).Date.AddHours(8)   # today 08:00
$trigger = New-ScheduledTaskTrigger -Daily -At $start
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $start `
    -RepetitionInterval (New-TimeSpan -Minutes 10) `
    -RepetitionDuration (New-TimeSpan -Hours 14)).Repetition

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName 'BoatraceBeforeinfoLive' `
                       -Action $action `
                       -Trigger $trigger `
                       -Settings $settings `
                       -Description 'Live beforeinfo scrape (5-9min before close), overwrites race_previews wind/wave/weather, re-predicts, syncs to Supabase. Hidden via VBS.' `
                       -Force | Out-Null

Write-Host '[OK] BoatraceBeforeinfoLive registered' -ForegroundColor Green
Write-Host '  Schedule: daily 08:00, repeat every 10min for 14h (until 22:00)'
Write-Host '  Action  : wscript.exe + run_hidden.vbs + run_beforeinfo_live.bat'
Write-Host '  Log     : logs\beforeinfo_live_<YYYYMMDD>.log'

Get-ScheduledTask -TaskName 'BoatraceBeforeinfoLive' |
    Select-Object TaskName, State |
    Format-Table -AutoSize
