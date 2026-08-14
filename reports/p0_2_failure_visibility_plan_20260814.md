# 作業指示書: P0-2 失敗の見える化 (障害にお客様より先に気づく)

作成: 2026-08-14 / 発注者: リッキー / 単体で完結する指示書。
リポジトリ: `C:\boat_project\boatrace-analysis` (正本。他の場所に checkout を作らない)
背景: 監査 [reports/codebase_audit_20260813.md] の P0-2。現状は「戦略評価が壊れても
無音」「cron が失敗しても通知ゼロ」で、バグ発見が数週間遅れる。有料販売の前提として
「利用者より先に障害へ気づける」体制を作る。

## 絶対に守るルール

1. **origin/main への push 禁止** (push=本番デプロイ)。コミットはローカル main まで。
2. ROI 戦略ロジック・予測ロジック・DB スキーマの変更禁止。
   (通知やカウンタの追加であり、判定条件・数値は 1 文字も変えない)
3. 作業ログを `reports/p0_2_work_log_20260814.md` に記録 (変更・テスト・失敗も正直に)。
4. テスト: `.venv/Scripts/python.exe -m pytest tests/ -q`。既存16失敗を増やさない。
5. **P0-3 指示書 (reports/p0_3_ban_mitigation_plan_20260814.md) と並行作業の可能性あり。**
   P0-3 は `src/collectors/` `src/parsers/` 中心。本作業がそこを触る必要が出たら
   停止して報告 (衝突防止)。

## 現状の事実 (2026-08-14 に行番号まで検証済み)

- `src/web/app.py:14050` `_safe_signal_eval(name, fn, ...)` — 戦略評価の例外を握りつぶし
  `None` (=条件不成立と同じ) を返す。呼び出し 77 箇所。warning ログのみ。
- `scripts/render_maintenance_scheduler.py:317` `main()` — `:331` で常に `return 0`。
- `scripts/render_program_bootstrap_scheduler.py:581` `main()` — `:591` で常に `return 0`。
  → Render の cron 失敗通知が両ジョブで永久に無効。
- `src/notifications/error_handler.py:83` `install_error_notifier` — 配線先は
  `src/web/app.py:5928` (Flask) のみ。**cron は 1 本も通知しない**。
- `scripts/refresh_race_detail_after_exhibition.py:612` — 多重実行スキップ時に
  `record_cron_run(task_name, args.date, "success", ...)` — **やっていないのに成功記録**。
- `src/web/app.py:1580-1622` — 管理画面が `boatrace-race-detail-cron` に
  `"毎日 04:00 JST"` 表記 (実際は render.yaml で 04:00-06:59 の10分毎、中身は
  maintenance coordinator)。`render_maintenance_*` / `render_program_bootstrap_*` の
  task_runs 行は管理画面に一切表示されない。

## タスク

### タスク1: 戦略評価の失敗カウンタ (最優先)

1. `_safe_signal_eval` に失敗記録を追加:
   - プロセス内カウンタ `{strategy_name: {count, last_error, last_at}}` を保持。
   - 同一 (日付, strategy_name) の初回失敗時のみ `system_status` へ
     `check_name='signal_eval_failure'`, `status='warning'`, `message=戦略名と例外種別`
     で記録 (既存の system_status 書き込みパターンに合わせる。スパム防止のため
     同日同戦略は1回)。
2. `/admin/data-status` に「戦略評価エラー」セクションを追加し、当日の失敗戦略名・
   件数・最終エラーを表示。ゼロ件なら「なし」と明示。
3. 回帰テスト: 例外を投げる fn を渡すと (a) None が返る (従来動作維持)、
   (b) カウンタ・system_status に記録される、(c) 同日2回目は system_status に
   重複記録されない。

### タスク2: cron 終了コードの正直化

対象: `render_maintenance_scheduler.py` / `render_program_bootstrap_scheduler.py`

1. 「次 tick で再開したいので途中失敗は 0 で返す」という既存意図は**維持してよい**。
   ただし以下の場合は**非0で返す**:
   - maintenance: 自動窓 (04:00-07:00) の最終判定で `degraded` (未完フェーズあり) の場合
   - bootstrap: `ALERT_TASK` (07:30) 発火時点でソース未解決の場合
   つまり「まだリトライが残っている失敗=0 / もうリトライが無い最終失敗=非0」。
2. 非0 return と同時に `system_status` へ error を記録 (既存 `_write_status` 等を流用)。
3. 既存のスケジューラ系テストが「常に0」を前提にしていれば、意図を反映した形へ更新。

### タスク3: cron へのメール通知

1. `src/notifications/error_handler.py` の仕組みを流用し、cron 用のヘルパー
   (例: `notify_cron_failure(job, message)`) を追加。
   - クールダウン: 同一 job は 6 時間に 1 通まで (連続失敗のスパム防止)。
     クールダウン状態は `system_status` か `task_runs` の既存行で管理し、
     新テーブルは作らない。
2. 配線対象 (最終失敗のみ・タスク2の非0経路から呼ぶ):
   - `render_maintenance_scheduler.py` (窓終了時 degraded)
   - `render_program_bootstrap_scheduler.py` (07:30 未解決)
3. メール送信は `src/notifications/mailer.py` の既存送信経路を使う。宛先は既存の
   管理者宛設定に従う。**新しい送信先をコードに直書きしない。**
4. テスト: 送信関数を mock し、(a) 最終失敗で1回呼ばれる、(b) クールダウン中は
   呼ばれない、を検証。

### タスク4: 管理ダッシュボードの整合

1. `src/web/app.py` の admin data-status に `render_maintenance_*` と
   `render_program_bootstrap_*` の task_runs 最新状態を表示する行を追加。
2. スケジュール表記を render.yaml の実態に合わせて修正:
   - `boatrace-race-detail-cron`: 「04:00-07:00 JST の10分毎 (夜間メンテ統括)」
   - 「毎日 07:30 JST 付近」等の死んだ表記も実態へ (render.yaml が正)。
3. 表記文字列は 1 箇所の定数/辞書にまとめ、複数行へのコピペを避ける。

### タスク5: 偽装成功の修正

1. `refresh_race_detail_after_exhibition.py:612` のスキップ時 `"success"` 記録を
   `"skipped"` に変更。
2. `task_runs.status='skipped'` を読む側 (管理画面・agent_monitor 等) が
   未知ステータスで壊れないことを grep で確認し、必要なら表示対応。
3. 回帰テスト: スキップ経路で success が記録されないこと。

## 受け入れ条件

- [ ] 戦略評価に例外を仕込むと管理画面で見える (テストで検証)
- [ ] maintenance/bootstrap の「最終失敗」が exit 非0 + system_status error になる
- [ ] 最終失敗でメール通知関数が呼ばれ、クールダウンが効く (mock テスト)
- [ ] 管理画面に maintenance/bootstrap の状態が表示され、スケジュール表記が実態と一致
- [ ] スキップは skipped として記録される
- [ ] pytest: 既存16失敗から増えていない。新規テスト全 green
- [ ] push していない (デプロイは発注者ゲート)。作業ログに「デプロイ待ち」を明記

## やらないこと (スコープ外)

- 57箇所の `except: pass` の全面除去 (P1 で段階的に)
- 回復ロジックの再設計 (P1-4)
- 通知チャネルの新設 (LINE/Slack 等) — メールのみ
