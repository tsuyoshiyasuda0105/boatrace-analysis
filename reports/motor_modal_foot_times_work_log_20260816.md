# モーター詳細モーダル 展示足タイム表示 作業ログ

作業日: 2026-08-16
対象: `src/web/static/race_detail.js`、`src/web/app.py`、`tests/test_motor_modal_foot_times.py`

## 原因

`/api/race/<race_id>/motor-history/<boat_number>` の `current` には回り足 (`turn_time`) と直線 (`straight_time`) が存在し、一周値もDBから取得されていましたが、フロントのモーダル描画処理はこれらの実測タイムを参照していませんでした。また、一周値は既存互換名 `dash_time` だけで返され、指示書上の `lap_time` キーはありませんでした。

## 修正内容

- `race_detail.js` に「本日の展示足」ブロックを追加し、回り足・直線・一周をラベル付きで2桁表示するようにした。
- `null` / 未取得値はエラーや `NaN` にせず「—」と表示する専用フォーマッタを追加した。
- 履歴テーブル、6艇ポジション、コース統計、選手詳細の既存描画経路は変更せず、モーター履歴パネルの先頭に新ブロックだけを追加した。
- `_motor_history_payload` の `current.lap_time` に、既存 `dash_time` と同じ `race_original_exhibitions.lap_time` の値を併記した。旧 `dash_time` は維持しており、API値の意味・取得元・集計方法は変更していない。
- CSSファイルは変更せず、既存のモーター履歴サマリー用スタイルを再利用した。

## テスト追加

`tests/test_motor_modal_foot_times.py` に以下を追加した。

1. 固定DB行から `current.lap_time` / `turn_time` / `straight_time` がすべて返ること。
2. 桐生相当の `turn_time=7.57`、`straight_time=None`、`lap_time=None` でpayloadが壊れず、Noneを保持すること。
3. JSが「本日の展示足」と3フィールドを描画し、未提供値に「—」を使うこと。

## 桐生1R・ページ本体との一致確認

ローカル `data/boatrace.db` をSQLite read-only URI (`mode=ro`) で照合した。最新の該当実データは `20251228-01-01`（桐生1R・1号艇）で、値は次のとおりだった。

- 回り足: `7.57`
- 直線: `None` → モーダル表示「—」
- 一周: `None` → モーダル表示「—」

ページ本体の展示足付与SQLとモーターAPIのcurrent取得SQLを同じrace/boatで実行し、どちらも `(一周=None, 回り足=7.57, 直線=None)` で一致した。両者は同じ `race_original_exhibitions` の `MIN(lap_time/turn_time/straight_time)` を使用している。

固定API応答を使った有限のheadless Chromium確認でも、モーターボタン押下後に `7.57 / — / —` が表示され、既存の履歴テーブルと当日行が残ることを確認した。ブラウザは終了し、ローカルサーバーやlistenerは起動していない。

## 検証結果

- 関連テスト: `51 passed`
- 指定の全非E2E選択: `942 passed, 1 skipped, 1 warning in 17.26s`
  - 最初のliteral実行は共有Windows TEMPの既知ACL問題により `744 passed / 198 setup errors`。同じ選択条件をプロセス限定のリポジトリ内TEMPで再実行して全件greenを確認し、専用TEMPは削除した。
  - skipは既存のローカルデータ不足時Kachisujiテスト、warningは既存 `.pytest_cache` ACL警告。
- `node --check src/web/static/race_detail.js`: 成功
- `git diff --check`: 成功
- 一時Playwrightハーネスとpytest専用TEMP: 削除済み

## 禁止範囲・回帰確認

- pushなし。
- ROI、予測、DBスキーマ、`render.yaml`、収集ロジック、スケジューラ、データ書き込みは変更・実行していない。
- APIの既存キーと意味は維持し、一周値の同義キー `lap_time` のみ追加した。
- 全非E2Eテストとブラウザ確認により、既存モーダルの履歴表示を含む回帰がないことを確認した。
