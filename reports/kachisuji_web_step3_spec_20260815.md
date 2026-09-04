# 勝ち筋サーチ Step 3 実装仕様書 — 検索 Web UI

作成: 2026-08-15 リン（Claude Code）/ 発注先: Codex
前提:
- Step 1（f2ee23a, 0bb3fcc）: `data/kachisuji_search.db` の `asof_race_features`（1レース=1行）
- Step 2（e024a19）: `src/search/roi_search.py` の検索エンジン（条件JSON→ROI結果JSON）

## 目的

Step 2 のエンジンをブラウザから使えるようにする。**ローカル専用の単独 Flask アプリ**として作る。
既存の本番 Web アプリ（`src/web/app.py`）には一切手を加えない。

## 絶対的な制約（違反禁止）

1. **新規ファイルのみ作成。既存ファイルの変更は一切禁止**（`src/web/` 配下の既存ファイルを含む）。
2. DB は**読み取り専用**。`data/boatrace.db` には接続しない。
3. **サーバーを起動したまま放置しない**。動作確認は Flask test_client（`app.test_client()`）で行い、
   実サーバーの起動やブラウザ操作は行わないこと。
4. ネットワーク・スケジューラ・デプロイ・push 禁止。認証や課金の実装は範囲外。
5. コミットは main へのローカルコミット1つ。メッセージ: `Add kachisuji search web UI (step 3)`。

## 作成するファイル

- `src/kachisuji_web/__init__.py`（空でよい）
- `src/kachisuji_web/app.py` — Flask アプリ（`create_app()` ファクトリを持つこと）
- `src/kachisuji_web/templates/search.html` — 画面
- `src/kachisuji_web/static/kachisuji.css` — スタイル
- `scripts/run_kachisuji_web.py` — 起動スクリプト（`--port 5060` 既定。**Codex は実行しない**）
- `tests/test_kachisuji_web.py` — テスト
- `docs/kachisuji_web_step3_result_20260815.md` — 結果レポート

## 画面仕様

デモUI（HTML）が `reports/kachisuji_ui_reference.html` に置いてある。**これを実装の見た目・
条件項目の基準とすること**（デザイン・配色・条件の並び順・バッジをできる限り再現する）。
デモの JavaScript はダミー計算なので、そこだけ実 API 呼び出しに置き換える。

### 条件フォーム（デモUIと同一）
- レース条件: 会場（全会場+24場）/ 買い目（単勝・2連単・3連単 × 1〜3着の艇番）/ 天候（晴曇雨の複数選択）/ 風向き / 風の強さ / 潮
- 番組・市場条件: 性別構成 / 級別構成 / 開催日程 / 時間帯
- 艇別条件（1〜6号艇、折りたたみ。公式HP順）: 級別（A1/A2/B1/B2 複数選択）/ 選手（番号または名前）/ 平均ST / 全国勝率 / 当地勝率 / モーター2連対率 / 展示順位 / 展示タイム会場平均比 / 展示ST / 決まり手＋勝率 / 事故率
- 期間指定（date_from / date_to）
- データ期間バッジ: 決まり手・事故率は「📅 2023/5〜」（オレンジ）、展示系は「📅 2024/12〜」（赤）

### 結果表示
Step 2 の返却JSONをそのまま可視化する:
- KPI: 回収率（95%CI併記）/ 的中率（的中数/N）/ 合致レース数 N / 除外件数
- 警告: warnings をそのまま表示（n<30 は赤、n<100 は黄）
- 年別内訳テーブル（yearly）
- 除外の内訳（result_missing / condition_null）を明示。**「条件判定不能で除外した件数」を必ず見せる**

## API 仕様

- `GET /` — 検索画面（テンプレート描画）
- `POST /api/search` — リクエストボディ = Step 2 の条件JSON。レスポンス = Step 2 の結果JSON。
  - `fast` パラメータ（真偽）で正規近似CIに切替可能
  - エンジンが送出する検証エラー（未知キー等）は HTTP 400 + `{"error": "..."}` で返す
  - 想定外の例外は HTTP 500 + エラーメッセージ（スタックトレースは返さない）
- `GET /healthz` — `{"status":"ok"}`

### 選手入力の扱い
「4320 峰竜太」「4320」「峰竜太」のいずれの入力でも動くようにする:
- 先頭が数字なら選手番号として解釈し、条件の `racer_id` に渡す
- 数字でなければ名前として扱う。**名前→選手番号の解決が現時点でできない場合は、
  HTTP 400 で「選手番号で指定してください」と返す**（推測でのマッチングは禁止）。
  解決可能なマスタが `data/kachisuji_search.db` 内にない以上、今回は番号のみ対応でよい。
  画面のプレースホルダも実態に合わせること。

## テスト仕様（`tests/test_kachisuji_web.py`）

Flask test_client と合成フィクスチャDBで:
1. `GET /` が 200 で、主要な条件フィールドが HTML に含まれる
2. `POST /api/search` が正しい条件で 200 + 期待JSON構造
3. 未知キーを含む条件で 400
4. 買い目3種それぞれで正しく検索できる
5. 艇別条件（級別・モーター・展示）が API 経由で効く
6. 選手を名前だけで指定したとき 400 とメッセージ
7. `GET /healthz` が 200

## 完了条件（DoD）

1. テスト全件グリーン
2. `docs/kachisuji_web_step3_result_20260815.md` に: 作成ファイル / テスト結果 / API仕様 /
   デモUIから変更した点とその理由 / 既知の制限（選手名未対応など）
3. ローカルコミット1つ（push しない）

## 実装上の注意

- 条件フォーム → 条件JSON の変換は**クライアント側 JavaScript**で行い、
  「指定なし」の項目はキー自体を送らない（Step 2 は省略キー=指定なし）。
- Step 2 のエンジンは `from src.search.roi_search import ...` で再利用する。**ロジックを再実装しない**。
- DB パスは環境変数 `KACHISUJI_DB`（既定 `data/kachisuji_search.db`）で差し替え可能にする（テスト用）。
- 画面は日本語。UTF-8 を明示。
