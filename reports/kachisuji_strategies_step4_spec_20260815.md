# 勝ち筋サーチ Step 4 実装仕様書 — マイ手法の保存とレース照合

作成: 2026-08-15 リン（Claude Code）/ 発注先: Codex
前提:
- Step 1（f2ee23a, 0bb3fcc）: `data/kachisuji_search.db` の `asof_race_features`
- Step 2（e024a19）: `src/search/roi_search.py` 検索エンジン
- Step 3（7cdeb00）: `src/kachisuji_web/` 検索 Web UI

## 目的

見つけた検索条件を「マイ手法」として保存し、**指定日のレースの中から合致するものを抽出**する。
本商品の継続利用の核となる機能。

## 絶対的な制約（違反禁止）

1. **既存ファイルの変更は原則禁止**。例外として以下2ファイルのみ変更を許可する:
   - `src/kachisuji_web/app.py`（ルート追加）
   - `src/kachisuji_web/templates/search.html`（保存UI・手法一覧の追加）
   他の既存ファイル（`src/search/roi_search.py`、`src/features/asof_builder.py`、
   本番 `src/web/` 配下、テスト以外）は**変更禁止**。
2. 手法データは**新しい DB ファイル** `data/kachisuji_strategies.db` に保存する。
   `asof_race_features` を持つ `data/kachisuji_search.db` は**読み取り専用**のまま。
   `data/boatrace.db` には接続しない。
3. **サーバーを起動したまま放置しない**。動作確認は `app.test_client()` のみ。
   ポート5060で開発サーバーが起動中の可能性があるため、実サーバー起動は禁止。
4. ネットワーク・スケジューラ登録・デプロイ・push 禁止。認証・課金は範囲外
   （将来のユーザー単位化に備え、テーブルに `owner` TEXT 列だけ用意し既定値 `'local'` とする）。
5. コミットは main へのローカルコミット1つ。メッセージ: `Add strategy save and race matching (kachisuji step 4)`。

## 作成するファイル

- `src/search/strategies.py` — 手法の保存/取得/削除、照合ロジック
- `scripts/match_strategies.py` — CLI（指定日の全手法照合。将来の夜間バッチ用エントリポイント）
- `tests/test_strategies.py` — テスト
- `docs/kachisuji_strategies_step4_result_20260815.md` — 結果レポート
（`src/kachisuji_web/app.py` と `search.html` は上記例外として変更可）

## データモデル

DB: `data/kachisuji_strategies.db`

```sql
CREATE TABLE IF NOT EXISTS strategies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL DEFAULT 'local',
  name TEXT NOT NULL,
  conditions_json TEXT NOT NULL,   -- Step 2 の条件JSONそのまま
  created_at TEXT NOT NULL,        -- ISO8601。フォワード検証の基準日
  backtest_json TEXT,              -- 保存時点の検索結果JSON（探索時の成績スナップショット）
  is_active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_strategies_owner_active ON strategies(owner, is_active);
```

- `created_at` は**フォワード成績の起点**。以後この日付以降のレースだけで別集計する。

## 機能仕様

### A. 手法の保存・一覧・削除（`src/search/strategies.py`）

- `save_strategy(name, conditions, backtest=None, owner='local') -> int`
  - 同名は許容（連番IDで区別）。`name` 空文字はエラー
  - `conditions` は Step 2 のバリデータを通してから保存する（不正な条件を保存させない）
- `list_strategies(owner='local', include_inactive=False) -> list[dict]`
- `get_strategy(id) -> dict | None`
- `deactivate_strategy(id) -> bool`（物理削除はしない）

### B. レース照合（本機能の核）

`match_races(strategy_id_or_conditions, target_date, search_db, strategies_db) -> dict`

指定日 `target_date` の `asof_race_features` の行に対し、**Step 2 と完全に同じ条件解釈**で
合致するレースを返す。ROI 計算はせず、**どのレースが合致したか**を返す。

- 返却:
```json
{
  "strategy_id": 3, "strategy_name": "鳴門・3連単1-2-3",
  "target_date": "2026-08-16",
  "matched": [
    {"race_id": "...", "jcd": 12, "race_no": 7, "bet": "3連単 1-2-3", "status": "confirmed"}
  ],
  "pending": [
    {"race_id": "...", "jcd": 12, "race_no": 10, "bet": "3連単 1-2-3",
     "status": "pending", "undetermined_columns": ["b1_ex_rank"]}
  ],
  "counts": {"races_on_date": 144, "matched": 2, "pending": 1}
}
```
- **`status` の区別が重要**:
  - `confirmed` — 条件が参照する全列に値があり、すべて合致
  - `pending` — **📋前日確定の条件はすべて合致しているが、⏱当日確定の列がまだ NULL**
    （天候・風・展示系）。「朝候補」として表示する対象。`undetermined_columns` に列名を入れる
  - 前日確定の条件で外れた行は返さない
- この判定ロジックは Step 2 の条件パーサを**再利用**すること（条件解釈の二重実装は禁止）。
  Step 2 側を変更してはならないため、必要なら `src/search/strategies.py` 内で
  `roi_search` の公開関数/クラスを import して使う。それが不可能な構造の場合は
  結果レポートに理由を書き、最小限の重複に留めた上でその旨を明記すること。

### C. Web API（`src/kachisuji_web/app.py` に追加）

- `POST /api/strategies` — `{"name": "...", "conditions": {...}, "backtest": {...}}` → 保存、`{"id": n}`
- `GET /api/strategies` — 一覧
- `DELETE /api/strategies/<id>` — 無効化
- `GET /api/strategies/<id>/matches?date=YYYY-MM-DD` — 照合結果（B の JSON）
- `GET /api/matches?date=YYYY-MM-DD` — **全有効手法**をまとめて照合（本日一覧用）
- 既存の `/`、`/api/search`、`/healthz` の挙動は変えない

### D. 画面（`search.html` に追加）

- 検索結果の下に「★ この条件をマイ手法として保存」ボタン（手法名を入力するプロンプト）
- 「マイ手法」一覧セクション: 名前 / 保存日 / 保存時の回収率とN / 削除ボタン
- 「レース照合」セクション: 日付を選んで実行 → 手法ごとに合致レースを表示。
  `confirmed` は緑、`pending` は「🌅 未確定項目あり（○○）」と黄色系で区別表示
- デモUI（`reports/kachisuji_ui_reference.html`）の見た目・配色に合わせる

### E. CLI（`scripts/match_strategies.py`）

```
python scripts/match_strategies.py --date 2026-08-16            # 全有効手法を照合しJSON出力
python scripts/match_strategies.py --date 2026-08-16 --id 3
```
将来この CLI を夜間バッチから呼ぶ想定。**今回スケジューラ登録は行わない**。

## テスト仕様（`tests/test_strategies.py`）

合成フィクスチャDB（search 用と strategies 用の2つ）で:
1. 保存 → 一覧 → 取得 → 無効化のライフサイクル
2. 不正な条件JSON（未知キー）は保存できずエラー
3. 照合: 前日確定条件のみの手法 → 合致レースが `confirmed` に入る
4. 照合: 展示条件を含む手法で展示列が NULL の行 → `pending` に入り `undetermined_columns` に列名
5. 照合: 前日確定条件で外れる行は matched にも pending にも入らない
6. `GET /api/matches` が複数手法をまとめて返す
7. 既存の `/api/search` が引き続き動作する（回帰）
8. 対象日にレースがない場合 counts が 0 で例外にならない

実行: `.venv/Scripts/python.exe -m pytest tests/test_strategies.py tests/test_kachisuji_web.py -q`

## 完了条件（DoD）

1. 新規テストおよび既存の `tests/test_kachisuji_web.py` `tests/test_roi_search.py` が全件グリーン
2. `docs/kachisuji_strategies_step4_result_20260815.md` に: 作成/変更ファイル / テスト結果 /
   条件パーサ再利用の方法 / pending 判定の実装方法 / 既知の制限
3. ローカルコミット1つ（push しない）

## 実装上の注意

- DB パスは環境変数で差し替え可能に（`KACHISUJI_DB` / `KACHISUJI_STRATEGY_DB`）。テストで必須。
- 手法名・条件はユーザー入力。HTML エスケープと SQL パラメータバインドを徹底する。
- `pending` の判定には「その条件が参照する列」を知る必要がある。Step 2 の条件パーサが
  参照列を列挙できない場合、条件キー→列名の対応表を `strategies.py` に持ってよいが、
  **Step 2 の解釈と食い違わないようテストで担保**すること。
