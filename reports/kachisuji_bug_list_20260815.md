# 勝ち筋サーチ Playwright バグ一覧 — Round 1

対象: `src/kachisuji_web/`。Chromium headless で S1〜S7 の49シナリオを実行し、46 passed / 3 xfailed。製品コードの修正は行っていない。

| ID | 深刻度 | 分類 | 再現手順（最小） | 期待 | 実際 | 該当ファイル/行(推定) |
|----|--------|------|------------------|------|------|----------------------|
| BUG-001 | Medium | validation / ux | 3連単の1着と2着を同じ艇番にして検索する。 | 日本語で、着順ごとに異なる艇番が必要だと画面上に案内する。 | 400にはなるが、`bet boats must be distinct integers from 1 through 6` という内部向け英語メッセージがそのまま日本語UIに表示される。 | `src/search/roi_search.py:151`, `src/kachisuji_web/templates/search.html:476` |
| BUG-002 | Medium | validation / ux | 艇間比較を追加し、比較元と比較先を同じ艇（例: 1号艇 vs 1号艇）にして検索する。 | 日本語で「同じ艇同士は比較できない」と明示する。 | 400にはなるが、`compare.0.boat and other must differ` という内部フィールド名を含む英語メッセージが表示される。 | `src/search/roi_search.py:363`, `src/kachisuji_web/templates/search.html:476` |
| BUG-003 | Medium | validation / correctness | `POST /api/strategies` に、有効な name/conditions と `backtest: "not-an-object"` を送る。 | backtest は回収率・件数等を持つJSONオブジェクトに限定し、型違反は400にする。 | HTTP 200で保存され、`GET /api/strategies` に不正な型の backtest が永続化される。 | `src/kachisuji_web/app.py:94-100`, `src/search/strategies.py:119-123` |

## バグではないが改善余地

- 空の手法名、日付逆転、各数値境界の400本文も英語中心である。機能上は拒否できているが、日本語UIとしてエラー文言を統一すると理解しやすい。
- Chromium は意図した400応答も `Failed to load resource` として console error に記録する。テストでは期待済み400だけを除外し、JavaScript例外と500系resource errorは引き続き失敗扱いにしている。

## 未検出・確認済み

- XSS文字列は一覧でテキストとしてエスケープされ、`script`/`img` 要素の生成やイベント実行はなかった。
- 390px幅で document の横方向オーバーフローはなかった。
- 検索ボタンの同一tick二重クリックは1リクエストに抑止された。
- confirmed と pending の両方を実データ日付でAPI照合できた。
- 未知キー、型違い数値、巨大JSON、壊れた日付、空/配列/欠損strategy payloadは500にならず400へ分類された（BUG-003のbacktest型を除く）。
