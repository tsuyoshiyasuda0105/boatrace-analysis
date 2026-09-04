# 作業指示書: /member/today-races の 500 (DB接続枯渇) 修理 (Codex CLI 用)

作成: 2026-08-20 / 発注: リッキー / 診断・検品: リン (Claude)
テスト: .venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e
(現状 1175 passed, 1 skipped。割らないこと)

## ユーザ報告 (実害)

「本日のレース画面から日付と表示を押しても画面が変わらない」
「本日のレースからバックテストLABを押しても画面が変わらない」

## 症状 (リン診断済み・確定)

`system_status.slow_request` の detail_json より、本番の実測:

```json
{"at": "2026-08-20T22:13:09+09:00", "path": "/member/today-races",
 "status": 500, "elapsed_ms": 64012.0, "db_queries": 0, "db_time_ms": 0.0}
{"at": "2026-08-20T12:37:43+09:00", "path": "/member/today-races",
 "status": 500, "elapsed_ms": 59165.5, "db_queries": 0, "db_time_ms": 0.0}
```
20:08 にも同様の 500 あり (当日 312 件の slow request)。

**重要**: `db_queries: 0` / `db_time_ms: 0.0` — **1本もクエリを投げていない**。
ページ処理が重いのではなく、**DB接続を最後まで取得できず 500 で終わっている**。
ユーザには「押しても画面が変わらない」= 60秒待たされてエラー、として現れる。

関連する実測エラー (リンが内部エンドポイント呼び出し時に観測):
- `TooManyRequests: the pool 'pool-1' has already 12 requests waiting`
- `PoolTimeout: couldn't get a connection after 5.00 sec`

**Postgres 側は健全**: pg_stat_activity は active=1 / idle=8、5秒超のクエリ 0 件。
つまり詰まっているのは **web プロセス側のクライアントプール**であって、DB ではない。

プール設定 (src/db/connection.py:553-590):
- web: max_size=4 / max_waiting=max_size / timeout=5秒 / max_lifetime=900 / max_idle=120
- 5秒でタイムアウトするはずが実測 64秒 → **リトライが累積**している疑い
  (src/db/connection.py:287 付近の retry_event 経路)

ローカル (本番同一コード・本番DB, port 5070) では同じURLが **0.23〜0.96秒** で 200。
再現しないので、コード自体ではなく**本番の同時実行下でのみ枯渇**している。

## やること

### [必須1] 接続を長時間握っている経路の特定と解消
web プロセスで、プール接続を取得したまま **DB以外の遅い処理** をしている箇所を全数調査し、
接続を早期返却するよう直す。重点的に見るべき候補:
 - リクエスト処理中の HTTP 呼び出し / ファイルI/O / テンプレート描画
 - 起動時バックグラウンドスレッド (kachisuji デルタ自動適用)
 - prewarm / signal 系のアプリ内実行経路
 - 接続リーク (取得後に close/return されない経路。with 文漏れ)
**まず計測を入れて事実を掴むこと** (プールの取得待ち時間・保持時間・同時取得数を
system_status か task_runs に記録)。当て推量で直さない。

### [必須2] 待ち時間の上限を設けフェイルファストにする
60秒待たせた末の 500 は最悪の体験。接続が取れない場合は
 - リトライ総時間に上限を設ける (合計 10 秒程度を提案)
 - ユーザには 500 ではなく「混雑中」の分かるページ/メッセージを返す
   (既存の degraded 表示の作法に合わせる)

### [必須3] プールサイズの妥当性検討
max_size=4 が本番の同時実行に対して妥当か検証し、根拠を作業ログに書く。
**注意**: Supavisor のクライアント枠は web と cron で共有。安易に増やすと
cron 側を枯渇させる。増やす場合は cron 側の消費実測を添えて提案し、
**コードのデフォルト変更は最小限に**。環境変数で調整可能な設計は既にある。

### [必須4] 回帰テスト
 - 接続が取れないときに 60秒待たず、上限時間内に分かる応答を返すテスト
 - 接続を握ったまま遅い処理をしないことの静的/単体チェック

## 絶対ルール
- push 禁止・デプロイ禁止・本番Supabase書込み禁止・本番/data操作禁止
- 採用ROI戦略の判定結果を変えない / render.yaml の cron 構成を増やさない
- UI マークアップ (base.html のナビ・日付フォーム) は変更しない
  (リン検証でローカルでは正常動作。原因はサーバ側)
- 直近の a7efdb8 / 8f5b810 / 7964324 / df4d2e7 / 4ca07ad を壊さない
- 作業ログ: reports/member_today_races_pool_starvation_work_log_20260820.md
  (計測結果 / 原因 / 変更点 / テスト結果 / コミットID)
