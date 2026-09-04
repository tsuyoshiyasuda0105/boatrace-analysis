# 勝ち筋サーチ Step 5 実装仕様書 — 艇間比較条件・年齢・起動ランチャー

作成: 2026-08-15 リン（Claude Code）/ 発注先: Codex
前提: Step 1〜4 実装済み（f2ee23a, 0bb3fcc, e024a19, 7cdeb00, 423ddbc）。
目的: ユーザーの実手法（adopted_strategies.md 記載）を検証できるよう、**艇同士の比較条件**と
不足している条件軸を追加する。あわせて、非エンジニアのオーナーがダブルクリックで起動できる
ランチャーを追加する。

## 変更を許可するファイル（これ以外の既存ファイルは変更禁止）

- `src/features/asof_builder.py`（列追加）
- `src/search/roi_search.py`（条件追加）
- `src/search/strategies.py`（照合の追随。条件パーサ再利用が保たれていれば変更不要のはず）
- `src/kachisuji_web/app.py` / `templates/search.html` / `static/kachisuji.css`
- `tests/test_asof_builder.py` / `tests/test_roi_search.py` / `tests/test_strategies.py` / `tests/test_kachisuji_web.py`
- 新規: `scripts/start_kachisuji.bat`, `docs/kachisuji_compare_step5_result_20260815.md`

制約は従来どおり: 本番 `src/web/` 変更禁止 / `data/boatrace.db` は読み取りのみ
（今回は builder の変更があるため読み取りは可）/ 実サーバー起動禁止（test_client のみ）/
ネットワーク・スケジューラ・デプロイ・push 禁止 / ローカルコミット1つ
（メッセージ: `Add boat comparison conditions and launcher (kachisuji step 5)`）。

## A. as-of テーブルへの列追加（schema_version=3）

既存 DB（data/kachisuji_search.db, 54.6万行）を壊さないこと:
- 新列は `ALTER TABLE ... ADD COLUMN` で追加し、既存行は NULL のままでよい
  （全期間の再生成=--rebuild はリンが後で実行する）
- schema_version 3 の行を書けるようにし、既存の v2 行と共存できること

追加列:
1. `bN_age` INTEGER（N=1..6）📋 — レース日時点の満年齢。
   選手の生年月日が `data/boatrace.db` 内（racers 等）に存在するか**まず調査**し、
   あれば `race_date` 基準で計算。存在しなければ全行 NULL とし、
   結果レポートに「生年月日データが未投入のため年齢は未対応」と明記（勝手に外部取得しない）。
2. `bN_national_rate2` REAL 📋 — 全国2連対率（番組表掲載値。race_entries を調査して転記）。
   同様に `bN_local_rate2` REAL（当地2連対率）も掲載値があれば追加、なければ省略して報告。

## B. 検索エンジンへの条件追加（src/search/roi_search.py）

### B-1. レース条件
- `race_no`: `{"min": 7, "max": 12}` — レース番号範囲（手法の「7-12R」「10-12R」に対応）

### B-2. 艇別条件
- `age`: `{"min": x}` / `{"max": x}`（bN_age。NULL は condition_null 除外）
- `national_rate2`: `{"min"/"max"}`（bN_national_rate2）
- （local_rate2 を追加した場合は同様に）

### B-3. 艇間比較条件（本ステップの核）
条件JSONに新キー `compare`（配列）を追加:

```json
"compare": [
  {"metric": "motor_rate2", "boat": 2, "op": "ge", "other": 4, "margin": 5},
  {"metric": "avg_st",      "boat": 4, "op": "ge", "other": 1, "margin": 0.02},
  {"metric": "ex_time",     "boat": 3, "op": "le", "other": 2, "margin": 0},
  {"metric": "ex_st",       "boat": 1, "op": "le", "other": 2, "margin": 0},
  {"metric": "age",         "boat": 1, "op": "le", "other": 2, "margin": 0}
]
```

- 意味: `boat の値 (op) other の値 ± margin`。
  - `"op":"ge","margin":5` → boat の値 ≥ other の値 + 5（「2号艇が4号艇より5pt以上優位」）
  - `"op":"le","margin":0.02` → boat の値 ≤ other の値 − 0.02 …ではなく、
    **符号規約が紛らわしいので次で固定する**: 判定式は常に
    `value(boat) - value(other) >= margin`（op="ge"）/ `value(boat) - value(other) <= -margin`（op="le"）。
    margin は常に 0 以上。docstring とテストでこの規約を明示すること。
- 対応 metric: `motor_rate2` / `avg_st` / `ex_time` / `ex_st` / `national_rate` / `local_rate` /
  `national_rate2` / `age`（列が存在するもののみ。ホワイトリスト検証）
- どちらかの艇の値が NULL の行は condition_null 除外（既存規則と同じ）
- `boat` と `other` が同一はエラー。複数の compare 条件は AND
- SQL の WHERE に直接式を生成してよいが、**列名はホワイトリスト由来のみ**（文字列連結の
  インジェクション余地を残さない）。margin はパラメータバインド

### B-4. 「隣接艇より遅い」の扱い
手法には「隣接艇最速より0.02以上遅い」があるが、今回は**ペア比較の AND で表現可能**
（例: 4号艇 vs 3号艇 と 4号艇 vs 5号艇 の2条件）なので、専用構文は追加しない。
結果レポートにこの表現方法を記載すること。

## C. 画面（search.html）

- レース条件に「レース番号」範囲（1〜12 の from/to セレクト）を追加
- 艇別条件に「年齢」（以上/以下＋数値）と「全国2連対率」を追加（年齢が未対応なら
  グレーアウトし「生年月日データ未投入のため準備中」と表示）
- 新セクション「⚖ 艇間比較」: 行の追加/削除ができる UI。
  各行: 「[N号艇] の [指標] が [M号艇] より [X] 以上 [高い/低い]」
  （内部で B-3 の規約に変換。指標の単位表示に注意: モーター=pt、ST/展示=秒、年齢=歳）
- 保存済み手法（compare 含む）が照合でもそのまま動くこと

## D. 起動ランチャー（scripts/start_kachisuji.bat）

オーナーは黒い画面を使わないため、**ダブルクリックで起動**できる bat を作る:
1. リポジトリルートに cd（bat 自身の場所から相対で解決）
2. 既に http://127.0.0.1:8080/healthz が応答する場合はサーバーを二重起動しない
3. `.venv\Scripts\python.exe scripts\run_kachisuji_web.py --port 8080` を起動
4. 既定ブラウザで http://localhost:8080 を開く
5. ウィンドウタイトルを「勝ち筋サーチ」にし、閉じればサーバーも止まることをコメントで明記
- **bat は ASCII のみで記述**（echo する日本語は使わない。文字化け回避のプロジェクト慣習）

## E. テスト

1. compare 規約の判定式（ge/le × margin）が docstring どおり（境界値含む）
2. compare で片側 NULL → condition_null 除外
3. metric/boat のホワイトリスト検証（不正はエラー）
4. race_no 範囲
5. age / national_rate2 の min/max（列が NULL のときの除外）
6. compare を含む手法の保存→照合が動く（strategies 経由の回帰）
7. 既存テスト全件が引き続きグリーン（schema v2 行との共存を含む）

## F. DoD

1. 全テストグリーン（`tests/test_asof_builder.py test_roi_search.py test_strategies.py test_kachisuji_web.py`）
2. 結果レポートに: 変更ファイル / テスト結果 / 生年月日データの調査結果 /
   全国2連対率・当地2連対率の番組表列の調査結果 / compare 規約の説明 /
   adopted_strategies.md のうち**この拡張で表現可能になった条件と、まだ表現できない条件の一覧**
   （例: 180日実測平均ST・STブレ・コース別勝率・T-5オッズ帯・直線/周回タイム順位・攻め決まり手回数 等）
3. ローカルコミット1つ（push しない）
