# 勝ち筋サーチ Step 8 実装仕様書 — オッズ条件をT-5のみに（確定オッズ選択肢の除去）

作成: 2026-08-16 リン（Claude Code）/ 発注先: Codex
前提: Step 7（6cc92ef）でオッズ条件（final/T-5min選択式）を実装済み。
ユーザー決定: **オッズ条件は T-5min（5分前オッズ）のみとし、final（確定オッズ）は選べないようにする。**

## 背景（この変更の理由）

確定オッズ（払戻）は「実際に来た組番」の分しか存在しないため、確定オッズで買い目を絞ると
「結果が当該組番だったレースのみ」が残り、的中率100%・回収率が異常膨張する未来情報バイアスになる。
T-5min（締切5分前オッズ）は外れる組番にもオッズが付くため、正しく事前絞り込みができる。
よってオッズ条件は T-5min のみに限定する。

## 絶対的な制約（違反禁止）

1. 変更してよいファイル: `src/search/roi_search.py`, `src/search/strategies.py`,
   `src/kachisuji_web/app.py`, `templates/search.html`, `static/kachisuji.css`,
   対応 `tests/`, 新規 `docs/kachisuji_odds_t5only_step8_result_20260816.md`。
   他の既存プロダクトファイルは変更禁止。asof_builder / odds_sync のスキーマは変えない。
2. `data/boatrace.db` 接続禁止。`data/kachisuji_search.db` は読み取りのみ。
3. 実サーバー起動しっぱなし禁止（test_client / E2E はポート8090で終了時kill）。
4. ネットワーク・スケジューラ・デプロイ・push 禁止。
5. コミットは main へのローカルコミット1つ。
   メッセージ: `Restrict odds condition to T-5min snapshot (kachisuji step 8)`。

## 変更内容

### 1. 検索エンジン（roi_search.py）
- オッズ条件 `odds` の `snapshot` は **'T-5min' のみ許可**。
  - `snapshot` 省略時の既定を 'T-5min' にする。
  - `snapshot` に 'final' もしくは 'T-5min' 以外が指定されたら**日本語エラー**
    （例:「オッズ条件は5分前オッズ(T-5min)のみ対応しています」）。HTTP 400。
- その他のオッズ判定ロジック（min/max、オッズ行なし→condition_null 除外、3連単のみ）は現状維持。

### 2. 照合（strategies.py）
- オッズ条件は引き続き当日確定（⏱）扱いで pending 分類。final 前提の記述があれば T-5min に統一。

### 3. 画面（search.html / css）
- オッズ条件セクションから **snapshot 選択（final/T-5min の切替）UI を削除**し、T-5min 固定にする。
  - ラベルは「オッズ（5分前・T-5）」等。バッジは ⏱ と 📅 2024/6〜（実測に合わせる）。
  - 確定オッズに関する注意書きは、T-5min 前提の説明に差し替える
    （例:「5分前オッズで絞り込みます。締切前に分かる値なので当日照合でも使えます。データは2024年6月以降」）。
- 既存の保存済み手法に snapshot='final' のものがあれば、表示・再実行時に T-5min とみなすフォールバックを入れるか、
  もしくはバリデーションで弾いて分かりやすく案内する（どちらにするか結果レポートに明記）。

### 4. データ整合（任意・安全側）
- odds_snapshot テーブルに残っている final 行は削除しなくてよい（検索経路から到達不能になるだけ）。
  ただし将来の誤用を避けるため、結果レポートに「final 行は存在するが検索からは使えない」旨を記載。

## テスト

1. `snapshot: 'final'` を指定した検索・保存が 400 で拒否され、日本語メッセージが返る。
2. `snapshot` 省略時に T-5min として動作する。
3. 既存のオッズ min/max・condition_null 除外・3連単限定・非3連単エラーのテストが T-5min で引き続き通る。
4. 画面に final 切替 UI が存在しないこと（E2E で select の option に 'final' が無いことを確認）。
5. 既存の全テスト（ユニット + E2E）がグリーン。

## DoD

1. 全テストグリーン。
2. 結果レポートに: 変更ファイル / final を弾く挙動 / 保存済みfinal手法の扱い / UI変更点 / 既知の制限。
3. ローカルコミット1つ（push しない）。
