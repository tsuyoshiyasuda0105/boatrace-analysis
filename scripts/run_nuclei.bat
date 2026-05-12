@echo off
REM Nuclei セキュリティスキャン (Java 不要、Go バイナリ)
REM 使い方: nuclei.exe を PATH に置くか、フルパス指定して実行
REM    scripts\run_nuclei.bat
REM
REM 約 8000 種類の脆弱性テンプレートで対象サイトを攻撃テスト
REM CVE, exposed-panels, default-logins, misconfig 等を網羅

set TARGET=https://boatrace-web.onrender.com

echo === Nuclei セキュリティスキャン ===
echo 対象: %TARGET%
echo.

REM nuclei.exe がパスにあれば nuclei、無ければフルパス指定
where nuclei.exe >nul 2>&1
if %errorlevel%==0 (
    nuclei -u %TARGET% -severity critical,high,medium -o nuclei_report.txt
) else (
    echo nuclei.exe が見つかりません。
    echo https://github.com/projectdiscovery/nuclei/releases から DL してパスに置いてください。
    exit /b 1
)

echo.
echo === スキャン完了 ===
echo レポート: nuclei_report.txt
