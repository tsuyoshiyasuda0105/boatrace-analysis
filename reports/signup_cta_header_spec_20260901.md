# 仕様書: ヘッダーに新規登録ボタンを追加する

作成: 2026-09-01 / 管理: リン / 実装: CODEX
ブランチ: `feature/signup-cta-header`

---

## 1. 直す問題

**本番のヘッダーに新規登録への導線が 1 つも無い。**
2026-09-01 に本番 HTML を実測した結果:

- ヘッダーのリンクは「競艇｜バックテストLAB」「メールログイン」「既存ログイン」の 3 つだけ
- トップページ全体を検索しても `signup-supabase` の出現回数は **0 回**

新規登録ページ `/signup-supabase` は存在するのに、**ログイン画面の下部にある小さな文字リンク
「新規登録はこちら」(`src/web/auth.py:450`) からしか辿れない**。
初訪問者はアカウントが無い時点で行き止まりに見え、申込の取りこぼしに直結している。

---

## 2. 成果物

1. `src/web/templates/base.html` (ヘッダーにボタン追加)
2. `src/web/static/style.css` (ボタンの見た目)
3. `tests/test_signup_cta_header.py` (新規)

**それ以外のファイルは変更しないこと。**

---

## 3. 実装内容

### 3-1. ヘッダーに新規登録ボタンを置く

`src/web/templates/base.html` の `<div class="auth-status">` 内、
未ログイン時の分岐 (78-83 行付近、`is_supabase_auth_enabled()` が真の枝) を次の並びにする:

| 順序 | ラベル | リンク先 | 役割 |
|---|---|---|---|
| 1 | **新規登録** | `url_for('signup_supabase')` | **主行動。最も目立たせる** |
| 2 | ログイン | `url_for('login_supabase')` | 副次 |
| 3 | 既存ログイン | `url_for('login')` | 従来どおり控えめ |

- 新規登録ボタンには `account-btn account-btn-signup` を付与する。
- **既存の「メールログイン」はラベルを「ログイン」に変更**する
  (新規登録と並んだとき「メールログイン」は冗長で、どちらが登録か分かりにくいため)。
  リンク先 `login_supabase` は変えない。
- 「既存ログイン」(`account-btn-public-roi`) はラベル・リンクとも**変更しない**。

### 3-2. 表示条件 (重要)

- **`is_supabase_auth_enabled()` が真のときだけ表示する。**
  偽のときは `/signup-supabase` が 404 を返す (`src/web/auth.py:855`) ため、
  リンクを出してはいけない。既存の分岐構造をそのまま使えばよい。
- **ログイン済みの会員には表示しない。** 既存の `{% if is_member() %}` の分岐に従う。
  `cache_neutral_auth` 経路では JS が `.auth-status` の中身を
  バッジ+ログアウトに差し替えるため (base.html 118-126 行付近)、
  **JS 側の変更は不要**。既存ログインボタンと同じ可視性ルールに乗せること。

### 3-3. 見た目 (`src/web/static/style.css`)

`.account-btn-login` (1212 行付近) の定義の近くに `.account-btn-signup` を追加する。

- **サイト既存のゴールド基調に合わせる。新しい色を発明しない。**
  既存のゴールド系ボタン (例: `.nav-btn`, `.l4-badge` 系) で使われている色や
  CSS 変数を再利用すること。
- **塗りつぶし (filled)** にして、線だけのログインボタンより明確に目立たせる。
  「主行動が新規登録である」ことが一目で分かる強弱にする。
- `font-weight` を既存ログインボタンより太くする。
- hover / active の状態を既存ボタンと同じ作法で用意する。

### 3-4. スマートフォン対応

`style.css` 1239-1255 行付近に `.nav-btn, .account-btn` のモバイル調整が既にある。

- **画面幅 375px でヘッダーが崩れない/横スクロールしないこと。**
- 新規登録ボタンのタップ領域は **高さ 44px 以上**を確保する。
- 幅が足りない場合、**削るのは「既存ログイン」から**。新規登録は最後まで残す。

---

## 4. テスト要件 (`tests/test_signup_cta_header.py`)

既存のテストの書き方 (`tests/test_today_races_page.py` 等) に合わせ、Flask test client を使う。

1. **未ログインのトップページに新規登録リンクが出る** — HTML に
   `/signup-supabase` への `<a>` が含まれること
2. **ラベルが「新規登録」であること**
3. **ログイン済み会員には出ない** — 会員セッションでトップページを開くと
   `/signup-supabase` へのリンクが含まれないこと
4. **Supabase 認証が無効なときは出ない** — `is_supabase_auth_enabled()` が偽の場合に
   リンクが含まれないこと (リンク先が 404 になるため)
5. **新規登録が既存ログインより前に現れる** — HTML 上の出現位置で
   `signup-supabase` が `/login"` より先にあること (主行動が先頭である保証)

---

## 5. 厳守事項

- ❌ **本番 Postgres に接続・書き込みしない**
- ❌ 指定 3 ファイル以外を変更しない
- ❌ `/signup-supabase` のルート実装 (`src/web/auth.py`) を変更しない。**今回は導線だけ**
- ❌ 認証・課金・権限まわりのロジックに触れない
- ❌ ログイン済み会員に新規登録ボタンを出さない
- ❌ 新しい配色を発明しない (既存のゴールド基調を再利用)
- ❌ `git commit` / `git push` しない (レビュー後にリンが行う)
- ✅ 日本語コメントは既存ファイルと同じ密度・トーンで

---

## 6. 完了条件

1. `tests/test_signup_cta_header.py` が全て green
2. `.venv/Scripts/python.exe -m pytest tests/ -q` で新たな失敗が増えていない
   (既知failure `test_security_policy_allows_supabase_auth_fetch` のみ許容)
3. `git status` に指定 3 ファイル以外の変更が出ていない
