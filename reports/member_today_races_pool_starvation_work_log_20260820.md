# `/member/today-races` DB接続枯渇 修理作業ログ

作業日: 2026-08-20
対象: `reports/member_today_races_pool_starvation_spec_20260820.md`

## 結論

Web共有プールの接続取得失敗を `ERROR` ログにしたことで、同期式の `EmailErrorHandler` が `record_incident()` を呼び、枯渇中の同じプールへ再度接続を取りに行っていた。そこで再び `ERROR` が発生し、SQLを1本も実行しないまま接続待ちが再帰的に累積していた。これは本番観測の `db_queries=0`、約59～64秒、`PoolTimeout` / `TooManyRequests` と整合する。

修理後は1リクエスト全体の共有プール取得待ちを最大10秒に制限し、`/member/today-races` は既存の degraded 作法による混雑中レスポンス（HTTP 200、`Retry-After: 30`、`no-store`）へ退避する。取得失敗ログは同期DB書込みを起動しない `WARNING` とし、非機密のプール計測値は次に取得できた接続で `system_status` へ保存する。

## 事前観測と計測

- 指示書記載の本番実測は `/member/today-races` が約59～64秒後に500、`db_queries=0` / `db_time_ms=0.0`。Postgres側は active=1 / idle=8、5秒超クエリなし。
- 変更前の取得経路は1回5秒の `getconn()` に最大2回の再試行があり、リクエスト全体の総時間上限がなかった。
- 初回の統合回帰テストでは、仮想的な取得予算を10秒にしても実時間が21.77秒まで延びた。ログとスタックから、接続失敗の `ERROR` → `EmailErrorHandler` → `record_incident()` → 同じプール取得失敗、という再帰を確認した。
- 新設した `system_status.check_name=db_pool_lifecycle` は、次の項目を日次集計する。
  - 取得回数、返却回数、取得失敗回数
  - 最大取得待ち時間 `max_wait_ms`
  - 最大保持時間 `max_hold_ms`
  - 同時取得ピーク `peak_concurrent` と直近値 `current_acquired`
  - 直近10件の取得・返却・失敗イベント、および非機密のプール統計
- 計測は最大100件のメモリバッファ、最大1分に1回で、すでに取得できた接続を利用する。計測のために新たな接続取得は行わず、保存失敗もユーザ応答を壊さない。
- 単体計測では、2接続同時取得時に `peak_concurrent=2`、2.5秒保持時に `hold_ms=2500.0`、取得125ms時に `max_wait_ms=125.0` が記録された。枯渇時は合計10,000msで打ち切られた。
- デプロイ禁止のため、新しい本番テレメトリの実値は未取得。デプロイ後はこの行の `max_wait_ms`、`max_hold_ms`、`peak_concurrent` を観測して容量判断する。

## 接続保持経路の全数調査

- `/member/today-races`: 通常表示用と履歴用の接続はいずれも `with` 範囲内で閉じ、キャッシュ処理・テンプレート描画より前に返却している。
- HTTP呼出し・認証・テンプレート: リクエスト中の外部HTTP処理はDB接続スコープ外。テンプレート描画もページ本体の接続返却後。グローバルコンテキストのDBアクセスも独立した短いスコープである。
- 起動初期化: `_ensure_db_initialized()` が `schema.sql` と `stadiums.json` を接続保持中に読んでいた。両ファイルの読込みを接続取得前へ移動した。
- kachisujiデルタ自動適用: `fetch_pending_payloads()` はPostgres接続を閉じてから一時ファイル・ローカルslim DB処理へ進むため、共有接続の長期保持はなかった。
- prewarm / signal: 重いsignal再計算は既存の `BOATRACE_TASK_TRIGGER` ゲート配下で、トリガープロセスはWeb共有プールを使わず直接接続する。通常Webリクエストからの重い再計算は既存仕様で禁止されている。
- 接続リーク: Web内の裸の接続取得4件は全件 `finally: close()` があり、その他の対象経路は `with` 管理だった。今回の調査範囲では未返却リークを確認しなかった。

## 原因

1. 共有プールが同時実行下で一時的に満杯になる。
2. 5秒の取得待ちと再試行に、リクエスト全体の上限がなかった。
3. 最終取得失敗を `ERROR` で出すと、同期エラーハンドラが同じDBへ障害記録を試みる。
4. その障害記録も接続取得前に失敗して再び `ERROR` を出し、待ちを再帰的に増幅する。
5. そのためSQL実行数0のまま約60秒後に500となり、日付変更やBacktest LABへの遷移が反応しないように見えていた。

## 変更点

- `src/db/connection.py`
  - Webリクエスト単位の共有プール取得予算を追加。環境変数 `BOATRACE_WEB_DB_CHECKOUT_BUDGET_SEC` で短縮可能だが、コード上の上限は10秒。
  - 個々の `getconn()` と再試行sleepを残予算以内に制限。
  - 取得待ち・保持時間・同時取得数・取得失敗を非機密イベントとして計測。
  - 枯渇時ログを `WARNING` にし、同期 `ERROR` ハンドラによる再帰的DB取得を遮断。
- `src/web/app.py`
  - `before_request` / `teardown_request` で取得予算を開始・解除。
  - 計測を既存の `system_status` へベストエフォート保存。
  - `/member/today-races` の一時的DB障害を既存の degraded 応答へ変換。
  - 起動時のスキーマ・競艇場マスタのファイルI/Oを接続取得前へ移動。
- テスト
  - 総取得待ち10秒上限、保持時間・同時取得計測、`system_status` 保存、混雑中応答、起動時ファイルI/O順序、ERRORハンドラ非起動を追加・更新。

## `max_size=4` の妥当性

デフォルトは4のまま変更しなかった。cron・maintenanceは既存の `BOATRACE_TASK_TRIGGER` を設定し、Web共有プールではなく直接接続を1本ずつ使う設計だが、Supavisorのクライアント枠自体はWebとcronで共有される。本番のcron同時消費数をこの作業では書込みなしに継続計測できず、ここでWebを増やすとcron側の余裕を削る可能性がある。

まず `db_pool_lifecycle` の `peak_concurrent`、`max_wait_ms`、`max_hold_ms`、`checkout_failures` と、同時間帯のcron実行数を観測する。保持時間が短いのに `peak_concurrent=4` と待ちが継続し、Supavisor全体の余裕も確認できた場合だけ、既存環境変数による段階的な増加を提案する。今回の確定原因は再帰的待ち増幅と総時間上限欠如であり、デフォルト増加は最小修理ではない。

## テスト結果

- 関連回帰: `166 passed`。
- 最終全回帰: `.venv/Scripts/python.exe -m pytest --ignore=tests/e2e --ignore=tests/round3_e2e -q --basetemp=.pytest_tmp_pool_starvation_full`
  - `1180 passed, 1 skipped, 1 warning in 25.24s`
  - 基準の1175 passedを5件上回った。
- Pythonコンパイル: `src/db/connection.py`、`src/web/app.py` 合格。
- Ruff: `src/db/connection.py` と変更テストは合格。巨大な既存 `src/web/app.py` 単独では95件の既存警告があるため、同ファイルはコンパイル・回帰テスト・差分監査で検証した。
- `git diff --check` 合格。
- `src/web/templates/base.html`、`render.yaml`、ROI判定ロジックに差分なし。
- 作業用pytestディレクトリ2件は、リポジトリ配下の記録済み絶対パスであることを確認して削除済み。

検証中の失敗と再発防止:

- 最初のfocusedテストはfake poolの `getconn(timeout=...)` 非対応で1件失敗。実API形状に合わせてfakeを更新した。
- 最初の統合テストは `create_app()` が `.env` のDB URLを読み、fake差替え前に実接続待ちを行った。テスト生成時だけ `DATABASE_URL` を空にし、接続・クエリ・書込みを防いだ。
- 最初の全回帰は先行テストが残した `BOATRACE_TASK_TRIGGER` により新テストが直接接続経路を選び、1件失敗。新テストで環境を明示的に隔離した。
- Flask統合テストでモジュール共有の `time.monotonic` を置換した試行は、psycopg側スレッドにも影響して停止したため終了させた。最終版は実時計測と即時失敗fakeを使用する。

## コミット

- 実装コミット: `5270688` (`Fix web database pool starvation`)
- push・デプロイは実施していない。本番Supabase書込み、本番`/data`操作、ローカル本番スケジューラ実行も行っていない。
