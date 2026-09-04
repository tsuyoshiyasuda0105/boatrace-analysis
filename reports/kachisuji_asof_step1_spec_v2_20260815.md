# 勝ち筋サーチ Step 1 実装仕様書 v2 — as-of 検索テーブル生成バッチ

作成: 2026-08-15 リン（Claude Code）/ 発注先: Codex
本書は v1 (`reports/kachisuji_asof_step1_spec_20260815.md`) を**置き換える**。確定 UI 仕様
（デモUI「勝ち筋サーチ」）に合わせて条件列を更新した版。v1 は読まなくてよい。

## 背景（1分で読める要約）

条件検索×回収率エンジンの土台。過去の各レースについて「事前に知り得た値」を復元した
1レース=1行 のテーブルを作る。検索・回収率計算はこのテーブルだけを参照する。
未来情報（レース結果以後にしか確定しない値を条件列に使うこと）の混入が絶対の禁止事項。

## 絶対的な制約（違反禁止）

1. **新規ファイルのみ作成。既存ファイルの変更は一切禁止**（docs/handoff.md も触らない）。
2. 出力先は新規 SQLite ファイル `data/kachisuji_search.db`。**`data/boatrace.db` への書込み禁止**（読み取りのみ）。
3. `data/boatrace.db` の読み取りは必ず `src.db.connection.connect()` を使う（WAL。直接 sqlite3.connect 禁止）。
4. ネットワークアクセス・スクレイピング・タスクスケジューラ登録・デプロイ・git push 禁止。
5. `scripts/install_all_tasks.ps1` / `scripts/startup_catchup.py` の実行禁止。
6. コミットは main への**ローカルコミット1つ**。メッセージ: `Add as-of feature snapshot builder (kachisuji step 1)`。

## 作成するファイル

- `src/features/asof_builder.py` — 生成ロジック本体
- `scripts/build_asof_features.py` — CLI エントリポイント
- `tests/test_asof_builder.py` — テスト（合成フィクスチャDB使用）
- `docs/kachisuji_asof_step1_result_20260815.md` — 実行結果レポート（最後に書く）

## 出力テーブル定義

DB: `data/kachisuji_search.db` / テーブル: `asof_race_features`（1レース=1行）

### キー・メタ
- `race_id` TEXT PRIMARY KEY / `race_date` TEXT (YYYY-MM-DD)
- `asof_date` TEXT — 前日集計の締め日。**必ず race_date の前日**
- `built_at` TEXT / `schema_version` INTEGER = 2

### レース条件列
確定タイミング区分: 📋=前日までに確定 / ⏱=当日確定（過去検証には使えるが、条件列としての性質をコメントで区別すること）

- `jcd` INTEGER（1-24）/ `race_no` INTEGER / `grade` INTEGER 📋
- `day_index` TEXT — '初日'/'中日'/'最終日'/NULL 📋（同一開催の日付順位から導出。導出不能は NULL）
- `daypart` TEXT — 'モーニング'/'デイ'/'ナイター'/NULL 📋（その場その日の第1レース予定時刻で分類。基準は docstring に明記）
- `female_present` INTEGER — 女性1人以上=1/男性のみ=0/性別不明あり=NULL 📋（racers.gender）
- `class_mix` TEXT — 'A1単騎'/'1号艇A1'/'A1複数_1号艇非A1'/'A1なし' 📋
- `tide_phase` TEXT — '満潮前後'/'干潮前後'/'上げ潮'/'下げ潮'/NULL 📋（潮汐テーブル×レース予定時刻。基準は docstring に明記）
- `weather` TEXT — '晴'/'曇'/'雨'/その他/NULL ⏱（race_previews.weather_number 等の実測記録から。対応表を docstring に）
- `wind_dir` TEXT — '追い風'/'向かい風'/'横風左'/'横風右'/NULL ⏱（wind_direction_number と会場の向きから導出。導出が困難なら生の方位番号を `wind_dir_raw` INTEGER として保存し、分類列は NULL のままでよい。判断は結果レポートに記載）
- `wind_speed` REAL ⏱（m/s 実測記録）

### 艇別条件列（N=1..6 の6セット）

番組表（公式B票由来、race_entries 等に保存済み）の**転記**（再計算禁止）📋:
- `bN_racer_id` INTEGER / `bN_class` TEXT（'A1'/'A2'/'B1'/'B2'）
- `bN_avg_st` REAL — 平均ST（番組表掲載値）
- `bN_national_rate` REAL / `bN_local_rate` REAL — 全国勝率・当地勝率（番組表掲載値）
- `bN_motor_rate2` REAL — モーター2連対率（番組表掲載値）

当日の直前情報（race_previews / exhibition_ranks に保存済みの実測値の転記）⏱:
- `bN_ex_time` REAL — 公式展示タイム（秒）
- `bN_ex_rank` INTEGER — レース内の展示タイム順位（1-6。同タイムは同順位で可、規則を docstring に）
- `bN_ex_dev` REAL — 展示タイムの**レース内6艇平均との差**（秒。負=平均より速い。定義を docstring に明記）
- `bN_ex_st` REAL — 展示ST（秒。F は負値等、元データの表現に従い docstring に明記）

自前集計（**asof_date 以前のデータのみ**。窓=[asof_date-364日, asof_date] 固定）📋:
- `bN_kimarite_rate_nige` / `_sashi` / `_makuri` / `_makurizashi` / `_nuki` / `_megumare` REAL
  — その選手の窓内「当該決まり手での1着回数 ÷ 出走回数」×100。出走0なら NULL
- `bN_accident_rate` REAL — 窓内の事故率（%）。事故の定義（F/L/失格等の採用コード）は実データを調査して定め docstring に明記。出走0なら NULL

### 結果・払戻列（答え合わせ専用）
- `result_sanrentan` TEXT / `payout_sanrentan` INTEGER
- `result_nirentan` TEXT / `payout_nirentan` INTEGER
- `result_tansho` INTEGER / `payout_tansho` INTEGER
- 中止・未確定は結果列 NULL（行は作る）

### 欠測の扱い（重要）
- 展示系・天候系はデータ蓄積開始（展示タイム: 2024-11-24 頃〜）より前のレースでは NULL。
  **NULL を 0 に潰さない**。検索時に「条件判定不能=母数から除外」できるよう NULL のまま保持。
- 各列の実カバレッジ（値が入っている最古日付と充足率）を生成後に集計し、結果レポートに表で記載すること。
  特に: 番組表転記列が 2022-05 より前（DB には 2016 年からのレースがある）にどこまで遡れるか、
  weather/wind が過去どこまで記録されているかを必ず調査・報告する。

## CLI 仕様（scripts/build_asof_features.py）

```
python scripts/build_asof_features.py --backfill --date-from 2025-06-01 --date-to 2025-06-07
python scripts/build_asof_features.py --daily            # 翌日分（同一コードパス）
python scripts/build_asof_features.py --verify --sample 20
python scripts/build_asof_features.py --coverage         # 列ごとのカバレッジ集計を表示
```

- `--backfill`: 日付昇順処理。既存 race_id はスキップ（append-only）。`--rebuild` 併用時のみ該当期間 DELETE→INSERT
- `--daily`: 明日のレースを同じ生成関数で処理（⏱列・結果列は当然 NULL になる。後続の再訪で埋める設計は Step 4 の範囲なので今回は不要）
- `--verify`: (a) 全行で asof_date < race_date 検査 (b) サンプル N 行の未来遮断不変性検査（asof_date より後のデータを除外して自前集計列を再計算→一致確認）。不一致で exit 1
- 進捗は 1000 レースごとに UTF-8 で標準出力

## テスト仕様（tests/test_asof_builder.py）

合成フィクスチャで最低限:
1. 未来遮断: 集計窓の内外境界（前日/当日/1年前）で混入なし
2. 不変性: 未来レースを追加しても過去行の自前集計列が不変
3. 転記の忠実性: 番組表値・展示値が再計算されずそのまま入る
4. `bN_ex_rank` / `bN_ex_dev` の計算が正しい（6艇の合成タイムで検証）
5. class_mix / female_present / 買い目3種の払戻列の突合せが正しい
6. append-only: 再実行で重複・変化なし
7. 展示データ未収集レースで展示列が NULL のまま行が生成される

実行: `.venv/Scripts/python.exe -m pytest tests/test_asof_builder.py -q`（必要なら `--basetemp .pytest_tmp_asof`）

## 完了条件（DoD）

1. テスト全件グリーン
2. 実データで `--backfill --date-from 2025-06-01 --date-to 2025-06-07` 実行（展示データがある週）→ 行数確認
3. 同期間で `--verify --sample 20` が exit 0
4. `--coverage` の出力（列別の最古日付・充足率）を結果レポートに記載
5. `docs/kachisuji_asof_step1_result_20260815.md` に: 作成ファイル / テスト結果 / 生成行数 / カバレッジ表 / 事故率の採用コード定義 / wind_dir の扱いの判断 / 既知の制限
6. ローカルコミット1つ（push しない）

## 実装上の注意

- **スキーマ調査から始める**: races / race_entries / race_results / race_payouts / racers / race_previews / exhibition_ranks / 潮汐系テーブルの実カラム名・値域を確認してから書く。本仕様書のカラム名を推測で決め打ちしない
- 決まり手の表記（漢字/コード）は実データで対応表を作る
- 1年窓集計は日付順ストリーミング（選手別履歴のインメモリ保持等）で、全期間バックフィルが1時間以内に収まる設計に。ただし今回の DoD は1週間分でよい
- 例外時は該当レースをスキップして warning、最後にスキップ数を報告（黙って欠測を作らない）
