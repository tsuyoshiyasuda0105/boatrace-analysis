# 仕様書: 逃がし率タグの画面表示 + 逃げの事前テーブル化 (Task 2/2)

作成: 2026-09-01 / 管理: リン / 実装: CODEX
ブランチ: `feature/nigashi-tag-ui`

---

## 0. 前提

Task 1 で `scripts/build_racer_course_role_stats.py` と
テーブル `racer_course_role_snapshots` は完成済み。**本タスクはそれを画面に出す。**

テーブルの列 (0〜1 の小数。走数 0 のときは rate が NULL):

```
snapshot_date, racer_number, window_days,
course1_starts, course1_wins, course1_win_rate,        -- 逃げ
course2_starts, course2_nigashi_count, course2_nigashi_rate,  -- 逃がし
updated_at
```

---

## 1. 作るもの

### 1-A. 新設「壁」タグ (逃がし率)

> **2 号艇の選手**の逃がし率が **65% 以上** かつ **2 コース進入 20 走以上**のとき、
> TOP 画面のレース行にタグを出す。

- バッジのキー: `nigashi`
- 画面の文字: **壁**
- ツールチップ: `2号:壁 73.0% (27/37)` の形

### 1-B. 既存「逃げ」タグを事前テーブルに統一

現在 **3 つの経路がバラバラの定義**で同じバッジを出しており、キャッシュの温まり具合で
意味が変わってしまっている:

| 経路 | 現在の窓 | 最低走数 |
|---|---|---|
| `_hydrate_market_race_badges` のインライン SQL (799 行付近) | 全期間 | なし |
| プリウォームの `escape_by_race` (5940 行付近の SQL) | 直近 180 日 | なし |
| `_boat1_monthly_escape_profile` (6338 行) | 直近 180 日 | なし |

→ **すべて `racer_course_role_snapshots` からの読み出しに統一する。**
判定は **1 号艇の選手・`course1_win_rate` 70% 以上・`course1_starts` 20 走以上**。

---

## 2. 閾値は 1 箇所にだけ置く

`src/web/app.py` に定数として定義し、**他のどこにも数値を書かない**
(CLAUDE.md の既知バグパターン「❌ 片方だけ更新」を避けるため):

```python
COURSE_ROLE_MIN_STARTS = 20        # 逃げ・逃がし共通の最低走数
ESCAPE_WIN_RATE_MIN = 0.70         # 逃げタグの閾値
NIGASHI_RATE_MIN = 0.65            # 壁タグの閾値
```

---

## 3. データの読み方

### 3-1. 読み出しヘルパ

`_load_entry_change_snapshot_stats` (既存) と同じ形で、
**当日出走選手ぶんを 1 クエリでまとめて引く**ヘルパを作る:

```python
def _load_course_role_snapshot_stats(snapshot_date, racer_numbers) -> dict[int, dict]
```

- `WHERE snapshot_date = ? AND racer_number IN (...)`
- 主キー `(snapshot_date, racer_number)` が効くので高速
- **1 レースごとに引かないこと。** 必ずまとめて引く

### 3-2. スナップショットが無い日の扱い (重要)

`racer_course_role_snapshots` は今後の日付から順に溜まる。**過去日には行が無い。**

- **行がある日**: テーブルの値だけを使う (速い・定義が統一される)
- **行が無い日**: **既存の計算経路をそのまま使う** (フォールバック)

→ 過去日を閲覧しても**バッジが消えない**ことを保証する。
既存経路は削除せず、フォールバックとして残すこと。
将来スナップショットを過去日ぶん backfill すれば、この分岐は外せる。

**壁タグ (`nigashi`) にはフォールバックを作らない。** 行が無ければ単に出さない
(新規タグなので「消える」ことがない)。

---

## 4. 実装箇所

### 4-1. レース詳細タグ (`_build_race_detail_tag_snapshot`, 6289 行付近)

`boats[str(boat_number)]` に、既存の `escape_tag` / `entry_change_tag` と同じ作法で追加:

- **1 号艇**: `escape_tag` を**テーブル由来の値**で作る (行があれば)。
  `{"label": "逃げ", "rate": <0-100 の百分率>, "wins": ..., "starts": ...}`
  形式は既存のまま。`rate` は**百分率 (70.0 等)** で入れること (既存コードがそう扱っている)。
- **2 号艇**: `nigashi_tag` を新設。
  `{"label": "壁", "rate": <百分率>, "wins": <逃がし数>, "starts": ...}`

### 4-2. TOP バッジ化 (`_hydrate_market_race_badges`, 694 行付近)

- `boats[n]["nigashi_tag"]` を読んで `badge_info["nigashi"]` を組み立てる。
  既存の `escape` の組み立て (920-951 行付近) と**同じ構造**にすること
  (`items` / `boats` / `max_rate` / `label`)。
- `escape` の直接経路 (833 行付近の `escape_by_race`) も、
  テーブルがある日はテーブル由来の値を使うようにする。

### 4-3. ラベル正規化 (`_normalize_race_badge_labels`, 1107 行付近)

`nigashi` のラベルを既存バッジと同じ作法で組み立てる。

### 4-4. ゲスト可視 (`_GUEST_SAFE_BADGE_KEYS`, 1304 行付近)

`"nigashi"` を**追加する**。表示系タグなので未ログインでも見せる
(既存の `accident` / `escape` / `ace_motor` / `entry_change` / `kimarite` と同じ扱い)。

### 4-5. 画面 (`src/web/templates/index.html` 398-428 行付近)

`escape` / `entry_change` と同じ書き方で `nigashi` バッジを追加する:

```js
if (nigashi) addBadge(row, 'nigashi-watch-badge', '壁', nigashi.label || '2号艇 壁');
```

**注意**: index.html には `{% if not show_today_picks_panel %}` の分岐があり、
**実際に動くのは前半のブロック**である (`show_today_picks_panel` はどこからも渡されていない)。
**必ず動く方 (398-428 行付近) に追加すること。**
バッジ除去のセレクタ (510 行付近) にも `nigashi-watch-badge` を加える。

### 4-6. 見た目 (`src/web/static/style.css` 5007-5086 行付近)

`.nigashi-watch-badge` を追加する。

- 共有のベース定義 (`.accident-watch-badge, .ace-motor-watch-badge, ...` の並び) に
  `.nigashi-watch-badge` を**加える**
- 配色は**既存パレットから選ぶ。新しい色を発明しない**
- 既存の `escape`(シアン) / `事故`(赤) / `M`(金) / `!`(橙) と**見分けがつく色**にすること
- `.race-item.done / .is-closed` の可読性オーバーライド (5088 行付近) にも加える

### 4-7. キャッシュ版数 (必須・忘れると画面が変わらない)

- `RACE_DETAIL_TAG_CACHE_VERSION` (5943 行) を上げる … `boats[n]` に新項目が増えるため
- `TOP_PAGE_SNAPSHOT_VERSION` (1342 行) を上げる … 既存スナップショットを無効化するため

**両方上げること。** 片方だけだと古いバッジが残り続ける
(`_write_top_page_snapshot` は既存バッジを保護する作りのため、新キーが黙って隠れる)。

---

## 5. テスト要件 (`tests/test_nigashi_tag_ui.py` 新規)

1. **壁タグが出る** — 逃がし率 70%・25 走の 2 号艇選手 → `nigashi` バッジが付く
2. **閾値未満は出ない** — 逃がし率 64.9% → 付かない
3. **走数不足は出ない** — 逃がし率 80% だが 19 走 → 付かない
4. **逃げタグがテーブル由来になる** — `course1_win_rate` 0.75・25 走 → `escape` が付き、
   **rate が 75.0 (百分率) で入る**こと
5. **逃げの走数不足は出ない** — 90% だが 19 走 → 付かない (現行は最低走数が無いので新挙動)
6. **スナップショットが無い日は逃げが既存経路にフォールバックする** — 行が無くても
   `escape` バッジが従来どおり出ること
7. **スナップショットが無い日に壁タグは出ない**
8. **ゲストにも壁タグが見える** — `_GUEST_SAFE_BADGE_KEYS` に含まれること
9. **閾値の数値がソースに 1 箇所しか無い** — `0.65` / `0.70` / `20` が
   定数定義以外に散らばっていないこと (静的チェック)

---

## 6. 厳守事項

- ❌ **本番 Postgres に接続・書き込みしない**
- ❌ 変更してよいのは次の 5 つだけ:
  `src/web/app.py` / `src/web/templates/index.html` / `src/web/static/style.css` /
  新規テスト / (必要なら) `tests/test_source_regression.py`
- ❌ **1 レースごとに DB を引かない** (必ずまとめ読み)。本プロジェクトは
  DB 接続枠の逼迫で過去に障害を起こしている
- ❌ 過去日で既存の「逃げ」バッジが消える実装にしない (フォールバック必須)
- ❌ 閾値を 2 箇所以上に書かない
- ❌ 事故率・エースモーター・進入変更の既存バッジの挙動を変えない
- ❌ `git commit` / `git push` しない
- ✅ 日本語コメントは既存ファイルと同じ密度・トーンで

---

## 7. 完了条件

1. 新規テストが全て green
2. `.venv/Scripts/python.exe -m pytest tests/ -q` で新たな失敗が増えていない
   (既知failure `test_security_policy_allows_supabase_auth_fetch` のみ許容)
3. `git status` に指定ファイル以外の変更が出ていない
