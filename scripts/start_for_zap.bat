@echo off
REM ローカル Flask 起動 - ZAP スキャン用
REM 起動後、ZAP の攻撃対象を http://127.0.0.1:5000 に設定

cd /d %~dp0..
set FLASK_APP=src/web/app:create_app
set FLASK_ENV=production
set DISABLE_LIVE_PREDICT=false

REM 危険な heavy compute を有効化したい場合は上記を true ではなく未設定に
REM (ローカル開発なので heavy 計算してOK)

echo === Flask ローカル起動 (ZAP テスト用) ===
echo URL: http://127.0.0.1:5000
echo.
echo ZAP の攻撃対象に http://127.0.0.1:5000 を指定して攻撃してください
echo (Ctrl+C で停止)
echo.

.venv\Scripts\python.exe -m flask --app src.web.app:create_app run --host=127.0.0.1 --port=5000
