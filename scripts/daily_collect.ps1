# ============================================================
# BOATRACE - Daily Open API Collection Wrapper
#   schtasks から呼ばれる前提。
#   - venv の python.exe を絶対パスで起動
#   - stdout/stderr を logs/daily_collect_YYYYMMDD.log に追記
#   - 失敗時は最大3回・5分間隔でリトライ
# ============================================================

$ErrorActionPreference = "Continue"

$ProjectRoot = "C:\boat_project\boatrace-analysis"
$Python      = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Script      = Join-Path $ProjectRoot "scripts\daily_collect.py"
$LogDir      = Join-Path $ProjectRoot "logs"

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$LogFile = Join-Path $LogDir ("daily_collect_{0:yyyyMMdd}.log" -f (Get-Date))

Add-Content $LogFile ("[INFO] {0:yyyy-MM-dd HH:mm:ss} starting daily_collect.py" -f (Get-Date))

if (-not (Test-Path $Python)) {
    Add-Content $LogFile "[ERROR] python.exe not found at $Python"
    exit 2
}
if (-not (Test-Path $Script)) {
    Add-Content $LogFile "[ERROR] daily_collect.py not found at $Script"
    exit 2
}

$maxAttempts = 3
for ($i = 1; $i -le $maxAttempts; $i++) {
    Add-Content $LogFile ("[INFO] attempt {0}/{1}" -f $i, $maxAttempts)
    # Python 側で --log-file に UTF-8 で直接書き込む (PowerShell redirect の UTF-16 LE を回避)
    & $Python $Script --verbose --log-file $LogFile | Out-Null
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        Add-Content $LogFile ("[INFO] {0:yyyy-MM-dd HH:mm:ss} success" -f (Get-Date))
        exit 0
    }
    Add-Content $LogFile ("[WARN] attempt {0} failed exit={1}" -f $i, $code)
    if ($i -lt $maxAttempts) {
        Start-Sleep -Seconds 300
    }
}

Add-Content $LogFile ("[ERROR] {0:yyyy-MM-dd HH:mm:ss} all {1} attempts failed" -f (Get-Date), $maxAttempts)
exit 1
