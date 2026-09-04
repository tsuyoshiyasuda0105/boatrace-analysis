# 追補指示書: ゾンビ回収の条件を広げる (Codex CLI 用)

作成: 2026-08-15 / 発注者: リッキー / 検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本のみ)
現行 main: **701 passed**。直前コミット `7a5067a` (reap_stale_running_tasks 追加) の**追補修正**。

## 背景 (検品で発見した取りこぼし)

本番 `task_runs` の2ゾンビのうち、片方が現行 reaper の条件から漏れる:

- `render_race_detail_all` (run_date=2026-08-02): `status='running'`, `finished_at IS NULL`
  → 現行 reaper で回収される ✓
- `render_signal_refresh_16_4` (run_date=2026-08-11): `status='running'` だが
  **`finished_at` がセット済み** (started_at==finished_at) → 現行 reaper の
  `finished_at IS NULL` 条件から漏れて**回収されない** ✗

`status='running'` かつ `finished_at` がセットされた行は**自己矛盾**である。
`record_task_run` の 'running' 経路は必ず `finished_at=NULL` にするため、
「今まさに実行中」の行は必ず finished_at が NULL。よって
**`finished_at` がセットされた 'running' 行は現在実行中ではない**ので、回収して安全。

## やること

`src/db/cron_runtime.py::reap_stale_running_tasks` の回収条件から
**`finished_at IS NULL` の要件を外す**。回収対象を:

> `status='running'` かつ `started_at` が `now - older_than_hours` より古い行

に広げる (finished_at の有無を問わない)。

- SELECT 側の `finished_at IS NULL` フィルタを削除。
- 競合防止の UPDATE 再チェックからも `finished_at IS NULL` を外す
  (再チェックは `status='running' AND started_at=?` で引き続き安全:
  SELECT〜UPDATE の間にタスクが完了すれば status が 'running' でなくなり 0 件になる)。
- **live 保護は `started_at < now-6h` の閾値で維持** (実行中の正当タスクは
  started_at が新しいので絶対に対象外)。この安全性は変えない。
- 回収時の `finished_at` 書き込み・`detail='stale_running_reaped'`・返り値 (回収件数) は現行のまま。

## 絶対ルール

1. **push 禁止** (ローカル main まで)。
2. スキーマ・ROI・予測・render.yaml・cron スケジュール不可侵。既存 API 無改変。
3. `.venv/Scripts/python.exe -m pytest tests/ -q` — **701 passed を割らない**。
4. 作業ログは既存 `reports/stale_running_reaper_work_log_20260815.md` に追記。コミット1個。

## テスト追加 (回帰防止)

- **今回の取りこぼしケース**: `status='running'` かつ `finished_at` がセット済み
  かつ `started_at` が 6h より古い行が回収される (= 8/11 ゾンビの再現)。
- 既存の境界・冪等・「実行中(直近)は触らない」テストは**そのまま green** であること
  (直近の running は finished_at の有無にかかわらず、started_at が新しいので対象外)。

## 受け入れ条件

- [ ] `finished_at IS NULL` 要件が外れ、`status='running' AND started_at<閾値` で回収
- [ ] finished_at セット済みの古い running も回収される新テストあり
- [ ] 実行中(直近)の running は引き続き触らない (境界テスト green)
- [ ] `pytest tests/ -q` 701+ green / 既存 API 無改変 / push なし / 作業ログ追記

## 検品 (リンが実施)

「finished_at セット済みの古い running が回収されるか」「直近 running を誤って
殺さないか (started_at 閾値)」「既存テスト全 green か」を照合。デプロイは発注者承認後。
