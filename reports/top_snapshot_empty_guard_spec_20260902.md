# 仕様書: TOP 画面が空で固まる障害の恒久対策 (原因B)

作成: 2026-09-02 / 管理: リン / 実装: CODEX
ブランチ: `fix/top-snapshot-empty-guard`

---

## 1. 直す障害

2026-09-02 朝、本番 TOP 画面が「この日のデータはありません」で固まった。
**DB にはレースが 156 件・選手 936 名分・予測 156 件すべて正常に入っていた**のに、
**画面用スナップショットだけが空**で保存され、当日 1 度も作り直されなかった。

### 原因の連鎖 (実測で確定)

```
OpenAPI が openapi_incomplete で 9 回連続失敗 (missing_stadiums=[16,17,18])
  → render_program_source_gate_v1 が ready にならない
  → run_lite_daytime_bootstrap が 850 行で早期 return
     "[lite-bootstrap] source gate not ready -> skip downstream prewarm"
  → 末尾の run_top_page_snapshot(lightweight=True) に到達しない
  → 当日 1 度も作り直されない
  → 前夜 22:04 の run_next_day_top_snapshot が焼いた空データが残り続ける
     ※ 22:04 時点で翌日の番組表は未着 (official 取込は 23:33)。生成の方が先だった
```

**公式ソースは完全なのに、OpenAPI の欠けだけで画面生成全体が止まっていた。**

---

## 2. 成果物

1. `scripts/build_top_page_snapshot.py`
2. `scripts/render_regular_scheduler.py`
3. `tests/test_top_snapshot_empty_guard.py` (新規)

**それ以外のファイルは変更しないこと。**

---

## 3. 修正 1: 空のスナップショットを保存しない

`scripts/build_top_page_snapshot.py` は現在、**中身が空でも無条件に書き込む** (46 行
`web_app._write_top_page_snapshot(args.date, payload)`)。
これが「毒」を作る唯一の入口である。

### 直し方

書き込みの**前**にレース数を数え、**0 件なら書き込まずに正常終了 (exit 0)** する。

- レース数の数え方は既存の集計 (48-49 行) と同じ
  (`stadium_groups` 内の `races` の合計)
- 書き込みをスキップしたことが**ログで分かる**ようにすること。例:
  `[top-snapshot] date=... SKIPPED write: no races yet (existing snapshot left untouched)`
- **終了コードは 0** にする (失敗ではなく「まだ早い」だけなので、cron を赤くしない)
- `--lightweight` / `--environment-only` / `--signals-degraded` の**どのモードでも同じ**に扱う

> **設計判断**: 全国どの会場も開催しない日は事実上存在しないため、
> 「レース 0 件なら書かない」で問題ない。万一そういう日があっても、
> 誤った「データはありません」を焼き付けるより、スナップショット無し
> (画面は再試行を促す一時ページ) の方が安全である。

---

## 4. 修正 2: ソースゲート不成立でも TOP 画面だけは作る

`scripts/render_regular_scheduler.py` の `run_lite_daytime_bootstrap` (805 行) は、
ソースゲートが ready でないと 848-852 行で早期 return し、
**末尾の `run_top_page_snapshot(now, lightweight=True)` (866 行) に到達しない。**

ゲートは「不完全なデータを見せない」ための仕組みであり、**残す**。
ただし **当日のレースが既に DB にあるなら、画面を出さない理由はない。**

### 直し方

850 行の早期 return の**直前**に、次を追加する:

1. 当日 (`today`) の `races` 件数を DB から数える
   (既存の 385 行付近に同じ形の COUNT クエリがあるので、その作法に合わせる)
2. **1 件以上あれば `run_top_page_snapshot(now, lightweight=True)` を実行する**
3. その結果をログと `detail` に残す。例:
   `detail="source_gate_not_ready(top_snapshot_rebuilt)"` /
   `detail="source_gate_not_ready(top_snapshot_failed)"` /
   レース 0 件なら従来どおり `detail="source_gate_not_ready"`
4. **その後は従来どおり `return False`** を返す
   (ゲートが本当に不成立なのは事実なので、重い prewarm は引き続き止める)

### 絶対に守ること

- ❌ **ソースゲートそのものを無効化・迂回しない。** 止めるのは
  「TOP 画面の生成だけを例外的に通す」ことに限る
- ❌ 早期 return をやめて後続の prewarm/signal 処理まで走らせてはいけない
- ❌ `record_task` の失敗記録を成功に変えない (ゲート不成立は失敗のまま)

---

## 5. テスト要件 (`tests/test_top_snapshot_empty_guard.py`)

1. **レース 0 件なら書き込まない** — `_write_top_page_snapshot` が呼ばれないこと
2. **レース 0 件でも終了コードは 0**
3. **レースがあれば従来どおり書き込む** — `_write_top_page_snapshot` が呼ばれること
4. **`--lightweight` でもレース 0 件なら書き込まない**
5. **ゲート不成立 + 当日レースあり → `run_top_page_snapshot` が呼ばれる**
6. **ゲート不成立 + 当日レース 0 件 → `run_top_page_snapshot` は呼ばれない**
7. **ゲート不成立時は、画面を作り直しても `run_lite_daytime_bootstrap` は False を返す**
   (ゲートの意味を壊していないことの保証)
8. **ゲート成立時の従来の流れが変わらない** — 既存どおり末尾まで進むこと

DB は monkeypatch / 一時 SQLite で代用し、**本番 DB に触れないこと**。

---

## 6. 厳守事項

- ❌ **本番 Postgres に接続・書き込みしない**
- ❌ 指定 3 ファイル以外を変更しない
- ❌ `render.yaml` の cron スケジュールを変更しない
- ❌ ソースゲートの判定ロジック自体を変更しない
- ❌ 既存テストを弱体化・スキップして通さない
- ❌ `git commit` / `git push` しない
- ✅ 日本語コメントは既存ファイルと同じ密度・トーンで。
  **なぜこの例外を設けたのか (2026-09-02 の障害) を必ずコメントに残すこと**

---

## 7. 完了条件

1. `tests/test_top_snapshot_empty_guard.py` が全て green
2. `.venv/Scripts/python.exe -m pytest tests/ -q` で新たな失敗が増えていない
   (既知failure `test_security_policy_allows_supabase_auth_fetch` のみ許容)
3. `git status` に指定 3 ファイル以外の変更が出ていない
