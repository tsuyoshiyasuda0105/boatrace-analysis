@echo off
REM Daily collect: runs daily_collect.py for final daily refresh (23:30)
REM Refreshes both Supabase (Render UI) and local SQLite (backtest)
cd /d C:\boat_project\boatrace-analysis
if not exist logs mkdir logs

set TS=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG=logs\daily_collect_%TS%.log

echo. >> "%LOG%"
echo === Daily collect started %date% %time% === >> "%LOG%"

REM 1. Supabase (default) - .env DATABASE_URL applied automatically
.venv\Scripts\python.exe scripts\daily_collect.py >> "%LOG%" 2>&1

REM 2. Local SQLite (--local explicitly skips DATABASE_URL)
.venv\Scripts\python.exe scripts\daily_collect.py --local >> "%LOG%" 2>&1

REM 3. l4_daily_summary 同期 (backlog item 19/20: ROI ダッシュボード反映)
REM    daily_collect は生データだけ書く。L4 [A1] 集計値は別途 sync で生成
REM    する必要があり、これが抜けていたため 5/17 以降のロウが欠落していた。
REM    過去 5 日分を再計算して上書き (race_payouts 確定遅延に耐性)。
.venv\Scripts\python.exe scripts\sync_l4_summary_to_supabase.py --recent-days 5 >> "%LOG%" 2>&1

REM 4. DB 容量監視 + 自動クリーンアップ (backlog item 12)
REM    日次で Supabase 使用量チェック。--auto により 80% 超で自動的に
REM    生データ (race_entries / race_payouts / ... の 90 日以前) を削除。
REM    l4_daily_summary に集計済の日付のみ削除されるため ROI 表示に影響なし。
REM    Local SQLite には全データ温存 (バックテスト用)。
.venv\Scripts\python.exe scripts\db_size_check.py --auto --keep-raw-days 90 >> "%LOG%" 2>&1

echo === Daily collect finished %date% %time% === >> "%LOG%"
exit /b 0
