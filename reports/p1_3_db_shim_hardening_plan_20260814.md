# 作業指示書: P1-3 二重DB変換層 (SQLite/Postgres shim) の防御

作成: 2026-08-14 / 発注者: リッキー / 単体で完結する指示書。
リポジトリ: `C:\boat_project\boatrace-analysis` (正本。他の場所に checkout を作らない)
背景: 監査 [reports/codebase_audit_20260813.md] の P1-3。`src/db/connection.py` の
SQLite→Postgres 変換層 (shim) が特定条件で「本番だけ静かに間違う」。無音のデータ
欠損 (昨日の結果欠損の温床) を止める。全 94 ファイルがこの shim を経由するため
最重要級。

## 絶対に守るルール

1. **origin/main への push 禁止** (push=本番デプロイ)。コミットはローカル main まで。
2. ROI 戦略ロジック・予測ロジックの変更禁止。DB スキーマ (schema.sql) の**構造変更**は
   禁止。ただし `_TABLE_PRIMARY_KEYS` の辞書追加は本タスクの主目的なので可。
3. 作業ログを `reports/p1_3_work_log_20260814.md` に記録。
4. テスト: `.venv/Scripts/python.exe -m pytest tests/ -q`。**既存の失敗16件から
   増やさない**こと (新規テストは全 green)。ベースライン確認方法は末尾参照。
5. **触ってよいファイル (これ以外は編集禁止)**:
   - `src/db/connection.py`
   - `tests/` 配下の新規ファイル (例 `test_db_shim_rewriter.py`,
     `test_db_pk_map_parity.py`)
   - `reports/p1_3_work_log_20260814.md`
   並行して別作業が `src/web/app.py` `src/collectors/*` `scripts/*` を編集中のため、
   **それらには一切触れないこと**。upsert 呼び出し側 (openapi.py 等) の修正が必要と
   判断したら、指示書に追記提案として作業ログに書き、実装はしない (別便で扱う)。

## 現状の欠陥 (2026-08-14 に行番号まで検証済み)

### 欠陥B (最重要): 未登録テーブルが「上書き」→「何もしない」に化ける
`src/db/connection.py:135-137` `_build_upsert`: `_TABLE_PRIMARY_KEYS` (104-129) に無い
テーブルは `ON CONFLICT DO NOTHING` を返す。以下は `INSERT OR REPLACE` で書かれるのに
マップに無く、**Postgres では2回目以降の書き込みが無音で捨てられる**:

- `l4_daily_summary`  (戦略集計。UI が読む可能性)
- `course1_stats_cache`
- `decay_factor`
- `paper_trades`
- `alert_sent`
- `racer_entry_change_snapshots`

(`t` はマッチノイズなので無視してよい)

### 欠陥A: 末尾行コメントで ON CONFLICT 句がコメント化
`:173` は `rstrip()` するが、行コメント `-- ...` を除去しない。SQL 末尾に `--` コメントが
あると、追記した `ON CONFLICT ...` がコメント行に飲まれて無効化 → `UniqueViolation`。

### 欠陥C: `racer_accident_period_stats` の PK 不一致リスク
`:124` のマップは `period_end` を含むが、稼働中の SQLite 実 PK は含まない可能性
(監査記載)。ON CONFLICT 指定列が実制約と食い違うと Postgres で
"no unique or exclusion constraint matching" のハード失敗。

### 欠陥D: `_placeholder_pg` が `%` を素通し
`:82-100`。psycopg3 はパラメータ束縛時にリテラル `%` を `%%` にする必要がある。
`LIKE '%x%'` + params で `unsupported format character`。現状の顕在化は限定的だが地雷。

## タスク

### タスク1 (最優先): PKマップの欠落テーブルを追加 + パリティテスト
1. 上記6テーブルの正しい主キーを `schema.sql` (無い場合は稼働 SQLite の
   `PRAGMA table_info` / 各テーブルの用途) から確認し、`_TABLE_PRIMARY_KEYS` に追加。
   - 例の当たり: `l4_daily_summary` は `date`、`course1_stats_cache` は要確認、
     `paper_trades` は `id`、`decay_factor` は `bucket`、
     `alert_sent`/`racer_entry_change_snapshots` は実テーブル定義を確認。
   - **確認できないテーブルは推測で入れない**。作業ログに「要確認」と記録し保留。
2. **パリティテスト** (`test_db_pk_map_parity.py`): リポジトリ内の全
   `INSERT OR REPLACE/IGNORE INTO <t>` を grep 相当で列挙し、各 `<t>` が
   `_TABLE_PRIMARY_KEYS` に存在することを assert。以後の欠落を自動検出する。
   (grep はテスト内で `subprocess` か Python の re でソース走査)

### タスク2: 未登録テーブルは DO NOTHING でなく明示エラーに
`_build_upsert` のフォールバックを、`INSERT OR REPLACE` (上書き意図) のとき
未登録テーブルなら **例外を投げる** (or 明確な warning + 呼び出し側に伝わる形) に変更。
「上書きのつもりが無音で捨てられる」を「気づける失敗」に変える。
※ `INSERT OR IGNORE` (重複無視が意図) は従来どおり DO NOTHING で良い。
kind (REPLACE/IGNORE) は `_rewrite_sqlite_specific` が持っているので引数で渡す。

### タスク3: 末尾行コメントの除去 (欠陥A)
`_rewrite_sqlite_specific` で ON CONFLICT を追記する前に、末尾の行コメント
(`-- ...` 改行まで) を除去してから tail を付ける。複数行 SQL の途中コメントは壊さない
こと。回帰テスト: 末尾 `-- comment` 付き INSERT OR REPLACE が正しく
`... ON CONFLICT ... ` になる。

### タスク4: `racer_accident_period_stats` の PK 整合確認 (欠陥C)
稼働 SQLite (`data/boatrace.db`, 読み取り専用) の実 PK を `PRAGMA table_info` /
`PRAGMA index_list` で確認し、マップ (`:124`) と一致するか検証。
- 不一致なら: どちらが正か作業ログに明記し、**マップ側を実態に合わせる**
  (schema.sql の構造は変えない)。判断に迷えば保留して報告。
- 一致なら: パリティテストに「マップの PK が schema.sql の PRIMARY KEY と一致」する
  チェックを追加できるか検討 (可能な範囲で)。

### タスク5 (任意・低risk): `%` エスケープ (欠陥D)
`_placeholder_pg` で、パラメータを使う文のリテラル `%` を `%%` に変換。
ただし誤爆 (既に %s にした部分を二重変換) を避ける実装にし、単体テストを付ける。
自信が持てなければ**保留**して作業ログに記録 (現状の顕在化は限定的)。

## 受け入れ条件

- [ ] 6テーブル (確認できたもの) が `_TABLE_PRIMARY_KEYS` に追加され、DO NOTHING に
      化けない
- [ ] パリティテストが「全 IOR 対象テーブルがマップにある」ことを保証
- [ ] 未登録テーブルへの REPLACE は無音でなく失敗する
- [ ] 末尾行コメント付き SQL で ON CONFLICT が有効
- [ ] `racer_accident_period_stats` の PK 整合を確認・記録 (必要なら修正)
- [ ] pytest: 既存16失敗から増えない。新規テスト全 green
- [ ] push していない。作業ログに変更一覧・テスト結果・保留項目・「デプロイ待ち」明記

## ベースライン確認方法 (テスト増減の判定用)

現在の main (コミット済み) の失敗は 16 件。変更後に
`pytest tests/ -q` の failed 件数が 16 を超えたら、超過分が本タスク起因かを
`git stash` で切り分けて確認すること。

## やらないこと (スコープ外)

- upsert 呼び出し側 (openapi.py の upsert_results 等) の COALESCE 化 (別便)
- schema.sql の構造変更 / マイグレーション作成
- 別作業が編集中の app.py / collectors / scripts への変更
