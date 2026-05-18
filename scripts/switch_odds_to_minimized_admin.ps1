# ===== Run THIS script AS ADMINISTRATOR =====
#
# BoatraceOddsScheduler は RunLevel=Highest (UAC 昇格) で登録されているため、
# 通常ユーザ権限 PowerShell からは Set-ScheduledTask が "Access is denied"
# になります。
#
# 手順:
#   1. スタートメニューで "PowerShell" を右クリック → 「管理者として実行」
#   2. cd C:\boat_project\boatrace-analysis
#   3. powershell -ExecutionPolicy Bypass -File scripts\switch_odds_to_minimized_admin.ps1
#
# 実行後の効果 (backlog item 1):
#   毎分起動する BoatraceOddsScheduler のコマンドプロンプトが
#   タスクバーに最小化された状態で表示され、ユーザは
#   「ボートレース処理が動いている」ことを視覚的に確認できる。

$ErrorActionPreference = 'Stop'

# Admin check
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$pr = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "管理者権限が必要です。PowerShell を 'Run as Administrator' で起動してから再実行してください。"
    exit 1
}

$taskName = 'BoatraceOddsScheduler'
$vbsPath  = 'C:\boat_project\boatrace-analysis\scripts\run_minimized.vbs'
$batPath  = 'C:\boat_project\boatrace-analysis\scripts\run_odds_scheduler.bat'

if (-not (Test-Path $vbsPath)) { Write-Error "VBS missing: $vbsPath"; exit 1 }
if (-not (Test-Path $batPath)) { Write-Error "BAT missing: $batPath"; exit 1 }

$argValue = "`"$vbsPath`" `"$batPath`""
$newAction = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument $argValue

$maxRetries = 8
$waitSec = 8
for ($i = 1; $i -le $maxRetries; $i++) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
    if ($task.Actions[0].Arguments -like "*run_minimized.vbs*") {
        Write-Host "Already minimized. Done." -ForegroundColor Green
        exit 0
    }
    try {
        $task.Actions = @($newAction)
        Set-ScheduledTask -InputObject $task -ErrorAction Stop | Out-Null
        Write-Host "[attempt $i] -> wscript.exe $argValue" -ForegroundColor Green
        exit 0
    } catch {
        Write-Host "[attempt $i] FAILED: $_" -ForegroundColor Yellow
        if ($i -lt $maxRetries) { Start-Sleep -Seconds $waitSec }
    }
}

Write-Error "Could not modify $taskName after $maxRetries attempts."
exit 1
