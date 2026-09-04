# 仕様書: ページキャッシュの再検証 (TOP画面が空で固まる障害の恒久対策)

作成: 2026-09-01 / 管理: リン / 実装: CODEX
ブランチ: `fix/page-cache-revalidation`

---

## 1. 直す障害

本番 TOP 画面が「この日のデータはありません。バックフィルされていないか、未来日です。」で
固まり、**web を手動再起動するまで永久に復旧しない**。2026-08-30 と 2026-09-01 に発生。

### 実際に起きたこと (2026-09-01)

| 時刻(JST) | 出来事 |
|---|---|
| 06:22 | `render_top_snapshot_full` がスナップショット生成 → **番組表未取込のため empty=True で焼かれる** |
| 06:31 | 番組表取込が完了 (144 レース) |
| 06:46 | **正しいスナップショット (empty=False / 12 会場 / 144 レース) が DB に書かれる** |
| 08:00 | それでも画面は空のまま。再起動して初めて復旧 |

DB は 06:46 の時点で正常だった。**それでも画面が直らなかったのが本障害の本質。**

### 真因 (`src/web/app.py`)

`_read_json_cache_stale` (1972 行付近) をはじめとする「stale 読み」経路:

```python
mem_entry = _PAGE_HTML_MEM_CACHE.get(cache_key)
if mem_entry:
    return json.loads(mem_entry[1])   # ← 無条件に返す
```

`_PAGE_HTML_MEM_CACHE[key] = (updated_at, html)` と **DB の updated_at を保存しているのに
一度も比較していない**。プロセス内に一度載った値は**永久に返り続け、DB を二度と読み直さない**。

そのため:
- DB を正しく書き直しても**画面には反映されない**
- 回復手段は **web 再起動** か `/admin/cache-clear` (admin 認証必須) の 2 つだけ
- `_read_json_caches_stale` (2010 行付近) は**空振り時に `(0.0, "{}")` という否定エントリ**を
  書き込む (2036 行)。これも同じく永久に残る

### 直し方の方針

**「メモ化してから何秒経ったか」で DB を読み直す (再検証)。**
既存の `max_age_sec` は *データの鮮度* (DB の updated_at からの経過) を見るもので、
*メモ化からの経過* とは別物。今回必要なのは後者。両者を混同しないこと。

`max_age_sec=None`(= stale 読み) の**「古くても返す」という耐障害目的は維持する**。
DB 読み直しに失敗したら、これまで通りメモ上の値を返して画面を落とさない。

---

## 2. 成果物

1. `src/web/app.py` (最小限の外科的変更)
2. `tests/test_page_cache_revalidation.py` (新規)

**それ以外のファイルは変更しないこと。**

---

## 3. 実装内容

### 3-1. 定数を追加

`_PAGE_HTML_MEM_CACHE` の定義 (382 行付近) の近くに置く:

```python
# メモ化してからこの秒数を過ぎたら DB を読み直す。DB 側が新しくなっても
# プロセス内の古い値を返し続ける事故 (2026-08-30 / 2026-09-01 の TOP 画面
# 固着) を防ぐ。読み直しに失敗したときは従来どおりメモ上の値を返す。
_PAGE_HTML_MEM_CACHE_REVALIDATE_SEC = float(
    os.environ.get("BOATRACE_PAGE_CACHE_REVALIDATE_SEC", "60")
)
```

環境変数で調整可能にすること (再デプロイなしで緩められるようにするため)。

### 3-2. メモ化時刻を記録する

現在 `_PAGE_HTML_MEM_CACHE[key] = (updated_at, html)` という代入が**約 10 箇所に散在**して
いる (1771, 1790, 1819, 1833, 1850, 1895, 1906, 1946, 1984, 2027, 2036 行付近)。
**書き込みを 1 箇所に集約するヘルパを作り、全代入をそれ経由に置き換えること。**

```python
_PAGE_HTML_MEM_CACHE_AT: dict[str, float] = {}


def _mem_cache_put(key: str, updated_at: float, html: str) -> None:
    _PAGE_HTML_MEM_CACHE[key] = (float(updated_at or 0), html)
    _PAGE_HTML_MEM_CACHE_AT[key] = time.time()


def _mem_cache_is_fresh(key: str, revalidate_sec: float | None = None) -> bool:
    """メモ化からの経過が再検証間隔以内なら True。"""
```

- 既存のエビクション (1791-1794, 1834-1837 行) で `_PAGE_HTML_MEM_CACHE` から
  pop するときは **`_PAGE_HTML_MEM_CACHE_AT` からも必ず pop する** (取り残すと漏れる)。
- `invalidate_cache()` (1653 行付近) は **両方 clear すること**。

### 3-3. stale 読み経路に再検証を入れる

対象は `_PAGE_HTML_MEM_CACHE.get(...)` を読む全経路 (1754, 1806, 1883, 1972, 2010 行付近)。

**判定順序を厳守:**

1. メモにあり、かつメモ化から `_PAGE_HTML_MEM_CACHE_REVALIDATE_SEC` 以内 → そのまま返す
2. メモにあるが期限切れ → **DB を読み直す**
   - 成功 → 新しい値をメモ化して返す
   - **失敗 (例外・接続不能) → メモ上の古い値を返す** (耐障害性を壊さない)
   - **DB に行が無い → メモ上の古い値を返す** (消えたのではなく読めなかっただけの可能性)
3. メモに無い → 従来どおり DB を読む

> `max_age_sec` が指定されている経路の既存判定は**そのまま残す**こと。
> 再検証はそれと直交する追加条件。

### 3-4. 否定エントリ (空振りキャッシュ) を短命にする

`_read_json_caches_stale` の 2036 行付近

```python
_PAGE_HTML_MEM_CACHE[key] = (0.0, "{}")
```

は「まだ生成されていないキー」を覚え込む。生成後も永久に「無い」と答え続けるため、
**否定エントリは再検証間隔を待たず、短い固定秒数 (10 秒) で必ず失効させること。**
専用の定数 `_PAGE_HTML_MEM_CACHE_NEGATIVE_SEC = 10.0` を置く。

### 3-5. 空の TOP スナップショットは信用しない

`_read_top_page_snapshot` (1418 行付近) に追加:

> **当日 (`target_date == _today_jst_iso()`) のスナップショットが `empty` を truthy で
> 持っている場合は、メモを信用せず必ず DB を読み直す。**

理由: 当日が空なのは「番組表がまだ届いていない」一時状態であることが圧倒的に多く、
遅れて正しい値が書かれる。ここを握り続けたのが今回の障害。対象は 1 キーだけなので
DB 負荷は無視できる。過去日・未来日は対象外 (本当に開催が無い日があるため)。

---

## 4. テスト要件 (`tests/test_page_cache_revalidation.py`)

**この 8 件は「今回の障害を再現し、修正で直ること」を固定するもの。必ず全て入れること。**

1. **再検証間隔内はメモを返す** — DB を読みに行かないこと (DB 読み関数をモックして
   呼ばれないことを確認)
2. **間隔を過ぎたら DB を読み直し、新しい値を返す**
3. **★本障害の再現** — 空スナップショット (`empty=True`) をメモに載せた後、DB 側を
   正しい値 (`empty=False`) に差し替え、間隔経過後の読み出しで **`empty=False` が返る**
   こと。修正前のコードならこのテストは落ちる
4. **★当日の空スナップショットはメモを無視して DB を読む** — 間隔を過ぎていなくても、
   当日かつ `empty=True` なら DB の新しい値が返ること
5. **DB 読み直しが例外で失敗したら、メモ上の古い値を返す** (画面を落とさない)
6. **DB に行が無くなった場合もメモ上の値を返す**
7. **否定エントリ `(0.0, "{}")` が 10 秒で失効する** — 失効後に DB へ問い合わせ、
   生成済みの値を拾えること
8. **`invalidate_cache()` が `_PAGE_HTML_MEM_CACHE` と `_PAGE_HTML_MEM_CACHE_AT` を
   両方 clear する** — 片方だけ残ると次回判定が壊れる

時刻は `time.time` を monkeypatch して進めること (実際に sleep しない)。

---

## 5. 厳守事項

- ❌ **本番 Postgres に接続・書き込みしない**
- ❌ `src/web/app.py` 以外の既存ファイルを変更しない (テストは新規追加のみ)
- ❌ 既存の `max_age_sec` の判定ロジックを削除・変更しない (追加のみ)
- ❌ stale 読みの「DB が死んでいても古い値で画面を出す」性質を壊さない
- ❌ キャッシュ全体を無効化する / メモ化をやめる、といった乱暴な解決にしない
  (DB 接続の逼迫は本プロジェクトの既知の障害要因)
- ❌ `git commit` / `git push` しない (レビュー後にリンが行う)
- ✅ 日本語コメントは既存ファイルと同じ密度・トーンで
- ✅ 変更は最小限・外科的に。app.py は巨大な本番ファイルである

---

## 6. 完了条件

1. `tests/test_page_cache_revalidation.py` が全て green
2. `.venv/Scripts/python.exe -m pytest tests/ -q` で **新たな失敗が増えていない**
   (既知の失敗 `tests/test_supabase_auth_stripe_migration.py::test_security_policy_allows_supabase_auth_fetch`
   は元から落ちている。これ以外が増えていないこと)
3. `git status` に `src/web/app.py` と新規テスト以外の変更が出ていない
4. 変更行数が過大でないこと (目安: app.py の diff は 120 行以内)
