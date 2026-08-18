# サイト公開前セキュリティ強化 作業ログ

- 実施日: 2026-08-19
- 対象指示書: `reports/security_hardening_spec_20260819.md`
- 開始時HEAD: `77b4beee3a1105918bd1ff6402e9dc0660185042`
- 制約遵守: push・deploy・本番Supabase接続/書込み・スキーマ変更・cron変更・ROI戦略変更なし

## 判定・変更点

### H1: 本番の開発用既定秘密

- 判定: 要修正。
- 変更: `RENDER` が有効な本番で `WEB_SESSION_SECRET == "dev-only-do-not-use-in-prod"` または `WEB_MEMBER_PASSWORD == "dev-member"` の場合、criticalログ後に `RuntimeError` で起動拒否する。
- テスト: 2つの既定値を個別にモックし、どちらも `create_app()` が失敗することを確認。

### H2: `/admin/cache-clear`

- 判定: 要修正。
- 変更: `@login_required` を `@admin_required` に変更。既存CSRF処理は変更なし。
- テスト: beta/paidが403、adminが200。

### H3: Render配下のclient IP

- 判定: 要修正。従来は `RENDER` 時にX-Forwarded-Forの先頭を手動採用しており、信頼するプロキシ段数が固定されていなかった。
- 変更: 本番のみ `ProxyFix(x_for=1)` を適用し、ロックアウトは補正後の `request.remote_addr` を使用。ロックアウトの回数・時間・保存方式は変更なし。
- テスト: 偽装値を含むXFFチェーンで直前1ホップだけを採用することを確認。

### M1: f-string SQL全数監査

- 判定: 指示書記載の65件は後続コミットにより増加。ASTで `src/` と `scripts/` の `execute()` 第一引数がf-stringである箇所を再列挙した実数は155件。
- 結果: 154件は内部生成プレースホルダ、固定SQL断片、数値化済み値、固定/検証済み識別子であり、Webリクエスト等の外部入力値の直補間なし。1件群 (`scripts/sync_to_supabase.py`) はCLI `--tables` の生識別子が到達可能だったため、英字/数字/アンダースコアだけを許可する検証と識別子クォートを追加した。値は従来どおりプレースホルダ。
- 回帰ガード: `tests/test_source_regression.py` が155件をAST列挙し、監査済み補間式以外または件数変化を拒否する。

#### 全155件の判定一覧

以下はすべて「安全」。括弧内は補間式で、値の外部入力直補間はない。

- `src/collectors/original_exhibition.py:248` → 安全 (`table_name`: 固定テーブル名)
- `src/collectors/result_scraper.py:83` → 安全 (`placeholders`: 個数生成)
- `src/collectors/result_scraper.py:278` → 安全 (`placeholders`: 個数生成)
- `src/collectors/tide.py:562` → 安全 (`placeholders`: 個数生成)
- `src/db/connection.py:511` → 安全 (`statement_timeout`: 数値化済み設定)
- `src/db/connection.py:699` → 安全 (`SQLITE_BUSY_TIMEOUT_MS`: 数値設定)
- `src/db/connection.py:720` → 安全 (`SQLITE_BUSY_TIMEOUT_MS`: 数値設定)
- `src/db/cron_runtime.py:274` → 安全 (`placeholders`: 個数生成)
- `src/db/task_log.py:32` → 安全 (`SQLITE_BUSY_TIMEOUT_MS`: 数値設定)
- `src/db/task_log.py:92` → 安全 (`_COLUMNS`: 固定列集合)
- `src/evaluation/accident_dent_strategy.py:187` → 安全 (`derived_cols/derived_join`: 固定スキーマ分岐)
- `src/evaluation/course_fit_strategy.py:250` → 安全 (`placeholders`: 個数生成)
- `src/evaluation/course_fit_strategy.py:275` → 安全 (`placeholders`: 個数生成)
- `src/features/asof_builder.py:154` → 安全 (`ddl`: 固定列定義から生成)
- `src/features/asof_builder.py:160` → 安全 (`name/kind`: 固定列定義)
- `src/features/asof_builder.py:166` → 安全 (`boat`: 固定1～6)
- `src/features/asof_builder.py:539` → 安全 (`placeholders`: 個数生成)
- `src/features/asof_builder.py:1525` → 安全 (`quoted`: DB列名を二重引用符エスケープ)
- `src/notifications/subscribers.py:89` → 安全 (`table_name`: 内部固定値)
- `src/roi_history.py:399` → 安全 (`placeholders`: 個数生成)
- `src/search/strategies.py:424` → 安全 (`schema_placeholders`: 個数生成)
- `src/start_prediction/features.py:103` → 安全 (`table_name`: 内部固定値)
- `src/start_prediction/features.py:145` → 安全 (固定スキーマ列/結合断片)
- `src/start_prediction/features.py:379` → 安全 (`cycle_select`: 固定分岐)
- `src/start_prediction/features.py:395` → 安全 (`cycle_clause`: 固定分岐)
- `src/start_prediction/features.py:436` → 安全 (`cycle_clause`: 固定分岐)
- `src/start_prediction/repository.py:230` → 安全 (`where`: コード内固定条件、値はparams)
- `src/web/app.py:355` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:1779` → 安全 (`_db_placeholders`: 個数生成)
- `src/web/app.py:3271` → 安全 (`placeholders/cycle_sql`: 個数生成・固定分岐)
- `src/web/app.py:3393` → 安全 (`cycle_sql`: 固定分岐)
- `src/web/app.py:4589` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:4605` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:4814` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:5015` → 安全 (`placeholders/cycle_filter_sql`: 個数生成・固定分岐)
- `src/web/app.py:5091` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:5113` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:5176` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:5235` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:5270` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:5310` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:5347` → 安全 (`cache_placeholders`: 個数生成)
- `src/web/app.py:6243` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:6290` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:6314` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:9541` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:9562` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:9631` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:9874` → 安全 (`racer_placeholders`: 個数生成)
- `src/web/app.py:9968` → 安全 (`racer_placeholders`: 個数生成)
- `src/web/app.py:10140` → 安全 (`racer_placeholders`: 個数生成)
- `src/web/app.py:10164` → 安全 (`racer_placeholders`: 個数生成)
- `src/web/app.py:10191` → 安全 (`motor_placeholders`: 個数生成)
- `src/web/app.py:10265` → 安全 (`racer_placeholders`: 個数生成)
- `src/web/app.py:10328` → 安全 (`racer_placeholders`: 個数生成)
- `src/web/app.py:10352` → 安全 (`motor_placeholders`: 個数生成)
- `src/web/app.py:10417` → 安全 (`racer_placeholders`: 個数生成)
- `src/web/app.py:10443` → 安全 (`motor_placeholders`: 個数生成)
- `src/web/app.py:10469` → 安全 (`racer_placeholders`: 個数生成)
- `src/web/app.py:10492` → 安全 (`race_placeholders`: 個数生成)
- `src/web/app.py:10571` → 安全 (`q1/q2`: 内部生成プレースホルダ)
- `src/web/app.py:14684` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:16509` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:16577` → 安全 (`_avg_st_expr/_derived_join`: 固定スキーマ分岐)
- `src/web/app.py:16751` → 安全 (`placeholders`: 個数生成)
- `src/web/app.py:19322` → 安全 (`racer_placeholders`: 個数生成)
- `src/web/app.py:19343` → 安全 (`motor_placeholders`: 個数生成)
- `src/web/app.py:20875` → 安全 (`_dst1_expr/_dstn1_expr/_derived_join`: 固定スキーマ分岐)
- `src/web/app.py:22105` → 安全 (`where_sql`: 固定条件、`limit`: int後50～1000に制限、検索値はparams)
- `src/web/membership.py:209` → 安全 (`placeholders`: 個数生成)
- `src/web/membership.py:218` → 安全 (`placeholders`: 個数生成)
- `scripts/_audit_features.py:91` → 安全 (`col`: コード内固定列リスト)
- `scripts/_check_2025.py:9` → 安全 (`m`: コード内固定月)
- `scripts/_check_2025.py:13` → 安全 (`y`: コード内固定年)
- `scripts/_check_db_dates.py:6` → 安全 (`y`: コード内固定年)
- `scripts/analyze_l4_tansho.py:132` → 安全 (`B`: 固定会場番号列)
- `scripts/analyze_l4_tansho.py:169` → 安全 (`B`: 固定会場番号列)
- `scripts/analyze_l4_tansho.py:242` → 安全 (`B`: 固定会場番号列)
- `scripts/apply_kachisuji_deltas.py:131` → 安全 (`table`: 固定TABLES)
- `scripts/apply_kachisuji_deltas.py:132` → 安全 (`alias/table`: 固定値)
- `scripts/apply_kachisuji_deltas.py:145` → 安全 (`alias`: 固定値、DBパスはプレースホルダ)
- `scripts/apply_kachisuji_deltas.py:149` → 安全 (`table`: 固定TABLES)
- `scripts/apply_kachisuji_deltas.py:154` → 安全 (`table/alias`: 固定値)
- `scripts/apply_kachisuji_deltas.py:163` → 安全 (`table`: 固定TABLES)
- `scripts/apply_kachisuji_deltas.py:174` → 安全 (`alias`: 固定値)
- `scripts/backtest_l4_course1_winrate.py:35` → 安全 (`EXCLUDE_B`: 固定数値集合)
- `scripts/backtest_l4_general_1c80.py:38` → 安全 (`EXCLUDE_B`: 固定数値集合)
- `scripts/backtest_l4_motor_rate.py:32` → 安全 (`EXCLUDE_B`: 固定数値集合)
- `scripts/backtest_l4_motor_weak_anti.py:38` → 安全 (`EXCLUDE_B`: 固定数値集合)
- `scripts/backtest_l4_racer_type.py:59` → 安全 (`EXCLUDE_B`: 固定数値集合)
- `scripts/backtest_l4_stadium_weather.py:60` → 安全 (`EXCLUDE_B`: 固定数値集合)
- `scripts/check_data_quality.py:232` → 安全 (`placeholders`: 個数生成)
- `scripts/check_data_quality.py:242` → 安全 (`placeholders`: 個数生成)
- `scripts/check_data_quality.py:254` → 安全 (`placeholders`: 個数生成)
- `scripts/check_data_quality.py:266` → 安全 (`placeholders`: 個数生成)
- `scripts/check_exhibition_thresholds.py:140` → 安全 (`where`: コード内固定シナリオ)
- `scripts/check_motor_top2_threshold.py:84` → 安全 (`where`: コード内固定シナリオ)
- `scripts/check_motor_top2_threshold.py:120` → 安全 (`where`: コード内固定シナリオ)
- `scripts/check_post_run_integrity.py:117` → 安全 (`placeholders`: 個数生成)
- `scripts/check_post_run_integrity.py:181` → 安全 (`_placeholders(chunk)`: 個数生成)
- `scripts/check_post_run_integrity.py:218` → 安全 (`_placeholders(chunk)`: 個数生成)
- `scripts/check_post_run_integrity.py:239` → 安全 (`_placeholders(target_races)`: 個数生成)
- `scripts/check_post_run_integrity.py:260` → 安全 (`_placeholders(chunk)`: 個数生成)
- `scripts/check_post_run_integrity.py:456` → 安全 (`_placeholders(target_races)`: 個数生成)
- `scripts/check_post_run_integrity.py:480` → 安全 (`placeholders`: 個数生成)
- `scripts/check_post_run_integrity.py:495` → 安全 (`_placeholders(closed_ids)`: 個数生成)
- `scripts/db_size_check.py:252` → 安全 (`cmd/table`: 固定VACUUM命令・固定削除テーブル)
- `scripts/explore_auto_loop_r15.py:569` → 安全 (`kim/PH`: 固定検証条件・個数生成)
- `scripts/export_kachisuji_slim_db.py:55` → 安全 (`quoted`: 固定TABLESを識別子クォート)
- `scripts/export_kachisuji_slim_db.py:58` → 安全 (`quoted`: 固定TABLESを識別子クォート)
- `scripts/export_kachisuji_slim_db.py:93` → 安全 (`quoted`: 固定TABLESを識別子クォート)
- `scripts/prewarm_race_detail_data.py:148` → 安全 (`placeholders`: 個数生成)
- `scripts/prewarm_race_detail_pages.py:102` → 安全 (`placeholders`: 個数生成)
- `scripts/prewarm_race_detail_tags.py:40` → 安全 (`placeholders`: 個数生成)
- `scripts/rebuild_racer_accident_stats.py:115` → 安全 (`table_name`: 呼出元固定値)
- `scripts/rebuild_racer_accident_stats.py:117` → 安全 (`table_name/ddl`: 呼出元固定列定義)
- `scripts/render_regular_scheduler.py:306` → 安全 (`placeholders`: 個数生成)
- `scripts/render_regular_scheduler.py:1151` → 安全 (`placeholders`: 個数生成)
- `scripts/render_regular_scheduler.py:1161` → 安全 (`placeholders`: 個数生成)
- `scripts/scrape_beforeinfo_live.py:191` → 安全 (`placeholders`: 個数生成)
- `scripts/self_heal_today_data.py:32` → 安全 (`SQLITE_BUSY_TIMEOUT_MS`: 数値設定)
- `scripts/smoke_search_all_fields.py:125` → 安全 (`boat`: 固定1～6)
- `scripts/sync_motor_preinspection_stats.py:93` → 安全 (`where`: コード内固定シナリオ)
- `scripts/sync_motor_preinspection_stats.py:125` → 安全 (`where`: コード内固定シナリオ)
- `scripts/sync_to_supabase.py:82` → 安全 (`quoted_table`: 厳格検証後の識別子)
- `scripts/sync_to_supabase.py:90` → 安全 (`col_list`: DB由来列を個別検証/クォート、`quoted_table`: 検証済み、`where`: 内部固定条件)
- `scripts/tilt_deep_verification.py:171` → 安全 (`boat/tilt_where`: 固定数値・固定条件)
- `scripts/tilt_deep_verification.py:185` → 安全 (`boat/tilt_where`: 固定数値・固定条件)
- `scripts/tilt_trifecta_backtest.py:79` → 安全 (`fixed_first/where_filter`: 固定数値・コード内固定条件)
- `scripts/tilt_trifecta_backtest.py:119` → 安全 (`fixed_first/where_filter`: 固定数値・コード内固定条件)
- `scripts/verify_2026_reproducibility.py:67` → 安全 (`YEAR_FILTER`: 固定条件)
- `scripts/verify_2026_reproducibility.py:78` → 安全 (`YEAR_FILTER`: 固定条件)
- `scripts/verify_2026_reproducibility.py:103` → 安全 (`YEAR_FILTER`: 固定条件)
- `scripts/verify_2026_reproducibility.py:123` → 安全 (`YEAR_FILTER`: 固定条件)
- `scripts/verify_2026_reproducibility.py:137` → 安全 (`YEAR_FILTER`: 固定条件)
- `scripts/verify_2026_reproducibility.py:147` → 安全 (`YEAR_FILTER`: 固定条件)
- `scripts/verify_2026_reproducibility.py:172` → 安全 (`YEAR_FILTER`: 固定条件)
- `scripts/verify_2026_reproducibility.py:190` → 安全 (`YEAR_FILTER`: 固定条件)
- `scripts/verify_2026_reproducibility.py:217` → 安全 (`YEAR_FILTER`: 固定条件)
- `scripts/verify_2026_reproducibility.py:239` → 安全 (`boat/YEAR_FILTER/tilt_where`: 固定数値・固定条件)
- `scripts/verify_bet_types.py:62` → 安全 (コード内固定買い目/年/オッズ条件)
- `scripts/verify_bet_types.py:90` → 安全 (`YEAR_FILTER/FAVORITE_BAND`: 固定条件)
- `scripts/verify_bet_types.py:106` → 安全 (`YEAR_FILTER/FAVORITE_BAND`: 固定条件)
- `scripts/verify_bet_types.py:127` → 安全 (`x`: 固定艇番、年/オッズ固定条件)
- `scripts/verify_bet_types.py:157` → 安全 (`x/combo`: 固定艇番/生成買い目)
- `scripts/verify_bet_types.py:186` → 安全 (`x`: 固定艇番、年/オッズ固定条件)
- `scripts/verify_bet_types.py:225` → 安全 (`a/b/c/combo_str`: 固定1～6から生成)
- `scripts/verify_bet_types.py:259` → 安全 (`a/b/c/combo`: 固定1～6から生成)
- `scripts/verify_f1_prime.py:199` → 安全 (`where_match`: コード内固定条件)
- `scripts/verify_pre_judgment_edges.py:56` → 安全 (`where`: コード内固定条件)
- `scripts/verify_pre_judgment_edges.py:96` → 安全 (`course`: 固定1～6)
- `scripts/verify_pre_judgment_edges.py:116` → 安全 (`course/lo/hi`: 固定数値ビン)
- `scripts/verify_pre_judgment_edges.py:179` → 安全 (`lo/hi`: 固定数値ビン)
- `scripts/verify_race_parts_impact.py:84` → 安全 (`placeholders`: 個数生成)
- `scripts/verify_race_parts_impact.py:96` → 安全 (`placeholders`: 個数生成)

### M2: ゲスト経路レート制限

- 判定: 要追加。
- 変更: 未ログインのリクエストを補正後IPごとにインメモリ60秒窓で集計。既定120件まで許可し121件目から429。`/healthz` と `/static/`、ログイン済み会員を除外。`BOATRACE_GUEST_RATE_LIMIT=0` で無効化。429に `Retry-After` と `Cache-Control: no-store` を付与。
- テスト: 120件成功→121件目429、healthz除外、無効化、会員除外を確認。

### M3: 500応答の内部情報

- 判定: 変更不要。`handle_500` は元例外を詳細ログへ残す一方、応答は固定テンプレート `_temporary_page_response(500)` の汎用文言のみ。
- テスト: pool名を含む強制例外で、ログには詳細が残り、HTTP本文には含まれないことを確認。

### LOW（記録のみ）

- CSP nonce化: 今回は未着手（大規模改修のbacklog）。
- DBバックド・共有ロックアウト: 今回は未着手（現行インメモリ方式を維持）。
- `/healthz` revision: 許容、変更なし。

## テスト結果

- scoped compile: 成功。
- focused: `41 passed`（初回はテストの設定名表記差で40 passed/1 failed、期待値だけ修正して再実行成功）。
- focused final bundle: `70 passed`。
- exact non-E2E: `1129 passed, 1 skipped`（基準1118 passedを維持）。
- scoped Python compile: 成功。
- scoped Ruff: 成功。
- `git diff --check`: 成功。
- M1作業ログ一覧: `→ 安全` 155件を機械集計してAST検出数155件と一致。
- scope audit: 実装はH1/H2/H3/M1/M2とテストfixture更新のみ。cron、ROI判定、既存の健全なセキュリティ制御、production dataは変更なし。

## 残課題

- LOW 3項目のみ。今回の受入範囲に未完了のHIGH/MEDIUM項目なし。

## コミット

- 実装コミットID: `b49ca49` (`Harden public web security controls`)
- 本行の確定追記は作業ログのみの後続ローカルコミット。push・deployなし。
