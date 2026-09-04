# 仕様書: 選手コース別ロール集計テーブル (Task 1/2)

作成: 2026-09-01 / 管理: リン / 実装: CODEX
ブランチ: `feature/nigashi-rate-tag`

---

## 0. このタスクの位置づけ

最終ゴールは 2 つ:

- **(新設)** 「逃がし率」タグ — 2 号艇の選手が「壁」となり 1 号艇を逃がした率が高いレースに TOP 画面でタグを出す
- **(改修)** 既存「逃げ」バッジを事前テーブル方式に統一する

本タスク **Task 1 はその集計基盤のみ**を作る。**画面表示・既存挙動の変更は Task 2 で行うため、本タスクでは
`src/web/` 配下を一切変更しないこと。** 純粋な追加のみ。

---

## 1. 背景 (なぜ事前テーブルにするか)

現在の「逃げ」バッジは 2 つの経路が別々に SQL を流しており、**同じバッジなのに定義が食い違っている**:

| 経路 | 窓 | 実装 |
|---|---|---|
| TOP 直接 | **全期間** | `src/web/app.py:719-748` (`_hydrate_market_race_badges` 内のインライン SQL) |
| レース詳細タグ | **直近 180 日**(月初締め) | `src/web/app.py:5930-5964` + `_monthly_snapshot_window` (6266) |

しかも後者が前者を上書きする (`app.py:944` が `app.py:844` の結果を上書き) ため、
**キャッシュの温まり具合でバッジの意味が変わる**。加えて `race_results.course_number` に
インデックスが無く、都度スキャンは本番 Postgres の接続逼迫リスクになる。

→ 選手ごとの数値を**毎日 1 回だけ集計してテーブルに置き**、画面はそれを引くだけにする。

---

## 2. 用語と厳密な定義

### コース判定 (リポジトリ共通イディオム。必ずこれを使う)

```sql
COALESCE(NULLIF(rr.course_number, 0), e.boat_number)
```

`race_results.course_number` は実際の進入コース。0 と NULL が混在するため上記で補正する。

### 逃げ率 (escape rate)

> ある選手が **1 コース**に進入したレースのうち、**その選手自身が 1 着**だった率。

- 母数: 選手のコース = 1 かつ `rr.finishing_position IS NOT NULL`
- 分子: 上記のうち `rr.finishing_position = 1`

### 逃がし率 (nigashi rate) — 新規

> ある選手が **2 コース**に進入したレースのうち、**1 コースの艇が 1 着**だった率。

- 母数: 選手のコース = 2 かつ `rr.finishing_position IS NOT NULL`
- 分子: 上記のうち、**そのレースの 1 着艇のコースが 1**

`finishing_position IS NOT NULL` を母数の条件にするのは意図的:
フライング・出遅れ・失格の艇はレースから除外され、**壁として機能し得ない**ため。

### 集計窓 (両指標共通)

**直近 1 年**。`snapshot_date` を基準に

```
race_date >= (snapshot_date - 365日)  AND  race_date < snapshot_date
```

`< snapshot_date` は当日結果の混入 (未来リーク) を防ぐため。**必ず未満**にすること。

### 最低試行数・閾値は本タスクでは扱わない

テーブルには **生の回数と率だけ**を入れる。「20 走以上」「65%」「70%」といった閾値判定は
Task 2 で `src/web/app.py` の定数として 1 箇所にのみ置く。

> **理由**: CLAUDE.md の既知バグパターン「❌ 片方だけ更新」を避けるため。
> 閾値をスクリプトとアプリの両方に書くと、片方を直し忘れて表示が食い違う。

---

## 3. 成果物 (この 2 ファイルのみ)

1. `scripts/build_racer_course_role_stats.py` (新規)
2. `tests/test_racer_course_role_stats.py` (新規)

**既存ファイルは 1 行も変更しないこと。**

---

## 4. テーブル定義

名前: `racer_course_role_snapshots`

```sql
CREATE TABLE IF NOT EXISTS racer_course_role_snapshots (
  snapshot_date         TEXT NOT NULL,
  racer_number          INTEGER NOT NULL,
  window_days           INTEGER NOT NULL,
  course1_starts        INTEGER NOT NULL,
  course1_wins          INTEGER NOT NULL,
  course1_win_rate      REAL,
  course2_starts        INTEGER NOT NULL,
  course2_nigashi_count INTEGER NOT NULL,
  course2_nigashi_rate  REAL,
  updated_at            TEXT NOT NULL,
  PRIMARY KEY (snapshot_date, racer_number)
);
CREATE INDEX IF NOT EXISTS idx_racer_course_role_snapshot_date
  ON racer_course_role_snapshots(snapshot_date, racer_number);
```

- `*_rate` は **0〜1 の小数**で格納する (百分率にしない)。
- **starts が 0 のとき rate は `NULL`** にすること。0.0 にしてはいけない
  (「データ無し」と「0%」を区別できなくなる)。
- スキーマ作成は `scripts/build_racer_entry_change_stats.py` の `ensure_schema()` と同じく
  スクリプト内の `conn.executescript(...)` で行う。別途マイグレーションファイルは作らない
  (`TEXT/INTEGER/REAL` は SQLite・Postgres 双方で通る)。

---

## 5. 実装方針

### 雛形

**`scripts/build_racer_entry_change_stats.py` を雛形として構造を踏襲すること。**
(`ensure_schema` → `_target_racers` → `_history_rows` → `build_rows` → upsert → `main`)

### DB 接続

- **必ず `from src.db.connection import connect as db_connect` を使う。**
  直接 `sqlite3.connect()` を書かない (WAL・busy_timeout・Postgres 変換が効かなくなる)。
- SQL は SQLite 方言 (`?` プレースホルダ) で書いてよい。
  `_PgConnection` が `?` → `%s` に自動変換する。

### 集計対象の選手

`snapshot_date` に出走する選手のみ (entry_change と同じ):

```sql
SELECT DISTINCT e.racer_number
  FROM races r JOIN race_entries e ON e.race_id = r.race_id
 WHERE r.race_date = ? AND e.racer_number IS NOT NULL
```

### 集計 SQL の必須要件

**1 着艇の重複に対する防御を必ず入れること。** データ不整合で 1 レースに
`finishing_position = 1` の行が複数ある場合があり、素直に JOIN すると二重計上される。
勝者側は必ず **レース単位に畳んでから** JOIN する:

```sql
win AS (
  SELECT race_id,
         MAX(CASE WHEN COALESCE(NULLIF(course_number, 0), boat_number) = 1
                  THEN 1 ELSE 0 END) AS course1_won
    FROM race_results
   WHERE finishing_position = 1
   GROUP BY race_id
)
```

同様に、コース 2 側も 1 レース 1 選手 1 行になることを保証すること
(`race_entries` の PK は `(race_id, boat_number)` なので通常は 1 行だが、
`COUNT(DISTINCT ...)` 等で明示的に守る)。

### 2 指標を 1 回のスキャンで

コース 1 とコース 2 は同じ履歴テーブルから取れる。**選手履歴の取得は 1 クエリにまとめ**、
Python 側でコース別に振り分けること (entry_change と同じ形)。全走レース分を 2 回
スキャンしない。

### 書き込み

- 同一 `snapshot_date` の再実行で重複しないよう **upsert** すること。
  `scripts/build_racer_entry_change_stats.py` の書き込み方法に合わせる
  (SQLite/Postgres 双方で動く形にする)。
- 対象選手が 0 人なら何も書かずに正常終了 (exit 0)。

---

## 6. CLI 仕様

```
python scripts/build_racer_course_role_stats.py [options]
```

| オプション | 既定 | 内容 |
|---|---|---|
| `--date YYYY-MM-DD` | 今日 (JST) | スナップショット日 |
| `--window-days N` | `365` | 集計窓の日数 |
| `--local` | off | ローカル SQLite に書く |
| `--log-file PATH` | なし | UTF-8 で直接ログ出力 |
| `--verbose` | off | 進捗表示 |

### `--local` の実装 (重要・過去に事故あり)

`config.py` の `load_dotenv` が `.env` の `DATABASE_URL` を復活させるため、
**`config` を import する前に `os.environ` から `DATABASE_URL` を pop すること。**
`scripts/backfill_official.py:246` 付近に同じ処理があるので、そこに合わせる。

> 過去、これを忘れてローカル埋めのつもりが**本番 Postgres に書き込む**事故が起きている。

### 終了コード

正常 0 / 失敗 1。最後に 1 行サマリを出す:

```
[summary] date=2026-09-01 racers=1440 course1_rows=... course2_rows=... elapsed=..s
```

---

## 7. テスト要件 (`tests/test_racer_course_role_stats.py`)

`pytest` の `tmp_path` に小さな SQLite を組み立てて検証する。**本番 DB に触れないこと。**
既存テストの書き方 (`tests/test_delta_backfill_replace.py` 等) に合わせる。

最低限、以下を各 1 ケース以上:

1. **逃げ率が正しい** — 1 コース進入 4 走・うち 2 着 1 回/1 着 3 回 → `course1_win_rate == 0.75`
2. **逃がし率が正しい** — 2 コース進入 4 走・うち 1 コース艇が 1 着 3 回 → `course2_nigashi_rate == 0.75`
3. **窓の外は数えない** — `snapshot_date - 366日` のレースが母数に入らない
4. **当日は数えない** — `race_date == snapshot_date` のレースが母数に入らない (未来リーク防止)
5. **starts が 0 なら rate は NULL** — 一度も 2 コースに入っていない選手の `course2_nigashi_rate is None`
6. **1 着重複への防御** — 1 レースに `finishing_position=1` が 2 行ある異常データでも
   `course2_nigashi_count` が 1 を超えない
7. **失格等を母数から除く** — `finishing_position IS NULL` の行が母数に入らない
8. **再実行しても重複しない** — 同じ `snapshot_date` で 2 回実行して行数が変わらず、値が更新される

---

## 8. 厳守事項

- ❌ **`src/web/` 配下を変更しない** (画面は Task 2)
- ❌ **本番 Postgres に接続・書き込みしない。** 動作確認は必ず `--local`
- ❌ `sqlite3.connect()` を直接書かない (`src.db.connection.connect` を使う)
- ❌ 閾値 (20 走・65%・70%) をこのスクリプトに書かない
- ❌ `git commit` / `git push` しない (レビュー後にリンが行う)
- ❌ 既存ファイルの変更・削除・リネームをしない
- ✅ 日本語コメントは既存ファイルと同じ密度・トーンで

---

## 9. 完了条件

1. `scripts/build_racer_course_role_stats.py` と `tests/test_racer_course_role_stats.py` が存在する
2. `.venv/Scripts/python.exe -m pytest tests/test_racer_course_role_stats.py -q` が全て green
3. `.venv/Scripts/python.exe -m pytest tests/ -q` で **新たな失敗が増えていない**
4. `git status` に上記 2 ファイル以外の変更が出ていない
