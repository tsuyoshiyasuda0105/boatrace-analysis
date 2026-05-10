# ============================================================
# BOATRACE - Layer 3 Daily Scraping Wrapper (parts + odds)
# ============================================================

$ErrorActionPreference = "Continue"

$ProjectRoot = "C:\boat_project\boatrace-analysis"
$Python      = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Script      = Join-Path $ProjectRoot "scripts\scrape_layer3.py"
$LogDir      = Join-Path $ProjectRoot "logs"

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$LogFile = Join-Path $LogDir ("layer3_{0:yyyyMMdd}.log" -f (Get-Date))

Add-Content $LogFile ("[INFO] {0:yyyy-MM-dd HH:mm:ss} starting scrape_layer3.py" -f (Get-Date))

if (-not (Test-Path $Python)) {
    Add-Content $LogFile "[ERROR] python.exe not found at $Python"
    exit 2
}
if (-not (Test-Path $Script)) {
    Add-Content $LogFile "[ERROR] scrape_layer3.py not found at $Script"
    exit 2
}

# Layer 3 はリトライしない (REQUEST_INTERVAL_SECONDS でサイト負荷管理しつつ
# Python 側で 5xx 自動リトライ済み。失敗時は翌日タスクで未取得分を拾う)
& $Python $Script --targets parts,odds --verbose *>> $LogFile
$code = $LASTEXITCODE
if ($code -eq 0) {
    Add-Content $LogFile ("[INFO] {0:yyyy-MM-dd HH:mm:ss} success" -f (Get-Date))
    exit 0
}
Add-Content $LogFile ("[ERROR] {0:yyyy-MM-dd HH:mm:ss} failed exit={1}" -f (Get-Date), $code)
exit 1
