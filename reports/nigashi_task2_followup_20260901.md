# 追加指示: Task 2 — 既存テストの更新許可

作成: 2026-09-01 / 管理: リン / 実装: CODEX

---

## 経緯

`reports/nigashi_tag_task2_spec_20260901.md` の実装は完了しているが、
`tests/test_prewarm_race_detail_tags.py` の 2 テストが落ちている。
仕様書 6 章で同ファイルの変更を許可していなかったため、CODEX が正しく停止した。

**このファイルの更新を許可する。** ただし下記の範囲に限る。

---

## 調査結果 (リンが確認済み)

落ちているのは、いずれも**意図的に変えた実装の詳細を固定していた箇所**である。

| 箇所 | 内容 | 判断 |
|---|---|---|
| 200 行 `legacy_conn.execute_count == 1` | プリウォーム無しの経路の DB 呼び出し回数 | **更新可**。スナップショットのまとめ読みが 1 回増えたため。既存の `_load_entry_change_snapshot_stats` と同じ形の**まとめ読み 1 回**であり、レースごとの N+1 ではない |
| 225 行 `RACE_DETAIL_TAG_CACHE_VERSION = "v6"` | 版数のソース文字列 | **更新可**。版数を上げるのは仕様の必須要件 |
| 230 行 `"escape_rate >= 70.0" in source` | 閾値の直書きをソース文字列で固定 | **更新可**。閾値を定数 1 箇所に集約するのは仕様の必須要件 |

---

## 許可する変更

`tests/test_prewarm_race_detail_tags.py` に対し、**上記 3 箇所のみ**を新しい実装に合わせて更新する。

1. `legacy_conn.execute_count` の期待値を実際の回数に更新する。
   **コメントを添えて「なぜ 1 回増えたか」を書くこと** (スナップショットのまとめ読み)。
2. 版数の期待値を新しい値に更新する。
3. 閾値のソース文字列アサーションを、**新しい定数名を使っていることの確認**に置き換える
   (例: `ESCAPE_WIN_RATE_MIN` がソースに存在し、`70.0` の直書きが
   定数定義以外に無いこと)。

---

## 絶対に守ること

- ❌ **`optimized_conn.execute_count == 0` を緩めない。**
  これはプリウォーム経路がレースごとに DB を引かないことを守る重要な番人である。
  もし 0 にならないなら、テストではなく**実装を直す** (プリウォーム文脈に
  `course_role_by_racer` を必ず入れる)
- ❌ `test_prefetched_tag_build_is_byte_identical_to_individual_build` の
  「プリウォーム有無で結果が一致する」という本質的なアサーションを消さない
- ❌ 上記 3 箇所以外のテストを変更・削除・スキップしない
- ❌ 他の既存テストを弱体化して通すことをしない
- ❌ `git commit` / `git push` しない

---

## 完了条件

1. `tests/test_prewarm_race_detail_tags.py` が全て green
2. `tests/test_nigashi_tag_ui.py` が全て green
3. `.venv/Scripts/python.exe -m pytest tests/ -q` で新たな失敗が増えていない
   (既知failure `test_security_policy_allows_supabase_auth_fetch` のみ許容)
4. `git status` に想定外のファイル変更が出ていない
