# TOPスナップショット キャッシュ一括読取 作業ログ

作業日: 2026-08-23
対象指示書: `reports/top_snapshot_cache_batch_spec_20260823.md`

## 結論

TOPスナップショット生成で、市場シグナル2キーと当日168レースの詳細タグキーを先にまとめ、`page_html_cache` を `WHERE cache_key IN (...)` で一括取得するようにした。変更前に169回あった単件キャッシュSELECTは0回になり、一括SELECTは1回になった。

本番Supabaseを読み取り専用で実測した結果、`_build_top_page_snapshot_payload("2026-08-23")` は17.998秒・177 SQLから2.838秒・9 SQLへ短縮した。SQL回数は94.9%減、所要時間は84.2%減（約6.3倍高速）だった。

## 変更前後の実測

対象日は2026-08-23、対象レースは168件。各DB接続で `default_transaction_read_only=on` を設定し、計測ラッパーは `SELECT` / `WITH` / `SHOW` / `EXPLAIN` 以外を拒否した。安全設定用の `SET` はアプリSQL回数から除外している。キャッシュ書込み、スナップショット書込み、scheduler、writerは実行していない。

| 計測 | 所要時間 | 全SQL | SQL種類 | 単件cache SELECT | 一括cache SELECT |
|---|---:|---:|---:|---:|---:|
| 変更前 | 17.998秒 | 177 | 9 | 169 | 0 |
| 変更後 | 2.838秒 | 9 | 9 | 0 | 1 |
| 差 | -15.160秒（-84.2%） | -168（-94.9%） | 0 | -169 | +1 |

指示書の事前診断は179 SQL・11種類だったが、今回の開始HEAD `d2e4534` に対する同一ハーネス実測は177 SQL・9種類だった。支配要因は事前診断どおり169回の `SELECT html, updated_at FROM page_html_cache WHERE cache_key = ?` であり、変更後は完全に消えている。

## 変更点

- `src/web/app.py`
  - TOP生成前に必要な市場シグナル2キーとレース詳細タグキーを列挙し、一括でプロセスキャッシュへ読み込むようにした。
  - 永続キャッシュの複数キー読取を共通化し、900キーごとにチャンク分割する `_read_page_html_cache_rows` を追加した。
  - fresh/stale双方に使える `_read_page_html_caches` を追加した。各キー固有の `updated_at` でTTL判定し、期限切れメモリ値は永続行を再確認するなど、既存単件読取と同じ判定順を維持した。
  - 市場シグナル2キーと詳細タグを、ループ内の単件読取ではなく一括取得済み辞書から参照するようにした。
  - 既存 `_read_json_caches_stale` も同じ900件チャンクの永続行読取を使用するようにした。
- `tests/test_top_snapshot_cache_batch.py`
  - TOP経路に単件読取ループが残らない静的回帰を追加した。
  - TTLあり（fresh）・TTLなし（stale）で、戻り値と `_PAGE_HTML_MEM_CACHE` の最終状態が既存単件読取と一致する比較テストを追加した。
  - 901キーが900件と1件の2クエリへ分割されるテストを追加した。
- `tests/test_today_races_page.py`
  - バッジ系既存テストのmock seamを単件読取から一括読取へ追随させた。期待するシグナル・バッジ内容は変更していない。

## 共通処理・禁止経路への影響確認

- `_read_page_html_cache` と `_read_page_html_cache_stale` のシグネチャ・実装は変更していない。既存呼び出し元は追随不要。
- レース詳細の通常表示経路、fresh/stale判定、背景再生成の同時1本上限、`RACE_DETAIL_PAGE_FRESH_SEC=86400` は変更していない。
- `scripts/prewarm_race_detail_pages.py`、`render.yaml`、採用ROI戦略、展示データ反映、cron設定に差分はない。
- TOPの市場シグナル・race_badges・accident_watchの選別と正規化は維持し、展示内容を減らしていない。
- `d2e4534`、`a8b6e65`、`374d731`、`d26e587` の対象経路を含む全非E2E suiteが通過した。

## テスト・静的検査

- 新規＋TOP焦点: `29 passed`
- TOP・race-detail・stale・degraded・nightly・source関連: `123 passed`
- 指定の全非E2E suite: `1208 passed, 1 skipped`
  - 基準1204件に新規4件を追加し、既存件数を割っていない。
- `py_compile`（変更Python 3ファイル）: pass
- Ruff `F821/F822/F823`（変更テスト2ファイル）: pass
- `git diff --check` / staged allowlist: pass
- whole-file Ruffは、今回未変更の `src/web/app.py:14873-14893` に既存の `b1` / `b3` / `b4` 未定義7件を検出した。今回の差分外で、ROI判定変更禁止のため対象外とした。全pytestとコンパイルは通過している。

## コミット・運用

- 実装コミット: `2a3bd72` (`Batch TOP snapshot page cache reads`)
- push: 未実施
- deploy: 未実施
- 本番Supabase write: 未実施
- ローカルscheduler / writer: 未起動

## 計測・検証中の失敗と再発防止

- 最初の本番計測はsandboxの外向きTCP制限でDB接続前に失敗した。同一の読み取り専用プローブを限定承認で再実行した。
- 変更前プローブは結果取得後に共有DBプールを明示closeせず、プロセス終了時だけ `PythonFinalizationError` を出した。変更後プローブは `finally` でプールをclose・clearし、警告なく終了した。
- 初回焦点テストは、旧単件readerをmockしていた既存テスト1件のみ失敗した。一括readerをmockするようテストを追随し、再実行で29件すべて通過した。
