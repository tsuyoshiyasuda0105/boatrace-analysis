@echo off
REM sqlmap で SQL Injection 自動検査
REM 各 API エンドポイントの date パラメータをテスト

set TARGET=https://boatrace-web.onrender.com
set VENV_SQLMAP=%~dp0..\.venv\Scripts\sqlmap.exe

echo === sqlmap SQL Injection スキャン ===
echo 対象: %TARGET%
echo.

REM 1. /races?date= の date パラメータをテスト
echo --- Test 1: /races?date= ---
%VENV_SQLMAP% -u "%TARGET%/races?date=2026-05-11" --batch --level=2 --risk=1 --random-agent -o --output-dir=sqlmap_output

echo.
echo --- Test 2: /api/market-signals?date= ---
%VENV_SQLMAP% -u "%TARGET%/api/market-signals?date=2026-05-11" --batch --level=2 --risk=1 --random-agent -o --output-dir=sqlmap_output

echo.
echo === スキャン完了 ===
echo レポート: sqlmap_output/
echo.
echo 注意: SQL Injection が検出されない場合は「all parameters are not injectable」と出ます (=安全)
