# DB接続プール自己修復・フェイルファスト 作業ログ

作業日: 2026-08-15

対象: `C:\boat_project\boatrace-analysis`

実装コミット: `e06e673`

## 本番で確認された事実

- `pool_max=8` に対して `pool_size=2` のまま増加しなかった。
- 2接続とも占有され `pool_available=0` が継続した。
- `requests_waiting` が14から47へ単調増加し、再起動まで復旧しなかった。
- この事象を再現用の本番操作には使わず、提示された本番ログを確定事実として実装条件とした。

## 実装

### 待ち行列の上限とフェイルファスト

- Webプールの `ConnectionPool` に `max_waiting` を設定した。
- Webの既定値は実際の `max_size` と同数。`BOATRACE_DB_POOL_SIZE=8` なら既定 `max_waiting=8`、現行コード既定の `max_size=4` なら4となる。
- `BOATRACE_DB_POOL_MAX_WAITING` で調整できるが、Webでは0以下を1へ丸め、無制限へ戻せない。
- `psycopg_pool.TooManyRequests` を一時DBエラーとして分類し、既存のgraceful degradationへ渡す。
- 待ち行列超過だけは接続層の再試行・sleepを行わず、1回で即時に返す。通常の一時的な接続障害に対する既存の0.2秒、0.5秒の再試行は維持した。

### watchdogによる自己修復

- checkoutが最終的に失敗した時だけプール統計を判定する。
- 次の全条件を満たした時だけ、既存の `_PG_POOL_LOCK` 内で古いプールをcloseし、`_PG_POOL=None` にする。次回checkoutが新しいプールを生成する。
  - `pool_available == 0`
  - `requests_waiting > 0`
  - checkout失敗の観測が2回以上
  - 枯渇状態が既定90秒以上継続
  - 前回再生成から既定60秒以上経過
- checkoutが1回でも成功した場合、枯渇開始時刻と失敗回数をリセットする。瞬間的な高負荷だけでは再生成しない。
- `BOATRACE_DB_POOL_EXHAUSTION_SEC` と `BOATRACE_DB_POOL_REBUILD_COOLDOWN_SEC` で調整可能。誤設定による再生成ループを防ぐため、通常設定は最低30秒に丸める。
- 枯渇の初回検知、checkout失敗統計、自己修復実行をログへ記録する。新テーブルやDB書き込みは追加していない。

### cron・バッチとの分離

- `BOATRACE_TASK_TRIGGER` があるcron・バッチは従来どおり短命の直結接続を使い、Web共有プールを使わない。
- watchdogは `BOATRACE_TASK_TRIGGER` があるプロセスでは明示的に無効。
- プール設定関数を呼ぶ場合も、cron側の `max_waiting` 既定値は0（psycopg_poolの無制限）として分離した。
- cron定義、`render.yaml`、DBスキーマ、ROI、予測ロジックは変更していない。

## テスト

- 対象＋graceful degradation: `23 passed`。
- 全テスト: `811 passed`（変更前802件＋新規9件）。
- Pythonコンパイル: pass。
- `git diff --check`: pass。
- pytestは既存の `.pytest_cache` を作れないWindows警告を1件出したが、テスト失敗はない。専用の `--basetemp` を使用し、作業後にその一時ディレクトリだけを削除した。

追加した確認項目:

- Webの有限 `max_waiting` と0指定時の安全な丸め。
- `TooManyRequests` の一時障害分類と、sleepなし・1回での即時失敗。
- 90秒未満では再生成しないこと、継続後は再生成すること。
- checkout成功で枯渇判定がリセットされ、瞬間的な高負荷で誤爆しないこと。
- クールダウン中に連続再生成しないこと。
- cronプロセスではwatchdogが動かず、待ち行列制限もWebと分離されること。
- 既存の接続再試行とgraceful degradationの回帰がないこと。

## 省略した任意項目

管理画面へのプール統計表示は省略した。今回の必須ゴールは接続層のログだけで記録でき、`src/web/app.py` まで変更範囲を広げない方が低リスクなため。

## 禁止事項・後始末

- push、deploy、本番DB書き込み、ローカルscheduler起動は実施していない。
- 新テーブル、DBスキーマ変更、データ削除はない。
- ROI・予測・`render.yaml` は変更していない。
- テスト専用一時ディレクトリ `.pytest_tmp_db_pool_selfheal_20260815` のみ削除済み。復元不要のテスト生成物であり、他の未追跡ファイルには触れていない。
