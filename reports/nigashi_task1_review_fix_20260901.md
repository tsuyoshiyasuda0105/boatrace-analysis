# 仕様書: 逃がし率 Task 1 — レビュー指摘の修正

作成: 2026-09-01 / 管理: リン / 実装: CODEX
ブランチ: `feature/nigashi-task1-fixes`

---

## 0. 前提

`reports/nigashi_rate_tag_spec_20260901.md` の Task 1 として実装済みの
`scripts/build_racer_course_role_stats.py` と `tests/test_racer_course_role_stats.py`
(どちらも未コミット・作業ツリーに存在) に対し、コードレビューで出た 5 件を潰す。

**計算ロジック自体は検証済みで正しい** (独立実装と 527 選手を突き合わせ、不一致 0 件)。
**その正しさを壊さないこと。** 本タスクは守りの補強のみ。

Task 2 (画面表示) は本タスクの範囲外。**`src/web/` は一切変更しない。**

---

## 1. 指摘 1: 「逃がさなかった」ケースが 1 着不在レースになっている

`tests/test_racer_course_role_stats.py` の `_add_race` (45-69 行):

```python
other_finish = 1 if course1_wins else (2 if target_finish == 1 else 3)
```

`course1_wins=False` かつ `target_finish != 1` のとき、**どの艇も finishing_position=1 にならず、
勝者不在のレースが作られる**。その結果 `test_course2_nigashi_rate_is_correct` の否定ケースは
「1 コースが勝たなかった」ではなく「誰も勝っていない」を検証してしまっている。

実データで圧倒的に多い **「4 コースなど外の艇が 1 着」** という本来の否定ケースが一度も通らない。

### 直し方

`_add_race` を、**否定ケースでは必ず「1 コース以外の艇が 1 着になる」**ように変更する。
勝者不在のレースを作らないこと。

- 逃がし成立 (`course1_wins=True`): 1 コースの艇が 1 着
- 逃がし不成立 (`course1_wins=False`): **1 コース以外 (例: 3 コース) の艇が 1 着**

必要なら艇を 1 つ増やしてよい。**既存 8 テストの期待値 (0.75 等) は変えないこと。**

---

## 2. 指摘 2: course_number の 0 / NULL 補正が未テスト

実装の中核である `COALESCE(NULLIF(rr.course_number, 0), e.boat_number)` の補正経路を
どのテストも通っていない (テストは常に有効な 1-6 を入れている)。
本番 `data/boatrace.db` には course_number が NULL の行が約 6,388 件、0 の行も存在する。

### 直し方

テストを 2 件追加する:

- **`course_number` が NULL のとき `boat_number` が使われる** — 2 号艇で NULL なら
  コース 2 として母数に入ること
- **`course_number` が 0 のとき `boat_number` が使われる** — 同上

これにより、将来 SQL を「単純化」して `rr.course_number` 直参照に戻す変更が入れば
テストが落ちる。

---

## 3. 指摘 3: インデックスが主キーと完全重複

`scripts/build_racer_course_role_stats.py:45-46`:

```sql
CREATE INDEX IF NOT EXISTS idx_racer_course_role_snapshot_date
  ON racer_course_role_snapshots(snapshot_date, racer_number);
```

`PRIMARY KEY (snapshot_date, racer_number)` と**列も順序も同一**で、検索上の価値がゼロ。
1 日約 1,400 行 × 365 日 = 年間約 50 万行に対し、書き込みコストと容量を二重に払う
(CLAUDE.md が挙げる Supabase 容量制約に直接効く)。

### 直し方

**この `CREATE INDEX` を削除する。** 主キーの一意インデックスで十分。

---

## 4. 指摘 4: 本番入口 `build()` / `main()` が未テスト

テストは `build_rows()` と `upsert_rows()` を直接呼ぶだけで、
cron が実際に叩く `build()` / `main()` と `--local` の DATABASE_URL 遮断処理 (16-18 行) を
一度も実行していない。

### 直し方

テストを 2 件追加する:

- **`main()` が正常終了する** — 一時 SQLite を指すようにして `main()` を呼び、
  戻り値 0 かつ `racer_course_role_snapshots` に行が入ること
- **不正な `--date` で `main()` が 1 を返す** — 例外を投げず終了コード 1 になること

`db_connect` を monkeypatch して一時 DB を返す等、**本番 DB に触れない方法**で行うこと。

---

## 5. 指摘 5: 不要な ORDER BY と接続の長時間保持

`scripts/build_racer_course_role_stats.py:125`:

```sql
ORDER BY p.racer_number, p.race_id
```

集計は Python 側で `defaultdict` に畳むだけで**順序に依存しない**のに、
全件 (直近 1 年 = 実質 55,052 レース分) をソートしている。

### 直し方

1. **`ORDER BY` 句を削除する。**
2. **`db_connect()` を `db_connect(direct=True)` に変更する。**
   このスクリプトは夜間 cron の長時間クエリ (ローカル計測 8.5 秒) で、
   web が共有するプール (max_size=4) を長く握ると利用者の表示を止めうる。
   `src/kachisuji/delta_transport.py:110-116` に同じ理由の先例があるので、
   **同じ作法・同じ趣旨のコメント**を添えること。
   ※ ローカル SQLite では `direct` は無害に無視される。

---

## 6. 厳守事項

- ❌ **本番 Postgres に接続・書き込みしない。** 動作確認は必ず `--local`
- ❌ `scripts/build_racer_course_role_stats.py` と
  `tests/test_racer_course_role_stats.py` 以外を変更しない
- ❌ **`src/web/` を変更しない** (画面は Task 2)
- ❌ 逃がし率・逃げ率の**計算定義を変えない** (窓・母数・分子・コース判定式はそのまま)
- ❌ 既存 8 テストを削除・弱体化しない
- ❌ 閾値 (20 走・65%・70%) をこのスクリプトに書かない
- ❌ `git commit` / `git push` しない

---

## 7. 完了条件

1. `tests/test_racer_course_role_stats.py` が全て green (8 + 追加4 = 12 件以上)
2. `.venv/Scripts/python.exe -m pytest tests/ -q` で新たな失敗が増えていない
   (既知failure `test_security_policy_allows_supabase_auth_fetch` のみ許容)
3. `git status` に指定 2 ファイル以外の変更が出ていない
