# 作業指示書: ゾンビ running タスクの自動回収 (Codex CLI 用)

作成: 2026-08-15 / 発注者: リッキー / 検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本のみ)
現行 main: **697 passed**。

## 背景 (夜間監視 2026-08-15 で確認した実事象)

`task_runs` に **status='running' のまま完了記録が来ない「ゾンビ」レコード**が残る:
- `render_race_detail_all` が **2026-08-02 から13日間 running**
- `render_signal_refresh_16_4` が **2026-08-11 から running**

原因: タスクが `record_task_run(..., 'running')` を書いた後、プロセスが
クラッシュ/kill され、success/failure を記録せず終了する経路がある。現状これを
**検出・回収する仕組みが無い**ため running が永久に残る。監視の「stuck_running」
判定を汚し、将来 advisory 的なガードを足したときの誤判定源にもなる。

## 絶対ルール

1. **origin/main へ push 禁止** (ローカル main まで)。
2. ROI 戦略・予測・DB スキーマ (テーブル定義) ・render.yaml・cron スケジュールは変更しない。
   task_runs のスキーマは既存のまま。**行の status を更新するだけ**。
3. `src/db/cron_runtime.py` の既存 API (record_task_run / advisory_lock /
   ensure_task_runs_table) の**シグネチャと挙動を壊さない** (追加はOK)。
4. テスト: `.venv/Scripts/python.exe -m pytest tests/ -q` — **697 passed を割らない**。
5. 作業ログ `reports/stale_running_reaper_work_log_20260815.md`。コミット 1〜2個。

## やること

### 1. 回収ヘルパーを `src/db/cron_runtime.py` に追加

```
def reap_stale_running_tasks(conn, *, older_than_hours: int = 6,
                             now: datetime | None = None) -> int:
    ...
```

仕様:
- `status='running'` かつ `finished_at IS NULL` かつ `started_at` が
  `now - older_than_hours` より**古い**行だけを対象に、
  `status='failure'`, `finished_at=now`, `detail='stale_running_reaped'` へ UPDATE。
- 回収した行数を返す。
- **安全第一**: しきい値は既定6時間。**実行中の正当なタスク (数分〜十数分) は
  絶対に触らない**。ゾンビは数日前なので6hで十分安全。呼び出し側がより保守的に
  したい場合に `older_than_hours` を上げられるようにする。
- `now` は既存の JST/naive iso と**同じ時刻形式**で比較すること
  (`_now_iso()` と整合。`started_at` は naive iso 文字列)。文字列比較の落とし穴
  (CLAUDE.md の closed_at 比較バグ) を避け、Python 側で datetime 変換して比較するか、
  形式を揃えた文字列で比較する。テストで両形式の整合を必ず確認。
- Postgres / SQLite 両対応 (プレースホルダは既存コードの流儀に合わせる)。
  commit も既存流儀に合わせる。

### 2. スケジューラの入口で1回だけ呼ぶ

`scripts/render_regular_scheduler.py` の**通常 tick の早い段階** (毎 tick 実行されるが
UPDATE 対象は「6h以上前の running」だけなので軽く、多重実行も冪等) で
`reap_stale_running_tasks(conn)` を呼ぶ。回収数>0 のときだけ log 出力。
- どの経路が最も確実に毎 tick 走るかはコードを読んで判断 (既に task_runs へ
  record している経路の近く)。**新しい cron サービスやスケジュールは追加しない**。
- 失敗しても本流を止めない (回収は付随処理。例外は握って log)。

### 3. 既存2件のゾンビについて

本番 Postgres の既存ゾンビ (`render_race_detail_all` / `render_signal_refresh_16_4`) は、
**デプロイ後の次 tick で自動回収される**設計にすること (手動 DB 操作は不要)。
作業ログに「デプロイ後に自動回収される」旨を明記。

## テスト (`tests/` に追加)

- 6h より古い running → failure/`stale_running_reaped`/finished_at セットに回収される。
- 直近 (数分前) の running は**回収されない**。
- success/skipped/failure の行は触られない。
- 2回連続実行しても冪等 (2回目は0件)。
- 時刻形式 (naive iso) の比較が正しい (境界: ちょうど6h前後)。

## 受け入れ条件

- [ ] `reap_stale_running_tasks` 追加、既存 API 無改変
- [ ] regular scheduler が毎 tick 冪等に回収を試み、実行中タスクは触らない
- [ ] 既存2ゾンビはデプロイ後の tick で自動回収される設計
- [ ] `pytest tests/ -q` 697 passed 維持 + 新規 green
- [ ] push していない / 作業ログ提出

## 検品 (リンが実施)

「実行中の正当タスクを誤って殺さないか (しきい値と境界テスト)」「既存 API が無改変か」
「時刻比較が正しいか」「毎 tick 冪等か」「全テスト green か」を照合。デプロイは発注者承認後。
