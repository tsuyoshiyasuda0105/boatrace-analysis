# 仕様書: ページキャッシュ再検証 — レビュー指摘の修正 (第2版)

作成: 2026-09-01 / 管理: リン / 実装: CODEX
ブランチ: `fix/page-cache-revalidation` (第1版の続き。既存の変更の上に修正を重ねる)

---

## 0. 前提

第1版で `_PAGE_HTML_MEM_CACHE` に再検証を入れ、本番障害の再現テストが
「修正前は落ちる / 修正後は通る」ことまで確認済み。**その成果は壊さないこと。**
本仕様はレビューで出た 5 件の指摘を潰すもの。

---

## 1. 【最重要】メモ化時刻の取りこぼしを構造的に無くす

### 問題

第1版は「値の辞書 `_PAGE_HTML_MEM_CACHE`」と「時刻の辞書 `_PAGE_HTML_MEM_CACHE_AT`」を
**呼び出し側の規律で対に保つ**設計になっており、2 つの穴が開いている:

1. `_mem_cache_is_fresh` は時刻が無いキーを **True (永久に新鮮)** と扱う。
   `_mem_cache_put` を経ずに `_PAGE_HTML_MEM_CACHE[key] = (...)` と直接代入すれば、
   そのキーは再検証を一生受けない = **今回直した固着バグがそのまま復活する。**
2. 既存テスト 15 ファイルが `_PAGE_HTML_MEM_CACHE.clear()` だけを呼んでおり、
   `_PAGE_HTML_MEM_CACHE_AT` に**孤児エントリが残る**。後続テストが同じキーを
   直接注入すると前のテストの時刻で判定され、実行順やマシン速度で結果が変わる。

### 直し方: 辞書そのものに時刻を刻ませる

`_PAGE_HTML_MEM_CACHE` を **`dict` を継承した専用クラスのインスタンス**にし、
**書き込み・削除の全経路でメモ化時刻を自動管理**する。呼び出し側の規律に依存しない。

```python
class _PageHtmlMemCache(dict):
    """値とメモ化時刻を必ず対で管理するメモリキャッシュ。

    時刻を別辞書で持って呼び出し側の規律に任せると、直接代入や clear() の
    片手落ちで「永久に新鮮」なキーが生まれ、DB を読み直さなくなる
    (2026-08-30 / 09-01 の TOP 画面固着の再発経路)。書き込み口を型で塞ぐ。
    """
```

要件:

- `__setitem__` / `update` / `setdefault` — 値を入れると同時に**現在時刻を記録**する
  (**`dict.update` は CPython では `__setitem__` を呼ばないので、`update` は必ず自前で実装**)
- `pop` / `popitem` / `__delitem__` / `clear` — **時刻も必ず一緒に消す**
- `memoized_at(key) -> float | None` で時刻を取得できる

これにより:
- 既存テストの `_PAGE_HTML_MEM_CACHE.clear()` / `.update(...)` / `monkeypatch.setitem(...)`
  が**そのまま正しく動く** (テスト側の変更は不要)
- 直接代入も時刻が付くので**穴が塞がる**

その上で:

- `_mem_cache_is_fresh` の「時刻不明なら True」を **False (要再検証)** に変更する。
  上記により時刻不明は起こり得なくなるため、安全側の既定に倒せる。
- `_mem_cache_put` は薄いラッパとして残してよい (可読性のため)。
- `invalidate_cache()` は `_PAGE_HTML_MEM_CACHE.clear()` だけでよくなる
  (`_PAGE_HTML_MEM_CACHE_AT.clear()` の行は不要なら削除。残す場合も壊さないこと)。
- エビクション箇所の `_PAGE_HTML_MEM_CACHE_AT.pop(...)` も不要になれば削除してよい。

---

## 2. 例外ハンドラ内の `json.loads` が再度例外を投げる

`_read_json_cache_stale` (2033 行・2040 行付近):

```python
return json.loads(stale_raw) if stale_raw else None
```

メモ内容が壊れた JSON だと **ハンドラ自身が例外を投げ、関数の外へ漏れる**。
この関数は「失敗しても UI を止めない」ための耐障害経路で、`_read_top_page_snapshot`
など呼び出し元は例外を想定していないため **TOP 画面が 500 になる**。

→ **stale 値の復号を専用のヘルパに切り出し、復号失敗時は `None` を返すこと。**
2033 行・2040 行の両方に適用する。

---

## 3. 再検証時に stale 値を捨ててしまう経路

`row is None` のときは `stale_found` を復帰させているが、
**行はあるが `html` が空 / 鮮度切れ**という `continue` 経路で復帰させていない。

対象:
- `_read_page_html_caches` の 1801-1804 行 (prefetch 経路) と 1822-1825 行 (DB 経路)
- `_read_page_html_cache` の 1872 行 (`return None`)

**具体的な壊れ方**: `_write_page_html_cache` は**メモを先に `now_ts` で更新してから
DB に書く**。DB 書き込みが transient エラーで失敗すると、メモだけ新しく DB は古いままになる。
60 秒後の再検証で DB 行が鮮度切れと判定され、**修正前なら返せていたメモ値を失って
ミス扱いになる**。DB が不調なときにこそ再計算を誘発してしまう。

→ これらの経路でも `stale_found` / `stale_html` があればそれを返すこと。

---

## 4. 当日が空のとき DB を 2 度読む

`_read_top_page_snapshot` (1443-1447 行) は `_read_json_cache_stale` を呼んだあと、
当日かつ `empty` なら同じキーをもう一度 `force_revalidate=True` で読む。

→ **1 リクエストにつき 1 回の読み出しで済む形に整理すること。**
(例: 当日かどうかを先に判定し、`force_revalidate` を最初の 1 回に渡す。
ただし「当日の空スナップショットは必ず DB を再確認する」という**第1版の効果は維持**すること。
`test_today_empty_snapshot_bypasses_fresh_memory` が通り続けること。)

---

## 5. 静的回帰チェックを追加

`tests/test_source_regression.py` は本リポジトリで既知バグを静的に固定する場所。
ここに 1 件追加する:

> **`src/web/app.py` 内で `_PAGE_HTML_MEM_CACHE[` への直接代入が
> `_PageHtmlMemCache` クラス定義の外に存在しないこと。**

将来「速いから」と直接代入が復活しても、テストで即座に落ちるようにする。

---

## 6. テスト要件 (追加分)

`tests/test_page_cache_revalidation.py` に追加すること (既存 8 件は残す):

9. **直接代入でも再検証される** — `_PAGE_HTML_MEM_CACHE[key] = (updated_at, raw)` と
   直接入れた値が、再検証間隔経過後に DB の新しい値へ差し替わること
   (= 指摘 1 の回帰テスト)
10. **`clear()` で時刻も消える** — `_PAGE_HTML_MEM_CACHE.clear()` 後に
    `memoized_at(key)` が `None` になること
11. **`update()` でも時刻が付く** — `.update({key: (0.0, raw)})` した値が
    再検証対象になること
12. **壊れた JSON がメモにあり DB 読みも失敗したとき、例外を投げず `None` を返す**
    (= 指摘 2 の回帰テスト)
13. **DB 行が鮮度切れでもメモ値を返す** — `_read_page_html_cache` で、
    メモは新しいが DB 行が `max_age_sec` 超過のとき、`None` ではなくメモ値が返ること
    (= 指摘 3 の回帰テスト)

---

## 7. 厳守事項

- ❌ **本番 Postgres に接続・書き込みしない**
- ❌ `src/web/app.py` / `tests/test_page_cache_revalidation.py` /
  `tests/test_source_regression.py` 以外を変更しない
- ❌ 第1版の 8 テストを削除・弱体化しない。**特に
  `test_empty_snapshot_recovers_after_database_is_updated` と
  `test_today_empty_snapshot_bypasses_fresh_memory` は本番障害の再現テストなので必ず維持**
- ❌ stale 読みの「DB が死んでいても古い値で画面を出す」性質を壊さない
- ❌ `git commit` / `git push` しない
- ✅ 既存テスト 15 ファイル (`_PAGE_HTML_MEM_CACHE` を直接触るもの) は**変更せずに通す**こと

---

## 8. 完了条件

1. `tests/test_page_cache_revalidation.py` が全て green (8 + 追加5 = 13 件以上)
2. `tests/test_source_regression.py` が green
3. `.venv/Scripts/python.exe -m pytest tests/ -q` で新たな失敗が増えていない
   (既知failure `test_security_policy_allows_supabase_auth_fetch` のみ許容)
4. `git status` に指定 3 ファイル以外の変更が出ていない
