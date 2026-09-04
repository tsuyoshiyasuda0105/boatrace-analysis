# 勝ち筋サーチ Step 1 実装仕様書 — as-of 検索テーブル生成バッチ

作成: 2026-08-15 リン（Claude Code）/ 発注先: Codex
目的: 条件検索×回収率エンジンの土台となる「as-of 特徴量スナップショットテーブル」を生成するバッチを実装する。

## 背景（1分で読める要約)

過去の各レースについて「前日の夜に知り得た値」だけを復元した 1レース=1行 のテーブルを作る。
検索・回収率計算はこのテーブルだけを参照する。未来情報（レース当日以降のデータ）が
条件列に混入しないことが本システムの生命線である。

## 絶対的な制約（違反禁止）

1. **新規ファイルのみ作成すること。既存ファイルの変更は一切禁止**（docs/handoff.md も触らない）。
2. 出力先は新規 SQLite ファイル `data/kachisuji_search.db`。**`data/boatrace.db` への書込みは禁止**（読み取りのみ）。
3. `data/boatrace.db` の読み取りは必ず `src.db.connection.connect()` を使う（WALモード必須。直接 sqlite3.connect 禁止）。
4. ネットワークアクセス禁止。スクレイピング禁止。タスクスケジューラ登録禁止。デプロイ禁止。git push 禁止。
5. `scripts/install_all_tasks.ps1` / `scripts/startup_catchup.py` の実行禁止。
6. コミットは main への **ローカルコミット1つ**にまとめる。メッセージ: `Add as-of feature snapshot builder (kachisuji step 1)`。

## 作成するファイル

- `src/features/asof_builder.py` — 生成ロジック本体
- `scripts/build_asof_features.py` — CLI エントリポイント
- `tests/test_asof_builder.py` — テスト（合成フィクスチャDB使用）
- `docs/kachisuji_asof_step1_result_20260815.md` — 実行結果レポート（最後に書く）

## 出力テーブル定義

DB: `data/kachisuji_search.db` / テーブル: `asof_race_features` (1レース=1行)

### キー・メタ
- `race_id` TEXT PRIMARY KEY（既存DBの race_id 形式に合わせる）
- `race_date` TEXT (YYYY-MM-DD)
- `asof_date` TEXT — 集計締め日。**必ず race_date の前日**
- `built_at` TEXT — 生成時刻
- `schema_version` INTEGER — 1 固定

### レース条件列（前日確定のみ）
- `jcd` INTEGER — 会場コード（1-24）
- `race_no` INTEGER
- `grade` INTEGER — 既存DBのグレード区分に従う
- `day_index` TEXT — '初日'/'中日'/'最終日'/NULL。導出方法: 同一開催（jcd×連続開催期間）内の日付順位から導出。既存DBに節情報があればそれを優先。導出不能なら NULL
- `daypart` TEXT — 'モーニング'/'デイ'/'ナイター'/NULL。その場のその日の第1レースの予定時刻で分類（〜10:59開始=モーニング、〜16:59=デイ、17:00〜=ナイター相当。既存データから合理的に定義し docstring に明記）
- `female_present` INTEGER — レース内に女性選手が1人以上=1/男性のみ=0/性別不明選手あり=NULL（racers.gender 使用）
- `class_mix` TEXT — 'A1単騎'（1号艇のみA1）/'1号艇A1'（1号艇A1かつ他にもA1）/'A1複数_1号艇非A1'/'A1なし' の4分類
- `tide_phase` TEXT — '満潮前後'/'干潮前後'/'上げ潮'/'下げ潮'/NULL。既存の race_tides（または潮汐テーブル）から、レース予定時刻と満干時刻の関係で分類。テーブルが無い/欠測の場合は NULL。分類基準は docstring に明記

### 艇別条件列（N=1..6 の6セット）
番組表（race_entries 等に保存済みの公式B票由来の値）を**そのまま転記**する。再計算禁止:
- `bN_class` TEXT — 級別 'A1'/'A2'/'B1'/'B2'
- `bN_motor_rate2` REAL — モーター2連対率（番組表掲載値）
- `bN_national_rate` REAL — 全国勝率（番組表掲載値）
- `bN_local_rate` REAL — 当地勝率（番組表掲載値）
- `bN_racer_id` INTEGER

自前集計（**asof_date 以前のデータのみ**で計算。締め日厳守）:
- `bN_kimarite_rate_nige` REAL / `bN_kimarite_rate_sashi` / `bN_kimarite_rate_makuri` / `bN_kimarite_rate_makurizashi` / `bN_kimarite_rate_nuki` / `bN_kimarite_rate_megumare`
  — その選手の直近1年間（[asof_date-364日, asof_date]）の「当該決まり手での1着回数 ÷ 出走回数」×100。出走が0なら NULL
- `bN_accident_rate` REAL — 同じ窓での事故率（%）。事故の定義（F/L/失格/転覆等のコード）は既存DBの結果コードを調査して合理的に定め、**docstring に採用コード一覧を明記**。出走0なら NULL

### 結果・払戻列（答え合わせ専用。条件判定に使ってはならない）
- `result_sanrentan` TEXT（例 '1-2-3'）/ `payout_sanrentan` INTEGER
- `result_nirentan` TEXT / `payout_nirentan` INTEGER
- `result_tansho` INTEGER（勝ち艇番）/ `payout_tansho` INTEGER
- レース中止・結果未確定は結果列 NULL（行自体は作る）

## CLI 仕様（scripts/build_asof_features.py）

```
python scripts/build_asof_features.py --backfill --date-from 2024-06-01 --date-to 2024-06-07
python scripts/build_asof_features.py --daily            # 翌日分を生成（同じコードパス）
python scripts/build_asof_features.py --verify --sample 20
```

- `--backfill`: 指定期間を日付昇順で処理。**既存 race_id はスキップ**（append-only）。`--rebuild` フラグ併用時のみ該当期間を DELETE→INSERT
- `--daily`: 明日の日付のレースを対象に同じ生成関数を呼ぶ（バックフィルと本番が同一コードパスであること）
- `--verify`: ランダムに N 行サンプリングし、(a) asof_date < race_date の全行検査（全行対象）、(b) 未来遮断の不変性検査 — サンプル行について「asof_date より後のデータを除外した状態」で自前集計列を再計算し、保存値と一致することを確認。不一致があれば exit 1
- 進捗は 1000 レースごとに標準出力へ。`$env:PYTHONIOENCODING = "utf-8"` 前提の UTF-8 出力

## テスト仕様（tests/test_asof_builder.py）

合成フィクスチャ（一時 SQLite）で最低限以下を検証:
1. **未来遮断**: ある選手の集計窓の内側/外側（前日当日・1年境界）にレースを配置し、窓外が混入しないこと
2. **不変性**: フィクスチャに「未来のレース」を追加しても、過去レース行の自前集計列が変わらないこと
3. **転記の忠実性**: 番組表値（motor_rate2 等）が再計算されずそのまま入ること
4. **class_mix / female_present** の分類が正しいこと
5. **append-only**: 同一期間を2回 backfill しても行が重複せず、値も変わらないこと
6. 結果未確定レースで結果列が NULL になり、行は生成されること

既存テストへの影響なし（新規ファイルのみ）。実行は `.venv/Scripts/python.exe -m pytest tests/test_asof_builder.py -q`。
pytest の一時ディレクトリ問題がある環境のため、必要なら `--basetemp .pytest_tmp_asof` を使用（リポジトリ直下、コミットしない）。

## 完了条件（Definition of Done）

1. `tests/test_asof_builder.py` 全件グリーン
2. 実データで `--backfill --date-from 2024-06-01 --date-to 2024-06-07` を実行し、生成行数・NULL率・処理時間を確認
3. 同期間で `--verify --sample 20` が exit 0
4. `docs/kachisuji_asof_step1_result_20260815.md` に実行結果（行数、NULL 率の高い列とその理由、事故率の採用コード定義、既知の制限）を記載
5. ローカルコミット1つ（push しない）

## 実装上の注意

- スキーマ調査から始めること: `data/boatrace.db` の races / race_entries / race_results / race_payouts / racers / 潮汐系テーブルの実カラム名を確認してから書く。カラム名をこの仕様書から推測で決め打ちしない
- 決まり手の表記（漢字/コード）も実データを確認して対応表を作る
- 1年窓の集計はレースごとに全履歴を舐めると遅い。日付順に処理しながら選手別の履歴をインメモリで持つ等、22.4万レースを現実的な時間（目安: バックフィル全期間で1時間以内）で処理できる設計にする。ただし今回の DoD は1週間分でよい
- 女性選手判定: racers.gender が空/未投入の選手は NULL 扱い（0 にしない）
- 例外時は該当レースをスキップして warning を出し、最後にスキップ数を報告（黙って欠測を作らない）
