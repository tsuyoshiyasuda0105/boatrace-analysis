# 勝ち筋サーチ Playwright バグハント Round 1 仕様書

作成: 2026-08-15 リン（Claude Code）/ 発注先: Codex
目的: 勝ち筋サーチ Web UI (src/kachisuji_web/) を Playwright で多パターン操作し、
**バグを洗い出して一覧化する**。このラウンドでは**修正は行わない**（発見と報告に専念）。

## 対象
- アプリ: `src/kachisuji_web/`（検索・保存・照合・艇間比較の全機能）
- 実 DB: `data/kachisuji_search.db`（557,425行, schema v3）を読み取りで使う
- 検索エンジン: `src/search/roi_search.py` / `src/search/strategies.py`

## 絶対的な制約（違反禁止）
1. **このラウンドはバグ修正禁止**。プロダクトコードを一切変更しない。
   作ってよいのは Playwright テストとバグ報告のみ。
2. サーバーは **テスト内で subprocess 起動 → テスト終了時に確実に終了** させる。
   ポートは 8090（本番運用の 8080 と分離）。起動しっぱなしにしない。
3. `data/boatrace.db` には接続しない。DB 書き込み・DDL 禁止。
   手法保存テストは**一時ファイルの strategies DB**（環境変数 KACHISUJI_STRATEGY_DB）を使い、
   実運用の手法 DB を汚さない。
4. ネットワーク（外部サイト）・スケジューラ・デプロイ・push 禁止。
5. コミットは main へのローカルコミット1つ。メッセージ: `Add Playwright bug-hunt suite (kachisuji round 1)`。

## 作成するファイル
- `tests/e2e/conftest.py` — サーバー起動/終了 fixture（ポート8090・一時strategies DB・KACHISUJI_DB指定）
- `tests/e2e/test_kachisuji_e2e.py` — Playwright テスト本体（下記シナリオ）
- `reports/kachisuji_bug_list_20260815.md` — **バグ一覧（本タスクの主成果物）**
- `docs/kachisuji_bughunt_round1_result_20260815.md` — 実行サマリ

## テストシナリオ（Playwright, chromium headless）

網羅的に「壊しに行く」こと。最低限以下を試す:

### S1. 基本フロー
- トップ表示、主要セクションの存在、検索→結果KPI表示
- 単勝/2連単/3連単の切替で1着/2着/3着セレクトの表示が正しく変わるか

### S2. 買い目・着順の妥当性
- 3連単で1着=2着など**同一艇番**を選んだときの挙動（エラー？ 無言で0件？ 期待は明確なメッセージ）
- 2連単/単勝で不要な着セレクトが送信されないか

### S3. 艇別条件・艇間比較
- 各艇の折りたたみ開閉、条件数バッジの増減
- 級別チップの複数選択、全選択時の扱い
- 選手名のみ入力 → 400 と案内が画面に出るか
- 艇間比較行の追加/削除、同一艇 vs 同一艇を選んだときのエラー表示
- 比較指標の単位表示（pt/秒/歳）が指標に応じて変わるか

### S4. 数値・境界
- 数値欄に負値・極端値・空・非数値を入れて検索したときの挙動
- 期間 date_from > date_to、未来日、範囲外の日付
- 何も条件を付けずに検索（全件）→ 応答時間とエラーの有無

### S5. 保存・一覧・照合
- 手法名を空で保存 → 弾かれるか
- 正常保存 → 一覧に出るか → 削除できるか
- 保存した手法で「本日/指定日」照合 → confirmed/pending の表示
- 手法名やテキストに **HTMLタグ/スクリプト（例: <script>, "><img>）** を入れて保存 →
  一覧表示で **エスケープされ、スクリプトが実行されない**こと（XSS チェック）
- 大量保存（20件）後の一覧・照合の動作

### S6. API 直接（page.request で）
- `/api/search` に未知キー、型違い（数値欄に文字列）、巨大 JSON → 400/500 の切り分け
- `/api/strategies` に壊れたペイロード
- `/api/matches?date=不正` の挙動
- `/healthz`

### S7. 表示・レスポンシブ・多重操作
- モバイル幅(390px)で崩れ・横スクロールの有無
- 検索連打（多重クリック）で二重送信・結果の取り違えが起きないか
- ブラウザ console にエラーが出ていないか（全シナリオで console error を収集）

## バグ一覧の書式（reports/kachisuji_bug_list_20260815.md）

各バグを以下の表で。**発見のみ。修正はしない**:

| ID | 深刻度 | 分類 | 再現手順（最小） | 期待 | 実際 | 該当ファイル/行(推定) |
|----|--------|------|------------------|------|------|----------------------|

- 深刻度: Critical(データ破壊/XSS/500連発) / High(機能不能) / Medium(誤動作) / Low(表示崩れ/UX)
- 分類: correctness / validation / security / ux / performance / display
- 「バグではないが改善余地」は別セクションに列挙
- **バグが0件ならその旨を明記**（無理に捏造しない。ただし S1-S7 を実際に試した証跡=テスト件数を書く）

## DoD
1. Playwright スイートが実行でき、結果（passed/failed/検出バグ数）が出る
   ※テストは「アプリの期待挙動」をアサートし、**バグに当たった箇所は xfail またはコメントで
   バグIDに紐付ける**（テスト自体が赤で終わってもよいが、何が失敗したか明確に）
2. `reports/kachisuji_bug_list_20260815.md` にバグ一覧
3. サーバープロセスが残っていないこと（fixture で確実に kill）
4. ローカルコミット1つ（push しない）
5. 最後に: 実行シナリオ数 / 検出バグ数（深刻度別）/ 主要バグ3件の要約 / コミットハッシュ を出力

## 注意
- Playwright chromium は導入済み（`.venv/Scripts/python.exe -m playwright` 利用可）。
- ヘッドレスで実行。スクリーンショットは任意（reports/ 配下なら可、コミットは軽量に）。
- サーバー起動に `scripts/run_kachisuji_web.py --port 8090` を使い、環境変数
  `KACHISUJI_DB=data/kachisuji_search.db` と一時 `KACHISUJI_STRATEGY_DB` を渡す。
