# 勝ち筋サーチ Step 22 仕様書 — 本番アプリへの統合（会員限定機能）

作成: 2026-08-17 リン（Claude Code）/ 発注先: Codex
前提: ローカル版（`src/kachisuji_web/`）が完成。schema v10 / 557,785行。
本番 Web アプリ（`src/web/app.py`、Render稼働）へ**会員限定メニューとして統合**する。

## 調査済みの事実（この仕様の前提）

- 本番アプリ: `src/web/app.py` の `create_app()`（6531行）。ルートは `@app.route` 直書きが中心だが、
  `register_blueprint(start_prediction_bp)` の実績があり **Blueprint 追加が可能**。
- 認証: `src/web/auth.py` に `is_member()` / `is_paid_member()` / `login_required` /
  `member_only_api` が既にある。**これを再利用**する（新しい認証を作らない）。
- 検索DB: `data/kachisuji_search.db` は現在 **2,421MB**。内訳は
  `odds_snapshot` 1,303万行・`start_timing_events` 336万行・`racer_starts` 335万行が大半。
- **検索に実際に必要なのは `asof_race_features`（557,785行）と `racers`（1,643行）のみ**。
  この2テーブルだけを抽出+VACUUM した実測サイズは **566MB**（リンが実測済み）。
  → 配布用スリムDBを作れば Render のディスクに載る。

## 本ステップのスコープ（重要）

**このステップでは「コードの統合」までを行い、push・デプロイ・本番DB配置は行わない。**
デプロイはプロジェクトの承認ゲート（CLAUDE.md）に従い、リッキーさんの承認後にリンが実施する。

## 絶対的な制約（違反禁止）

1. 変更/作成してよいファイル:
   - 新規 `src/web/kachisuji_bp.py`（Blueprint。検索・手法・照合のルート）
   - 新規 `scripts/export_kachisuji_slim_db.py`（配布用スリムDB生成CLI）
   - `src/web/app.py`（**Blueprint 登録の数行のみ**。既存ルート/ロジックの変更は禁止）
   - `src/web/templates/`（勝ち筋サーチ画面のテンプレート追加。既存テンプレートの変更は
     ナビゲーションへのリンク1箇所追加のみ許可）
   - `src/web/static/`（CSS追加）
   - 対応 `tests/`、新規 `docs/kachisuji_production_integration_step22_result_20260817.md`
   - `render.yaml`（**ディスク定義の追記のみ**。既存サービス定義の変更禁止）
2. **`src/search/roi_search.py` と `src/search/strategies.py` は変更禁止**
   （検証済みの計算ロジックに一切触れない。import して再利用する）。
3. **`src/kachisuji_web/` はローカル開発用として残す**（削除しない）。
4. `data/boatrace.db` への書込み禁止。Supabase への接続・スキーマ変更禁止。
5. **push・デプロイ・スケジューラ登録・本番への実データ配置は禁止**。
   実サーバー起動しっぱなし禁止（テストは `app.test_client()` / ポート8090で終了時kill）。
6. コミットは main へのローカルコミット1つ。
   メッセージ: `Integrate kachisuji search into production app as member feature (kachisuji step 22)`。

## 実装内容

### 1. 配布用スリムDB生成CLI（`scripts/export_kachisuji_slim_db.py`）
```
python scripts/export_kachisuji_slim_db.py --out data/kachisuji_slim.db
python scripts/export_kachisuji_slim_db.py --out ... --verify
```
- `data/kachisuji_search.db` から **`asof_race_features` と `racers` のみ**を新DBへコピー
  （テーブルDDL＋インデックスも複製）→ `VACUUM` → サイズ出力。
- 元DBは**読み取り専用**で開く。元DBを変更しない。
- `--verify`: 生成後に行数一致・`PRAGMA quick_check` を実行。
- 想定サイズ 566MB 前後（実測値）。実行結果をレポートに記載。

### 2. Blueprint（`src/web/kachisuji_bp.py`）
ローカル版 `src/kachisuji_web/app.py` のルートを Blueprint 化して移植する。
**ロジックは `src/search/` の既存関数を import して使う（再実装禁止）。**

- `GET /kachisuji` — 検索画面（**会員限定**: `login_required` + 有料会員チェック）
- `POST /kachisuji/api/search` — 検索（`member_only_api`）
- `GET|POST|DELETE /kachisuji/api/strategies` 系 — マイ手法（`member_only_api`）
- `GET /kachisuji/api/strategies/<id>/performance` — 成績（`member_only_api`）
- `GET /kachisuji/api/matches` — 本日照合（`member_only_api`）
- `GET /kachisuji/api/racers` — 選手名検索（`member_only_api`）

**会員判定は既存の仕組みを使う**（`is_paid_member()` 等）。新規の認証実装は禁止。
非会員がアクセスした場合は、既存アプリの流儀に合わせてログイン誘導または403。

### 3. DBパスの環境変数化
- 検索DB: `KACHISUJI_DB`（既定 `data/kachisuji_slim.db`、無ければ `data/kachisuji_search.db`）
- 手法DB: `KACHISUJI_STRATEGY_DB`（既定 `data/kachisuji_strategies.db`）
- **Render のディスクマウント先を想定**し、絶対パスを環境変数で差し替え可能にする。
- **検索DBが存在しない場合**でも本番アプリ全体が落ちないこと。
  `/kachisuji` にアクセスしたときだけ「準備中」を表示し、他機能は正常動作する
  （**これは必須**。DB未配置での起動失敗は本番障害になる）。

### 4. テンプレート
- `src/kachisuji_web/templates/search.html` を本番テンプレートへ移植。
  本番のベーステンプレート（ヘッダー・ナビ）を継承し、デザインを本番に合わせる。
- 既存ナビゲーションに「勝ち筋サーチ」リンクを追加（**会員のみ表示**）。
- 静的ファイルのパスを本番の `url_for('static', ...)` 方式に合わせる。

### 5. render.yaml（追記のみ）
- `boatrace-web` サービスに**ディスク定義を追記**する形の**サンプルをコメントで示す**
  （実際に有効化するかはリッキーさんの判断。課金が発生するため）。
  - 想定: マウント先 `/data`、サイズ 1GB（566MB + 余裕）
- **既存のサービス定義・cron定義は一切変更しない**。

## テスト
1. 非会員が `/kachisuji` にアクセス → ログイン誘導/403（既存アプリの流儀に一致）
2. 会員が `/kachisuji` にアクセス → 200 で画面が出る
3. 各APIが `member_only_api` で保護されている（未認証で401/403）
4. 検索・手法保存・照合・選手検索が Blueprint 経由で動作（合成フィクスチャDB）
5. **検索DBが存在しない環境で、アプリ起動と既存ルート（/healthz, /races 等）が正常**
   （最重要の回帰テスト）
6. スリムDB生成CLIが行数一致・quick_check OK（小さな合成DBで検証）
7. **既存の全テストがグリーン**（本番アプリの回帰。特に既存ルート・認証まわり）

## DoD
1. 全テストグリーン（既存テストへの影響ゼロを明示）。
2. スリムDB生成をサンプル（合成DB）で実行し、動作を確認。
   **実データでの生成はリンが実行**。
3. 結果レポートに: 追加ファイル / 変更した既存ファイルと変更行数 /
   認証の再利用方法 / DB未配置時のフォールバック動作 / render.yaml の提案内容 /
   **デプロイ手順の下書き（実施はしない）** / 既知の制限。
4. ローカルコミット1つ（push しない）。

## 注意
- **本番アプリを壊さないことが最優先**。既存ルートやロジックへの副作用を絶対に作らない。
- 迷ったら「既存に手を入れない」方を選ぶ。
- 検証済みの計算ロジック（`src/search/`）は import するだけ。写経・改変は厳禁。
