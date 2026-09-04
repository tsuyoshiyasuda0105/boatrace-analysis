# 作業指示書: P1-4 cron の統一・整理 (本番稼働中・要注意)

作成: 2026-08-14 / 発注者: リッキー / 単体で完結する指示書。
リポジトリ: `C:\boat_project\boatrace-analysis` (正本。他の場所に checkout を作らない)
背景: 監査 [reports/codebase_audit_20260813.md] の P1-4。cron の信頼性を上げ、
「Fix cron / Recover」の火消しループを断つ。**対象は本番で5分毎に動く稼働中のcron**
なので、挙動を変えずに構造だけ直すことを最優先する。

## ⚠️ 最重要の前提: これは本番稼働中のcron

ロックや task_runs の書き込み挙動を**変えてしまうと、cronの多重実行・取りこぼし・
本番停止**につながる。この指示書は原則**「振る舞いを変えずコードを一本化する」
リファクタリング**であり、動作仕様の変更は最小限・明示的にのみ行う。

## 絶対に守るルール

1. **origin/main への push 禁止** (push=本番デプロイ)。コミットはローカル main まで。
2. **render.yaml のスケジュール・env は変更禁止**。cron の実行時刻/頻度は不変。
3. ROI 戦略ロジック・予測ロジック・DB スキーマの変更禁止。
4. **ロックの「どのジョブが同時に走れるか」という実効セマンティクスを変えない。**
   コードを共通化しても、各ジョブのロック名・スコープ・タイムアウトは現状維持。
5. 作業ログを `reports/p1_4_work_log_20260814.md` に記録 (変更・テスト・保留も)。
6. テスト: `.venv/Scripts/python.exe -m pytest tests/ -q`。**既存の失敗7件から
   増やさない** (末尾のベースライン参照)。各フェーズで回帰テストを追加。
7. **触ってよいファイル (これ以外は編集禁止)**:
   - `scripts/render_regular_scheduler.py`
   - `scripts/render_maintenance_scheduler.py`
   - `scripts/render_program_bootstrap_scheduler.py`
   - `scripts/refresh_race_detail_after_exhibition.py`
   - `scripts/odds_scheduler_render.py`
   - `src/db/cron_run_log.py`, `src/db/task_log.py`
   - 新規 `src/db/cron_runtime.py` (共通ヘルパー置き場)
   - `tests/` 配下の新規/関連テスト
   - `reports/p1_4_work_log_20260814.md`
   `src/web/app.py` `src/collectors/*` `src/parsers/*` には触れないこと。

## 現状の事実 (2026-08-14 に行番号検証済み)

### 既に解消済み (P0-2 で対応。今回**触らない**)
- maintenance/bootstrap の `main()` は最終失敗で非0を返し、`cron_alerts.notify_cron_failure`
  でメール通知する (`render_maintenance_scheduler.py:382-417`,
  `render_program_bootstrap_scheduler.py:586-619`, `src/notifications/cron_alerts.py`)。
- `refresh_race_detail_after_exhibition._record_cron_skip` (:129-160) は
  `status='skipped'` を正直に記録し running 行を上書きしない。
  → **これらは完了済み。再実装しないこと。**

### 残る問題 (今回の対象)

**問題A: ロック機構が5方式併存 (相互に無関係)**
| ジョブ | 方式 | 場所 |
|---|---|---|
| regular | task_runs 行のリース (条件付きUPSERT, 30分stale) | `render_regular_scheduler.py:_regular_run_lock` 53-115 |
| maintenance | `pg_try_advisory_lock(hashtext(...))` | `render_maintenance_scheduler.py:maintenance_lock` 58-73 |
| program-bootstrap | `pg_try_advisory_lock` | `render_program_bootstrap_scheduler.py:_run_lock` 102-117 |
| odds | `pg_try_advisory_lock` (P0-3で追加) | `odds_scheduler_render.py:odds_lock` 45-68 |
| exhibition | task_runs status='running' + 15分 | `refresh_race_detail_after_exhibition.py:_exhibition_refresh_recently_running` 193-219 |

**問題B: task_runs の書き手が7つ、`started_at` の扱いが不整合**
- 上書き型: `render_regular_scheduler.record_task` (375-399), `_regular_run_lock` (65-89),
  `refresh_race_detail_after_exhibition._record_task` (102-127),
  `render_program_bootstrap_scheduler._write_task` (144-181)
- 保持型: `refresh...._record_cron_skip` (129-160), `src/db/cron_run_log.record_cron_run`
  (14-73), `src/db/task_log.record` (55-91, ローカルSQLite)
- テーブルDDLも2箇所 (`render_regular_scheduler.ensure_task_runs_table` 347-372 /
  `refresh...._ensure_task_runs_table` 72-99)。

**問題C: render_regular_scheduler.py に本番到達不能コード ~200-250行**
- 主因は render.yaml:120-121 の `BOATRACE_RENDER_DAYTIME_LITE="1"` (→ lite_mode=True) と
  `BOATRACE_DEDICATED_PROGRAM_BOOTSTRAP` 既定 "1"。
- 到達不能: `run_morning` (572-593), `run_morning_catchup_if_needed` (866-887),
  `run_tide_self_heal` (785-798), `run_hourly` (715-726),
  `run_accident_self_heal` (1153-1201), `run_nightly` (1204-1253),
  `run_roi_history_slot` (927-943), および main 内の該当分岐。
  ※ `run_roi_daily_self_heal` (750-775) は maintenance 側 (run_integrity_phase:234) から
  **生きている**ので削除禁止。`run_accident_full_refresh` も呼び出し元を要確認。

**問題D: 重複ヘルパー (regular ⇄ exhibition)**
- `_parse_race_close_jst` (regular 156-169 / exhibition 286-299): ほぼ同一。
- **original-exhibition 探索** (`find_missing_original_exhibition_races` regular 172-221 /
  `_find_missing_original_exhibition_races` exhibition 302-364): **実装が乖離**
  (exhibition 版は lap/turn/straight 完全性チェック + `>=6` 要件、regular 版は `>0`)。
- `ensure_task_runs_table`/`record_task` も名前だけ違う重複。

## タスク (安全な順に段階実行。各フェーズ後にフルテスト)

### フェーズ1 (最安全): 共通ヘルパー化 (振る舞い不変)
新規 `src/db/cron_runtime.py` を作り、以下を**唯一の実装**として集約:
1. `record_task_run(conn, task_name, run_date, status, *, detail=None, increment=False)`
   — task_runs への UPSERT を1関数に統一。**`started_at` は初回のみ設定し、以降の
   terminal 遷移では保持する** (保持型に統一。これが「正しい」挙動)。
   `success_at` は success 時のみ設定。running/skipped/success/failure を扱う。
2. `ensure_task_runs_table(conn)` — DDL を1箇所に (RLS 有効化含め現状踏襲)。
3. `parse_race_close_jst(closed_at, race_date)` — 締切パースを1箇所に。
4. `advisory_lock(conn, name)` (contextmanager) — `pg_try_advisory_lock` 方式を1箇所に。
   SQLite では常に取得扱い (既存 odds_lock と同じ形)。
5. 各スケジューラの重複定義を上記 import へ置換。**ロック名・タイムアウト等の
   パラメータは各ジョブの現状値をそのまま渡す** (実効セマンティクス不変)。
   - regular のリース方式はリスクが高いので、**フェーズ1では方式を変えず**
     `record_task_run` の共通化のみ適用可。リース→advisory への移行は
     フェーズ4で別途判断 (下記)。
6. 回帰テスト: `record_task_run` が (a) 初回 started_at 設定, (b) success で
   started_at 保持 + success_at 設定, (c) skipped で running を壊さない,
   (d) advisory_lock が取得/解放される、を検証。

### フェーズ2: 乖離した original-exhibition 探索の一本化
`find_missing_original_exhibition_races` を1実装に統合。**exhibition 版 (厳しい方=
lap/turn/straight 完全性 + >=6) を正**とし、regular 側をそれに合わせる。
理由と差分を作業ログに明記。回帰テスト (完全性不足の行が「未取得」判定される) を追加。

### フェーズ3 (要検証): 到達不能コードの削除
1. 削除前に**到達不能であることを機械的に証明**する:
   - `BOATRACE_RENDER_DAYTIME_LITE="1"` と `BOATRACE_DEDICATED_PROGRAM_BOOTSTRAP="1"`
     を前提に、各 dead 関数の呼び出しゲートが False になることをテストで示す
     (env を設定して `main` の分岐を辿る、または該当ゲート条件を単体評価)。
2. 証明できたものだけ削除。**`run_roi_daily_self_heal` は削除禁止** (maintenance から生存)。
   `run_accident_full_refresh` は呼び出し元を grep し、maintenance 経由で生きていないか
   確認してから判断 (不明なら残す)。
3. 削除した関数一覧・証明方法を作業ログに記録。**1関数でも確信が持てなければ残す**。

### フェーズ4 (最要注意・任意): ロック機構の統一
- 5方式を advisory_lock 1方式に寄せる**提案**を作業ログに書く。ただし
  **regular のリース方式を advisory に変える実装は、この指示書では行わない**
  (本番の多重実行検知を壊すリスク。別レビュー・別デプロイで慎重に扱う)。
- exhibition の running チェックも同様に現状維持。
- つまりフェーズ4は「設計メモの作成のみ」。実コード変更は保留。

## 受け入れ条件

- [ ] `src/db/cron_runtime.py` に task_runs 書き込み/DDL/締切パース/advisory_lock が集約され、
      各スケジューラがそこを import している (重複実装が消えている)
- [ ] task_runs の `started_at` 保持セマンティクスが全経路で一貫
- [ ] original-exhibition 探索が1実装 (厳しい方) に統一
- [ ] 削除した dead code は到達不能をテストで証明済み。生きているものは残っている
- [ ] ロック統一はフェーズ4の設計メモのみ (実コードのロック方式は不変)
- [ ] pytest: 既存7失敗から増えない。新規テスト全 green
- [ ] push していない。作業ログに変更一覧・テスト・保留・「デプロイ待ち」明記

## ベースライン (テスト増減判定)
現行 main の失敗は **7件** (JS/バッジ系の失活テスト + run_nightly)。変更後に
failed が 7 を超えたら、超過分が本タスク起因か `git stash` で切り分けること。
既知の7件:
- test_l4_recent_odds_source::test_l4_mid_is_not_listed_in_high_roi_candidates
- test_source_regression::test_morning_watch_badge_is_prominent_in_today_picks
- test_source_regression::test_reference_market_signals_are_not_today_roi_candidates
- test_source_regression::test_today_high_roi_hides_female_mixed_and_general_references
- test_today_races_page::test_race_grid_badges_payload_hydrates_cache_only_payload
- test_today_races_page::test_write_top_page_snapshot_preserves_existing_daily_badges
- test_web_recompute_guard::test_nightly_prewarms_tomorrow_market_signals

## やらないこと (スコープ外)
- render.yaml のスケジュール/env 変更
- regular のリース→advisory ロック移行 (フェーズ4は設計メモのみ)
- app.py / collectors / parsers への変更
- 回復ロジック (SCHEDULER_VERSION 等) の再設計 — 今回は触らない
