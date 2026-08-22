# レース詳細ページ生成コスト 調査・修正ログ

作業日: 2026-08-22
対象指示書: `reports/race_detail_render_cost_spec_20260822.md`

## 結論

支配要因は、テンプレートやモデル読込ではなく、再生成経路が既存の page prewarm 用一括取得を使わず、DB 接続の取得と SQL を小分けに繰り返していたことだった。本番計測で既に観測されていた最大 2,571 ms の pool wait と組み合わさると、Web worker 上だけ 12-16 秒へ増幅し得る構造だった。

再生成 (`?recompute=1` の許可済み内部・保守経路) を既存の `_prefetch_race_detail_page_inputs()` と単一接続共有コンテキストへ通した。通常の fresh/stale キャッシュ応答、ROI 判定、展示内容、cron、`render.yaml` は変更していない。

## 計測結果

### 事前確定値

- ローカルから本番 DB: 20 SQL / 2.27 秒。
- 本番 slow_request: 13-16 秒だが、従来は profiling が無効で `db_queries=0` / `db_time_ms=0.0`。
- DB pool: failure 0、peak concurrent 1、max hold 296 ms、max wait 2,571 ms。
- DB サーバー側: active 1、長時間 SQL なし。

### 今回の読み取り専用比較

対象は `20260822-02-12`。本番 Supabase 接続をセッション単位で read-only にし、ページキャッシュ書込み、slow_request 永続化、pool lifecycle 永続化を no-op にして実施した。データ・schema の書込みはしていない。

| 経路 | 全体 | SQL | DB時間 |
|---|---:|---:|---:|
| 旧経路 | 3,679.4 ms | 17 | 3,083.6 ms |
| 一括取得＋接続共有 | 270.5 ms | 9 | 187.6 ms |
| 修正後・別のcold採取 | 491.0 ms | 16 | 401.4 ms |

最初の比較では旧経路を先に実行したため、後段にはプロセス内 cache warm の効果も含まれる。また、安全のため各新規接続に read-only 設定を行っており、旧経路の絶対時間は本番そのものではない。一方、旧経路の 84% が DB 内で、接続を小分けに取得するほど待ちが増えること、および単一接続化後に待ちが消えることは再現できた。

修正後 cold 採取の工程内訳:

- prefetch: 178.4 ms
- display enrichment: 117.7 ms
- template: 67.1 ms
- result: 47.0 ms
- venue environment: 40.1 ms
- conditions: 39.3 ms
- page cache write: 0.0 ms（診断では意図的に無効化）
- total DB: 401.4 ms / 16 SQL
- total: 491.0 ms

テンプレートは 67.1 ms、DB 外の全時間も約 90 ms で、1 core/GIL やテンプレートは 12-16 秒の支配要因ではない。production/cached-only の詳細ページは永続済み prediction を読むだけで、リクエスト中のモデル artifact 読込はない。`test_client` は同一プロセス内の WSGI 再入で外部 HTTP は発生せず、今回 DB 時間が全体の大半を占めたため支配要因ではない。バックグラウンド経路には今後 `total_ms - request_ms` を `test_client_overhead_ms` として直接残す計測も追加した。

### タイムアウト棚卸し

- Web PostgreSQL `statement_timeout`: 8,000 ms
- `lock_timeout`: 3,000 ms
- `idle_in_transaction_session_timeout`: 15,000 ms（autocommit の通常 SELECT 待ちではない）
- pool checkout timeout: 5 秒
- Gunicorn timeout: 120 秒

成功リクエストが 13-16 秒に揃う単独の 15 秒 timeout は見つからなかった。最大 2.571 秒の pool wait を含む複数回の DB 接続取得・往復が積み上がる方が、実測とコードの両方に整合する。

## 変更点

- `src/web/app.py`
  - 許可済み race-detail recompute を既存の一括 prefetch＋単一接続共有へ統合。
  - live build に入ったリクエストだけ SQL profiling を自動有効化。通常のキャッシュ hit では無効のまま。
  - slow_request に `phases_ms` を追加し、prefetch、race info、venue、prediction、表示付加、選手名、conditions、result、template、cache write を記録。
  - template と cache write の時間を分離。
  - background rebuild ログに `total_ms`、内部 request 時間、`test_client_overhead_ms` を追加。
- `tests/test_race_detail_page_prewarm.py`
  - recompute が一度だけ prefetch し、同じ接続を route 内で再利用する回帰テストを追加。
  - 同期 cache miss の限定 profiling と、既存の HTML byte-identical / prewarm trigger 契約を更新。

## テスト・静的検査

- 対象: `36 passed`
  - `tests/test_race_detail_page_prewarm.py`
  - `tests/test_slow_request_recorder.py`
- 指示書どおりの全非 E2E suite: `1198 passed, 1 skipped`
  - 基準 1197 passed に今回の回帰テスト 1 件を追加。
- `py_compile`: pass
- Ruff `F821,F822,F823`（変更テスト）: pass
- `git diff --check`: pass
- `render.yaml`、展示 refresh、page prewarm script: 差分なし

## 失敗と再発防止

- 最初の広域 `rg` は、存在を確認していない `Procfile` を引数に含めたため exit 1。以後は `rg --files` または存在確認済みパスに限定した。
- Windows wildcard (`tests/test_member*`) を `rg` のファイル引数へ渡し exit 1。以後はディレクトリを検索し pattern で絞った。
- pytest の既定 temp root は ACL で読めず4件が setup error。リポジトリ直下の専用 `--basetemp` と `-p no:cacheprovider` で再実行し全通過。作成した3ディレクトリは記録後に削除確認済み。
- 最初の full-suite 呼出しは shell timeout を1秒にして終了コード124。残存プロセスがない状態で10分上限へ直し、全通過した。
- 最初の local commit は `.git/index.lock` の sandbox 権限で失敗。対象2ファイルを再確認し、許可された git 操作で同じ allowlist のみを commit した。

## コミット・運用

- 実装 commit: `e4aff22` (`Reduce race detail rebuild database churn`)
- push: 未実施
- deploy: 未実施
- 本番 Supabase write: 未実施
- ローカル scheduler / writer: 未起動

本番 12-16 秒の最終値は deploy 禁止のためこの作業内では再計測できない。次回の承認済み deploy 後は、slow_request の `db_queries`、`db_time_ms`、`phases_ms` と background log の `test_client_overhead_ms` だけで残差を切り分けられる。
