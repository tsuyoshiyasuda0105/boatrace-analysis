# DATABASE_URL を対話的に受け取り .env に保存 + 即同期実行
# 使い方: powershell -ExecutionPolicy Bypass -File scripts\setup_database_url.ps1

$ErrorActionPreference = "Stop"
$envFile = "C:\boat_project\boatrace-analysis\.env"
$pythonExe = "C:\boat_project\boatrace-analysis\.venv\Scripts\python.exe"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  DATABASE_URL セットアップ" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Render Dashboard で DATABASE_URL の値を表示しコピーしてください:" -ForegroundColor Yellow
Write-Host "  1. https://dashboard.render.com/" -ForegroundColor White
Write-Host "  2. boatrace-web -> Environment" -ForegroundColor White
Write-Host "  3. DATABASE_URL の目アイコン -> コピー" -ForegroundColor White
Write-Host ""
Write-Host "値の例:" -ForegroundColor Gray
Write-Host "  postgresql://postgres.xxx:xxx@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres" -ForegroundColor Gray
Write-Host ""

# 値を受け取る (右クリック貼り付けで PowerShell に貼れる)
$url = Read-Host -Prompt "ここに貼り付けて Enter (右クリック貼り付け)"
$url = $url.Trim()

if (-not $url) {
    Write-Host "[NG] 空のため終了します" -ForegroundColor Red
    exit 1
}

if (-not ($url -match "^postgres(ql)?://")) {
    Write-Host "[NG] postgresql:// で始まる値ではありません" -ForegroundColor Red
    Write-Host "    受け取った先頭: $($url.Substring(0, [Math]::Min(40, $url.Length)))..." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "[OK] 受信完了 (先頭40文字): $($url.Substring(0, [Math]::Min(40, $url.Length)))..." -ForegroundColor Green
Write-Host ""

# .env を更新 (DATABASE_URL の行を置換 or 追記)
$envContent = Get-Content $envFile -Raw -Encoding UTF8
if ($envContent -match "(?m)^DATABASE_URL=.*$") {
    $newContent = $envContent -replace "(?m)^DATABASE_URL=.*$", "DATABASE_URL=$url"
} else {
    $newContent = $envContent + "`nDATABASE_URL=$url`n"
}
Set-Content -Path $envFile -Value $newContent -Encoding UTF8 -NoNewline
Write-Host "[OK] .env に保存しました" -ForegroundColor Green
Write-Host ""

# 同期実行
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Supabase に 5/12 ~ 5/13 のデータを同期" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

Push-Location "C:\boat_project\boatrace-analysis"
try {
    Write-Host "[1/2] races / race_entries / race_results / race_payouts 同期..." -ForegroundColor Yellow
    & $pythonExe scripts\sync_to_supabase.py --date-from 2026-05-12 --date-to 2026-05-13
    Write-Host ""
    Write-Host "[2/2] 予測 (predictions) 同期..." -ForegroundColor Yellow
    & $pythonExe scripts\cache_predictions.py --date-from 2026-05-12 --date-to 2026-05-13 --sync
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  同期完了!" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "5 分後にブラウザで以下を開いて確認:" -ForegroundColor Yellow
    Write-Host "  https://boatrace-web.onrender.com/races?date=2026-05-13" -ForegroundColor White
} finally {
    Pop-Location
}
