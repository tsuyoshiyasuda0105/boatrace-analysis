# 勝ち筋サーチ Step 3 実装結果（2026-08-15）

## 作成ファイル

- `src/kachisuji_web/__init__.py`
- `src/kachisuji_web/app.py`
- `src/kachisuji_web/templates/search.html`
- `src/kachisuji_web/static/kachisuji.css`
- `scripts/run_kachisuji_web.py`
- `tests/test_kachisuji_web.py`
- `docs/kachisuji_web_step3_result_20260815.md`（本レポート）

既存ファイルは変更していない。実サーバー、ブラウザ、ネットワーク、スケジューラ、デプロイ、
本番DB writer は使用していない。Web動作確認は Flask `app.test_client()` と合成SQLite DBだけで行った。
`data/kachisuji_search.db` と `data/boatrace.db` には接続していない。

## テスト・検査結果

- Step 3 対象テスト: `tests/test_kachisuji_web.py` → **10 passed**
- Step 2 + Step 3 結合回帰: `tests/test_roi_search.py tests/test_kachisuji_web.py` → **43 passed**
- リポジトリ全テスト: `tests` → **760 passed**（14.16秒）
- Ruff: 新規Python 4ファイル → **All checks passed**
- Python構文検査: 新規Python 4ファイル → **syntax: ok**
- JavaScript構文検査: テンプレート内scriptを `node --check -` → **ok**

pytest の警告1件は、既存の `.pytest_cache` に nodeids を作成できないWindows環境警告であり、
製品コードやテスト結果への影響はない。最初の対象テストでは、選手4320の該当2行のうち結果欠損1行が
Step 2により除外されることをテスト期待値へ反映できておらず 9 passed / 1 failed となった。
期待母数を2から1へ訂正し、対象・結合・全体テストをすべて再実行して成功を確認した。

## API仕様

### `GET /`

日本語UTF-8の検索画面を返す。24会場、3買い目、天候・風速・潮、番組・市場、1〜6号艇、
開始日・終了日、正規近似CI切替を表示する。

### `POST /api/search`

Step 2 の条件JSONを受け取り、`src.search.roi_search.search_roi()` の結果JSONをそのまま返す。
トップレベルの任意の `fast` 真偽値だけをWeb層で取り出し、正規近似CIの切替に渡す。

- 正常: HTTP 200 + `n`、`hits`、`hit_rate`、`roi`、95%CI、`excluded`、`yearly`、
  `warnings`、`effective_date_range`
- 検証エラー: HTTP 400 + `{"error": "..."}`
- 想定外例外: HTTP 500 + `{"error": "..."}`。スタックトレースはレスポンスへ含めない
- 選手入力: `4320` と `4320 峰竜太` は `racer_id=4320` として扱う。名前だけの入力は
  HTTP 400で「選手番号で指定してください」と案内する

DBパスは `create_app(db_path=...)` または環境変数 `KACHISUJI_DB` で差し替えられ、
未指定時は `data/kachisuji_search.db` を使う。実際のSQLite接続はStep 2が `mode=ro` で行う。

### `GET /healthz`

HTTP 200 + `{"status":"ok"}` を返す。

## デモUIから変更した点と理由

- ダミーの乱数計算、累積収支イメージ、追試、マイ手法保存、本日レース監視を削除し、
  `fetch('/api/search')` による実検索へ置き換えた。これらのダミー機能・永続化・監視はStep 3の範囲外である。
- デモの固定期間プルダウンを `date_from` / `date_to` の日付入力へ変更した。Step 2の実際のAPI契約へ
  そのまま変換でき、「指定なし」はキーごと省略できる。
- 4つ目のKPIをデモの「月あたり出現数」から仕様必須の「除外件数」に変更した。
  `result_missing` と、特に「条件判定不能で除外した件数」`condition_null` を独立した内訳でも常時表示する。
- デモの選手候補リストと「選手名」対応表記を外し、プレースホルダを番号入力の実態に合わせた。
  先頭に番号がある入力のみ確実に解決し、名前の推測マッチングはしない。
- 風向き欄は配置順と見た目を残したまま無効化し、「Step 2検索エンジンでは未対応」と表示する。
  Step 2の許可キーに風向きがなく、未知キーを送ったり風速へ誤変換したりしないためである。
- デモの初期検索条件は設定せず、未指定項目を送らない中立状態にした。配色、2カラム構成、艇色、
  折りたたみ、確定時期・データ期間バッジ、レスポンシブ表示、ダーク配色は参照UIを踏襲した。

## 既知の制限

- 選手名から選手番号を解決できるマスタがないため、名前だけの検索には未対応。
- 風向きはStep 2の条件キーにないため検索条件としては未対応。
- マイ手法保存、当日自動照合、通知、追試専用操作はStep 3の範囲外。
- ローカル専用で認証・課金・デプロイを実装していない。
- 起動用スクリプトは `--port 5060` を既定値として用意したが、本実装・検証では実行していない。
