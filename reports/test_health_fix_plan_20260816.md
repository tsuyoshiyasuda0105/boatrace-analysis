# 作業指示書: 失敗テスト2件の修正 (テスト健全性回復) (Codex CLI 用)

作成: 2026-08-16 / 発注者: リッキー / 検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本のみ)
現行 main: 本番 `e93808d` 稼働。テストは Playwright 未導入で e2e が error になるため、
基準は `pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e` で数える
(現状: **893 passed / 2 failed**)。この **2 failed を解消**するのが本タスク。

## 背景

長らく全 green を維持してきたが、現在2件が fail している。どちらも**本番コードの
回帰ではなく、テスト側の弱点**であることを調査済み:

### fail 1: `tests/test_db_pk_map_parity.py::test_all_static_insert_or_targets_have_primary_key_map_entries`
- 原因: 未追跡ファイル `src/features/odds_sync.py` が `INSERT OR IGNORE INTO odds_snapshot`
  を持つ。テストは「INSERT OR の対象テーブルは全て `_TABLE_PRIMARY_KEYS` に登録必須」と
  みなすため未登録の `odds_snapshot` を検知して落ちる。
- **重要な事実**: `odds_sync.py` は **生 `sqlite3`** (`sqlite3.connect(output)`) を使う
  **ローカル SQLite 専用ツール** (kachisuji/as-of 特徴量ビルダー `scripts/build_asof_features.py`
  からのみ import)。**本番の Postgres シム `src.db.connection.connect` を通らない**ので、
  ON CONFLICT 変換の対象外 = **PK マップ登録は本来不要**。
- つまりこのテストの検知範囲が広すぎる (シムを通らない生 sqlite3 の INSERT OR まで拾う)。

### fail 2: `tests/test_graceful_db_degradation.py::test_race_detail_transient_error_uses_stale_then_preparing`
- 原因: テストが `/race/20260815-01-01` を「今日のレース」前提で書かれている。日付が
  8/16 に変わり **8/15 が過去日扱い**になったため、`use_fresh_page_cache` の分岐が変わり
  期待する `X-Boatrace-Data-Stale` ヘッダ経路に入らなくなった。**日付固定のテストの弱さ**。

## 絶対ルール

1. **origin/main へ push 禁止** (ローカル main まで)。
2. **本番のプロダクトコードは変更しない** (これはテスト健全化タスク)。
   `src/web/app.py` 等のロジックは触らない。修正対象は原則 `tests/` 配下。
   ただし fail 1 で「テストの判定基準を正す」以外に、`odds_snapshot` を
   **意図的に PK マップへ登録する方が正しい**と判断した場合のみ `src/db/connection.py`
   への追記可 (下記参照)。
3. ROI 戦略・予測・DB スキーマ・render.yaml は変更しない。
4. テスト: `pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e` で
   **2 failed → 0 failed**、既存 passed を減らさない。
5. **PK マップの番人テストを弱体化させない**: 本番シム経由の INSERT OR (Postgres で
   ON CONFLICT になるもの) の欠落は**引き続き検知できる**こと。これは過去に本番障害を
   捕捉した重要なガードなので、抜け穴を作らない。
6. 作業ログ `reports/test_health_fix_work_log_20260816.md`。コミット1〜2個。

## やること

### fail 1 (PK マップ): 生 sqlite3 のローカル専用 INSERT OR を検知対象から外す

推奨アプローチ (いずれか、より安全・正確な方を選ぶ):
- **(A) テストの検知範囲を正す**: `INSERT OR` 対象でも、その**ファイルが本番シム
  `src.db.connection` を使わず生 `sqlite3` に書いている**ものは PK マップ不要なので
  除外する。判定は「同ファイルが `src.db.connection` を import しているか」等の
  明示的・保守的な条件で。**本番シムを使うファイルの INSERT OR は引き続き必須チェック**。
  除外理由をテスト内コメントに明記。
- **(B) 明示除外**: `odds_sync.py` のような生 sqlite3 オフラインツールを
  `EXCLUDED_*` 的な明示リストに追加 (なぜ除外して良いかコメント必須)。
- **(C) 登録**: どうしても曖昧なら `odds_snapshot` を実 PK と共に `_TABLE_PRIMARY_KEYS`
  に登録してしまう手もあるが、**非本番テーブルを本番シムのマップに混ぜるのは誤解を招く**
  ので (A)/(B) を優先。採用可否は作業ログに理由を書く。

**どのアプローチでも、シム経由の未登録テーブルは今後も検知できることをテストで担保**する
(例: シムを使う架空の INSERT OR を混ぜたら落ちる、というメタテストがあると尚良い)。

### fail 2 (日付依存): 「今日」を固定日付でなく動的にする

- `test_race_detail_transient_error_uses_stale_then_preparing` (と、同種の日付固定が
  他にもあれば) を、**実行日に依存しない**形に直す。
  - 例: `_today_jst_iso` を monkeypatch して固定の「今日」を注入する / または
    テスト内で「今日の日付」を動的に計算して race_id を組み立てる。
- 意図 (今日のレースで transient error → stale → preparing の順に落ちる) は変えない。
  **アサーションの趣旨は維持**し、日付の当たり外れで壊れないようにするだけ。
- 同ファイル内に日付を固定前提にした他テストがないか確認し、あれば同様に堅牢化。

## 受け入れ条件

- [ ] `pytest ... --ignore=e2e --ignore=round3_e2e` が **0 failed** (2件解消)
- [ ] PK マップの番人が、本番シム経由の未登録テーブルを今後も検知できる (弱体化なし)
- [ ] 日付が変わってもテストが壊れない (動的日付/モック)
- [ ] 本番プロダクトロジックは無改変 (fail1 で connection.py に登録した場合を除き tests/ のみ)
- [ ] push していない / 作業ログ提出

## 検品 (リンが実施)

「2件が本当に解消したか」「PK マップの番人が弱くなっていないか (抜け穴がないか)」
「日付依存が解消したか (別日でも通るか)」「本番ロジック無改変か」を照合。
デプロイは発注者承認後 (ただしテストのみなら本番挙動に影響なし)。
