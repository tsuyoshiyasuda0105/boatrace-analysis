# 作業指示書: step27 夜間デルタ取込の SQL 方言バグ修理 (Codex CLI 用)

作成: 2026-08-20 / 発注: リッキー / 診断・検品: リン (Claude)
テスト: .venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e
(現状 1159 passed, 1 skipped。割らないこと)

## 症状と原因 (リン診断済み)

step27 (コミット 2a3bd99) の日次 kachisuji デルタ取込が、昨夜 01:00 の**初回本番実行で失敗**
(BoatracePcNightlyPrepare exit=1)。ログ末尾:
```
syntax error at or near "OR"
LINE 1: INSERT OR REPLACE INTO "stadiums" ("stadium_number", "name",...
```
**SQLite 専用構文 `INSERT OR REPLACE` を PostgreSQL に発行**している。CLAUDE.md の既知
バグパターン (INSERT OR REPLACE は COALESCE upsert で置換すべき) の再発。
結果、バックテスト検索データ (kachisuji slim / asof_race_features) が 8/18 で停止し、
8/19 ぶんが未取込。プリフライト #7 が正しく fail を検知した。

## やること

### [必須1] 方言バグ修理
- 対象: scripts/apply_kachisuji_deltas.py / scripts/upload_kachisuji_delta.py /
  scripts/pc_nightly_prepare.py の該当経路。`INSERT OR REPLACE` を Postgres では
  `INSERT ... ON CONFLICT (...) DO UPDATE SET col = EXCLUDED.col` へ。
  SQLite に対しては従来構文でよい (接続先方言で分岐 or 共通の upsert ヘルパー)。
- **NULL 上書き禁止**: CLAUDE.md の教訓どおり、上書きは COALESCE(EXCLUDED.col, 既存)
  が必要な列がないか対象テーブルごとに確認し、必要なら適用。
- 同スクリプト内に他の SQLite 専用構文 (INSERT OR IGNORE / PRAGMA 等) が Postgres
  経路に混ざっていないか全数確認。

### [必須2] プリフライト #7 の参照先修正
scripts/render_maintenance_scheduler.py の backtest_yesterday_import チェックが
`latest_race_date: null` を返す (存在しないテーブル/列を参照)。slim DB の実体は
`asof_race_features.race_date`。正しい参照に直し、実測値が入るようにする。

### [必須3] 回帰テスト
- upsert ヘルパー/分岐の単体テスト (Postgres 構文に OR REPLACE が含まれないこと)
- tests/test_source_regression.py に「apply/upload スクリプトの Postgres 経路に
  INSERT OR REPLACE が無い」静的チェック

### [必須4] 8/19 ぶんの埋め戻し手順
修理後に 8/19 (必要なら 8/20 も) を取り込むコマンド手順を作業ログに記載。
実行はリン。**Codex は本番 DB に書き込まない**。

## 絶対ルール
- push 禁止・デプロイ禁止・本番 Supabase 書込み禁止
- 採用ROI戦略の判定結果を変えない / render.yaml 不変
- 直近の cf07735 / 58955c9 / 7223440 / b49ca49 を壊さない
- 作業ログ: reports/nightly_delta_dialect_fix_work_log_20260820.md
  (変更点 / テスト結果 / 埋め戻し手順 / コミットID)
