# 作業指示書: 朝の障害3点セットの修正 (Codex CLI 用)

作成: 2026-08-15 / 発注者: リッキー / 検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本のみ。他の場所に checkout を作らない)
背景: 2026-08-15 朝の実障害。夜間監視ログ `reports/night_watch_20260814.jsonl` と
`reports/todo_20260814.md` を参照。行番号は必ず現行 main で自分で再確認すること。

## 絶対ルール (毎回同じ)

1. **origin/main への push 禁止** (push=本番デプロイ。承認ゲートは発注者)。コミットはローカル main まで。
2. ROI 戦略ロジック・予測ロジック・DB スキーマ・render.yaml のスケジュール変更禁止。
3. P1-4 で導入済みの共通ヘルパー `src/db/cron_runtime.py` (record_task_run /
   advisory_lock / ensure_task_runs_table) を使うこと。task_runs への独自 UPSERT を新設しない。
4. テスト: `.venv/Scripts/python.exe -m pytest tests/ -q` — 現行 main は **全件 green
   (649 passed / 直近コミットで +α)**。1件でも壊したら自分の変更を疑うこと。
5. 作業ログ `reports/morning_fixes_work_log_20260815.md` に変更・テスト結果・保留を記録。
6. 各タスクは独立コミット (計3コミット目安)。

## タスク1 (最優先): モーター交換期でメンテ detail フェーズが打ち切られる問題

**実害**: 8/14・8/15 と2日連続で `render_maintenance_detail_v1` が failure
(06:31 打ち切り) → 本日のレース詳細キャッシュ 0 件のまま朝を迎え、会員がページを
開けなくなった。

**既知の事実**:
- system_status に `post_run_detail_rows_targeted` warning「motor rate all-zero 12 races」
  と `post_run_motor_cache_targeted` warning「motor history not established 18 items」。
  蒲郡などモーター交換直後の会場では新モーターの成績が全ゼロ/履歴未確立なのは**正常**。
- maintenance の detail フェーズは `scripts/render_maintenance_scheduler.py` の
  `run_detail_phase` 系 → `MAX_PHASE_ATTEMPTS=3` の circuit breaker で打ち切り。
- 過去の部分対応: コミット `1d6b629` (Classify new motor histories as warnings),
  `13d8b7c` (Recover missing published motor rates)。それでも失敗が続いている。

**やること**:
1. 8/15 06:31 の failure の正確な失敗経路を特定する (task_runs の detail_json、
   `scripts/check_post_run_integrity.py`、detail フェーズが呼ぶスクリプトを追う)。
2. 「モーター交換期として説明可能な欠損 (全ゼロ率・履歴未確立)」を**フェーズ失敗の
   条件から外す** (warning 記録は残す)。判定に使ってよいのは「新期モーター(履歴の
   浅さ)や当該会場の交換時期」などレース前に分かる事実のみ。
3. どんな場合でも **detail フェーズの本業 (tags/pages の prewarm) は最後まで走り切り**、
   検査の warning でキャッシュ生成自体が止まらない構造にする。
4. 回帰テスト: モーター全ゼロ会場が混ざっていても detail フェーズが success になり、
   prewarm が全レース分呼ばれることを fake で検証。

## タスク2: 朝のキャッシュ自動温め直し (最後の砦)

**目的**: メンテが何らかの理由で失敗しても、朝一番に「本日の詳細キャッシュ 0 件」の
まま会員を迎えない。

**やること**:
1. `scripts/render_regular_scheduler.py` の **lite 経路 (`run_lite_daytime_bootstrap`)**
   に「本日の `race_detail_page:<現行version>:<today>%` キャッシュ被覆率チェック」を追加。
   - 被覆 < 50% (レース数比) なら `scripts/prewarm_race_detail_tags.py --date today` →
     `scripts/prewarm_race_detail_pages.py --date today` を実行 (timeout は既存 run_py の
     流儀で bounded)。
   - `cron_runtime.record_task_run` + 既存の task_success_exists 相当で **1日1回まで**
     (task 名例: `render_detail_pages_selfheal`)。成功時のみ success 記録。
2. バージョン文字列 (`race_detail_page:v15:`) を**ハードコードしない**。
   `src/web/app.py` の `_race_detail_page_cache_key` / `RACE_DETAIL_PAGE_CACHE_VERSION`
   を import して組み立てる (app.py の import が重い場合は、`page_html_cache` から
   `race_detail_page:%:<today>%` の LIKE で件数を取る等、バージョン非依存の実装でも可)。
3. 回帰テスト: 被覆不足→prewarm 起動 / 被覆十分→起動しない / 同日2回目は起動しない。

## タスク3: 前日結果の自動再取込

**実害**: 8/13 は 7件、8/14 は 43件の結果が「日付が変わった瞬間に凍結」し、毎朝
手動修復している。ROI 台帳・成績表示に穴が出る。

**やること**:
1. 毎朝1回、**前日分**の `scripts/poll_results.py --date <yesterday> --no-jitter` を
   自動実行する。実装場所は `scripts/render_regular_scheduler.py` の lite 経路
   (8時台の最初の成功 tick で1回) を推奨。`cron_runtime.record_task_run` で
   1日1回ガード (task 名例: `render_results_backfill_yesterday`、run_date=yesterday)。
2. poll_results は既に --date 対応・冪等 (COALESCE系 upsert + ROI 即時清算) なので
   呼ぶだけでよい。**poll_results 自体の改造はしない**。
3. 回帰テスト: 8時台 tick で 1 回だけ起動・同日2回目なし・失敗時は翌 tick でリトライ
   (success 記録がなければ再試行される、の確認)。

## 受け入れ条件

- [ ] タスク1: モーター交換期の warning では detail フェーズが落ちない (テストあり)
- [ ] タスク2: キャッシュ被覆不足の朝に自動温め直しが1回走る (テストあり)
- [ ] タスク3: 前日結果の自動再取込が毎朝1回走る (テストあり)
- [ ] pytest 全件 green 維持 / push していない / 作業ログ提出

## 検品 (リンが実施)

完了報告後、リンが diff・テスト・受け入れ条件を照合します。指示書との乖離が
あれば差し戻します。デプロイは発注者承認後。
