# ゾンビ running タスク自動回収 作業ログ

作業日: 2026-08-15

## 実施内容

- `src/db/cron_runtime.py` に `reap_stale_running_tasks(conn, *, older_than_hours=6, now=None)` を追加。
- `status='running'`、`finished_at IS NULL`、`started_at` が閾値より厳密に古い、の3条件を満たす行だけを `failure` / `stale_running_reaped` に更新し、回収件数を返すようにした。
- `scripts/render_regular_scheduler.py` の通常tick冒頭で、スキーマ確認後・通常ジョブ開始前に毎回呼び出すようにした。回収失敗は警告ログに留め、本流を停止しない。

## 安全対策

- DB上の時刻文字列をSQLの文字列比較に任せず、Pythonの `datetime.fromisoformat()` で解釈して比較する。`T` 区切りと空白区切りが混在しても、6時間ちょうどの境界を誤回収しない。
- UPDATE時に `task_name` / `run_date` / `status='running'` / `finished_at IS NULL` / 選択時の `started_at` を再確認し、選択後に再開・完了した行を巻き込まない。
- 不正な時刻、`started_at IS NULL`、完了済み、success/skipped/failure、直近runningは更新しない。
- `older_than_hours <= 0` を拒否し、危険な閾値設定による現行タスク回収を防止した。
- DBスキーマ、ROI、予測、`render.yaml`、cronスケジュール、既存APIは変更していない。ローカルscheduler・本番writerは起動していない。pushも行っていない。

## 検証結果

- 対象テスト: 57 passed。
- 全テスト: 701 passed（変更前697件 + 新規境界/冪等/早期呼び出し/非致命エラー4件）。
- Pythonコンパイル: 成功。
- `git diff --check`: 成功。
- ローカルSQLiteをread-only URIで確認し、`race_results` の自然キー重複は0件。2026-08-15 accident snapshotは1621行、同日ROI行は2件（開催中のため2件とも未settle）。データ更新は行っていない。

## 既存ゾンビの扱い

既存の `render_race_detail_all` と `render_signal_refresh_16_4` は手動更新しない。デプロイ後、regular schedulerの次回正常tickで6時間閾値を満たすため、自動的に `failure` / `stale_running_reaped` へ回収される。

## 作業中の失敗と対策

- 読み取り専用調査コマンド2件がPowerShellの入れ子引用符で失敗した。単一引用符の `rg` パターンとPowerShell here-stringへ切り替えて再実行した。
- `sqlite3` CLIは未導入だったため、リポジトリvirtualenvのPython SQLiteをread-only URIで使用した。
- pytest一時ディレクトリは削除対象を `docs/handoff.md` に明記してから削除した。

## 追補修正: `finished_at` セット済み running の回収

- 追補指示書 `stale_running_reaper_fix_plan_20260815.md` に従い、SELECTとUPDATE再チェックの両方から `finished_at IS NULL` 条件を削除した。
- 回収条件は `status='running'` かつ `started_at` が `now - older_than_hours` より厳密に古いこと。`started_at` の選択時値をUPDATEで再確認する競合防止と、6時間ちょうどを回収しないlive保護は維持した。
- `render_signal_refresh_16_4` 相当の、`finished_at` がセット済みでも古いrunning行を回収する専用回帰テストを追加した。
- 直近のrunning行は `finished_at` の有無にかかわらず触らず、既存の境界・冪等・閾値検証もgreenであることを確認した。

### 追補検証結果

- 対象テスト `tests/test_cron_runtime.py`: 8 passed。
- 全テスト: 702 passed（基準701件 + 追補回帰1件）。
- Pythonコンパイル: 成功。
- `git diff --check`: 成功。
- ローカルSQLiteをread-only URIで確認し、`race_results` の自然キー重複は0件。2026-08-15事故ランクスナップショットは1621行、同日ROI行は2件（2件とも未settle）。データ更新は行っていない。
- DBスキーマ、ROI、予測、`render.yaml`、cronスケジュール/呼び出し、既存APIは変更していない。ローカルscheduler・本番writer・serverは起動していない。pushも行っていない。
- 事前登録したpytest一時ディレクトリだけを検証後に削除した。

### 追補作業中の失敗と対策

- read-only監査用の複合PowerShellコマンドが、検索パターン内の引用符不整合で構文エラーになった。検索パターンを単純化し、検証コマンドを個別実行して全項目を再確認した。
- DB設定の検索で存在しない `src/config.py` を指定してexit 1になった。`data/boatrace.db` の実在を確認してから、repository virtualenvのSQLiteをread-only URIで直接照会した。
