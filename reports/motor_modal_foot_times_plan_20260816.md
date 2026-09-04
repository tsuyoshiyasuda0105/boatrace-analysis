# 作業指示書: モーター詳細モーダルに回り足・直線タイムを表示 (Codex CLI 用)

作成: 2026-08-16 / 発注者: リッキー / 検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本のみ)
現行 main: 本番 `3a81c9a`。テスト基準 `--ignore=tests/e2e --ignore=tests/round3_e2e`。

## 背景 (リンが API・DB・JS を照合して切り分け済み)

会員が「モーターを開いたら回り足・直線のタイムが表示されない」と報告。
「モーターを開く」= レース詳細でモーター2連率/円を押して開く**モーター詳細モーダル**
(`race.html` の `#motor-inspector-shell`、描画は `src/web/static/race_detail.js`)。

調査結果:
1. **データもAPIも正常**。API `/api/race/<race_id>/motor-history/<boat_number>`
   (app.py `_motor_history_payload`) の戻り値 `current` ブロックに
   **`turn_time`(回り足)・`straight_time`(直線)・`lap_time`(一周) が入っている**
   (例: 桐生1R1号艇 `current.turn_time = 7.57`)。
2. **バグはフロント (JS)**。`race_detail.js` はこのモーダルで履歴テーブル・
   モーター位置チャート・コース別統計は描画するが、**`current.turn_time` /
   `current.straight_time` / `current.lap_time` (=本日の展示足の実タイム) を
   どこにも表示していない**。
3. レース詳細ページ本体 (`race.html`) の展示足テーブルでは `p.turn_time` を表示済み。
   **モーダルだけが本日の回り足/直線タイムを出していない**。
4. 一部会場 (桐生など) は `straight_time`/`lap_time` を提供せず `turn_time` のみ。
   → その場合 直線/一周は「—」表示で正しい (バグではない)。**None を欠損扱いにしない**。

## ゴール

モーター詳細モーダルに、**そのレース・その艇の本日の展示足タイム
(回り足=turn_time / 直線=straight_time / 一周=lap_time)** を表示する。
データがある艇は数値、無い項目は「—」。

## 絶対ルール

1. **origin/main へ push 禁止** (ローカル main まで)。
2. ROI 戦略・予測・DB スキーマ・render.yaml・収集ロジック・API の戻り値の**意味**は変えない
   (API は既に turn_time 等を返しているので**戻り値の追加は不要**。もし current に
   straight_time/lap_time が未含有なら app.py 側で current に含める最小限の追加は可)。
3. `pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e` を割らない + 新規 green。
4. 作業ログ `reports/motor_modal_foot_times_work_log_20260816.md`。コミット1〜3個。

## やること

### 1. モーダルに「本日の展示足」表示を追加 (`src/web/static/race_detail.js`)

- `data.current` から `turn_time` / `straight_time` / `lap_time` を読み、
  モーダル内の見やすい位置 (例: 現行モーター情報のヘッダ付近、または専用の
  小さな「本日の展示足」ブロック) に **回り足 / 直線 / 一周** のラベル付きで表示。
- 数値フォーマットは既存の `num()` 等の流儀に合わせ、**None/未取得は「—」**。
- 既存のセクション (履歴テーブル・位置チャート・コース統計・選手詳細) を壊さない。
- ラベルは日本語で会員に分かりやすく (回り足 / 直線 / 一周)。展示タイム
  (exhibition_time) も併記できると尚良い (任意)。

### 2. API 側の確認 (必要時のみ最小限)

- `_motor_history_payload` の `current` に `turn_time` はあるが `straight_time` /
  `lap_time` が欠けている場合、`race_original_exhibitions` から**同じ経路で最小限追加**
  (race.html の展示足と同じデータ源。ロジックの意味は変えない)。
- 既に含まれていれば API は無改変。

### 3. 表示の一貫性

- レース詳細ページ本体の展示足 (race.html) とモーダルで、**同じ artist の同じ値**が
  出ること (片方 7.57、片方空、のような不一致を作らない)。

## テスト (`tests/` に追加)

- API `_motor_history_payload` の `current` に turn_time/straight_time/lap_time が
  (データがあるレースで) 含まれることを固定フィクスチャで検証。
- straight_time が None の会場 (桐生相当) で payload が壊れず None を返すこと。
- (可能なら) JS 側は pytest で直接検証しづらいので、**API が正しいフィールドを返す**
  ことをバックエンドテストで担保し、JS の変更は作業ログに手動確認手順を明記。

## 受け入れ条件

- [ ] モーターモーダルに本日の回り足/直線(/一周)タイムが表示される (データがある艇)
- [ ] 未提供の項目は「—」で穏当に (桐生など straight_time 無しでも壊れない)
- [ ] ページ本体とモーダルで同じ値が出る (不一致なし)
- [ ] 既存モーダル機能 (履歴/チャート/コース統計/選手詳細) の回帰なし
- [ ] `pytest ... --ignore=e2e --ignore=round3_e2e` 維持 + 新規 green / push なし / 作業ログ

## 検品 (リンが実施)

「モーダルに本日の回り足/直線タイムが出るか (桐生1R相当で turn_time=7.57 が表示)」
「未提供項目が—で穏当か」「ページ本体と値が一致するか」「既存モーダルの回帰なし」
「API/収集ロジックの意味を変えていないか」「テスト green か」を照合。デプロイは発注者承認後。
