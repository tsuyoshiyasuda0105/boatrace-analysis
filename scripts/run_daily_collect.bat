@echo off
if exist C:\boat_project\boatrace-analysis\.pc_schedule_paused exit /b 0
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

REM Tide data -> Supabase
.venv\Scripts\python.exe scripts\fetch_and_import_jma_tides.py --year-from %date:~0,4% --year-to %date:~0,4% --only-missing --timeout 30 >> "%LOG%" 2>&1

REM Tide data -> local SQLite
.venv\Scripts\python.exe scripts\fetch_and_import_jma_tides.py --db C:\boat_project\boatrace-analysis\data\boatrace.db --year-from %date:~0,4% --year-to %date:~0,4% --only-missing --timeout 30 >> "%LOG%" 2>&1

REM 3. l4_daily_summary 同期 (backlog item 19/20: ROI ダッシュボード反映)
REM    daily_collect は生データだけ書く。L4 [A1] 集計値は別途 sync で生成
REM    する必要があり、これが抜けていたため 5/17 以降のロウが欠落していた。
REM    過去 5 日分を再計算して上書き (race_payouts 確定遅延に耐性)。
.venv\Scripts\python.exe scripts\sync_l4_summary_to_supabase.py --recent-days 5 >> "%LOG%" 2>&1

REM 3.5. 翌日 Layer 1 番組表を前夜に事前取得 (ユーザ要望 2026-05-19)
REM      boatrace.jp 公式 LZH は前日 23:00 過ぎに公開される (boatraceopenapi
REM      の Open API は当日 0:00 過ぎまで公開されない)。
REM      これで翌日朝の morning_task より早く出走表/選手情報が揃う。
REM      Supabase (Render UI) と Local SQLite (backtest) 両方に投入。
.venv\Scripts\python.exe scripts\backfill_official.py --tomorrow >> "%LOG%" 2>&1
.venv\Scripts\python.exe scripts\backfill_official.py --tomorrow --local >> "%LOG%" 2>&1

REM 3.6. 翌日 予測キャッシュ生成 → Supabase 同期
REM      L4 候補バッジ (🌅L4参考 / 🌅L4 G++ 等) は predictions の prob_first
REM      に依存。Layer 1 で出走表は揃うが、予測は別計算が必要。
REM      これにより前夜から翌日の「お金を入れる候補レース」が画面表示される。
.venv\Scripts\python.exe scripts\cache_predictions.py --tomorrow --sync >> "%LOG%" 2>&1

REM 4. DB 容量監視 + 自動クリーンアップ (backlog item 12)
REM    日次で Supabase 使用量チェック。--auto により 80% 超で自動的に
REM    生データ (race_entries / race_payouts / ... の 90 日以前) を削除。
REM    l4_daily_summary に集計済の日付のみ削除されるため ROI 表示に影響なし。
REM    Local SQLite には全データ温存 (バックテスト用)。
.venv\Scripts\python.exe scripts\db_size_check.py --cleanup --auto --keep-days 30 --keep-raw-days 90 >> "%LOG%" 2>&1

REM 5. タスク実行を task_runs に記録 (起動時キャッチアップの判定根拠)
.venv\Scripts\python.exe scripts\record_task_run.py daily_collect success >> "%LOG%" 2>&1

echo === Daily collect finished %date% %time% === >> "%LOG%"
exit /b 0
