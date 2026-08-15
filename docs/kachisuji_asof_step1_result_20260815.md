# 勝ち筋サーチ Step 1 実装結果（2026-08-15）

## 作成ファイル

- `src/features/asof_builder.py` — as-of 行生成、365日窓集計、検証、カバレッジ集計
- `scripts/build_asof_features.py` — backfill / daily / verify / coverage CLI
- `tests/test_asof_builder.py` — 合成SQLiteフィクスチャによる7テスト
- `docs/kachisuji_asof_step1_result_20260815.md` — 本レポート
- `data/kachisuji_search.db` — 実行生成物（`.gitignore` 対象、コミット対象外）

既存ファイルは変更していない。`data/boatrace.db` は
`src.db.connection.connect(str(config.DB_PATH))` で開き、直後に
`PRAGMA query_only=ON` を設定してSELECTのみに使用した。出力DB以外へのDML、
スクレイピング、scheduler、deploy、push は実行していない。

## テスト結果

- focused: `.venv/Scripts/python.exe -m pytest tests/test_asof_builder.py -q` → **7 passed**
- full: `.venv/Scripts/python.exe -m pytest tests -q` → **716 passed**
- 両方とも警告は既存 `.pytest_cache` を作成できない `PytestCacheWarning` 1件のみ。
- `py_compile`、最終 `git diff --check`、生成DB整合性検査も合格。

合成テストは、365日窓の前日・当日・1年前境界、未来レース追加後の不変性、
番組表・展示値の忠実な転記、同タイム展示順位と6艇平均との差、class/gender、
3券種払戻、append-only、展示欠測NULL維持を検証した。

## 実データ生成・verify

実行期間: `2025-06-01`〜`2025-06-07`

- 元DB選択レース: **1,104**
- 生成行数: **1,104**（1レース=1行、warning/skip-error 0）
- append-only再実行: inserted 0 / skipped_existing 1,104
- `--verify --sample 20`: exit 0
  - rows 1,104 / sampled 20
  - `asof_date >= race_date`: 0
  - 自前集計列の再計算不一致: 0

## 生成DBカバレッジ

全行数1,104。最古日は、値あり列ではすべて2025-06-01、意図的にNULLとした
`wind_dir` のみ該当なし。艇別の同種6列はまとめ、艇差があるものは艇番順に列挙した。

| 列または列ファミリ | 値あり/全体 | 充足率 | 最古日 |
|---|---:|---:|---|
| race_id / race_date / asof_date / built_at / schema_version | 各1104/1104 | 各100.00% | 2025-06-01 |
| jcd / race_no | 各1104/1104 | 各100.00% | 2025-06-01 |
| grade | 1018/1104 | 92.21% | 2025-06-01 |
| day_index / daypart | 各1104/1104 | 各100.00% | 2025-06-01 |
| female_present | 1079/1104 | 97.74% | 2025-06-01 |
| class_mix | 1104/1104 | 100.00% | 2025-06-01 |
| tide_phase | 564/1104 | 51.09% | 2025-06-01 |
| weather | 1086/1104 | 98.37% | 2025-06-01 |
| wind_dir | 0/1104 | 0.00% | — |
| wind_dir_raw | 1083/1104 | 98.10% | 2025-06-01 |
| wind_speed | 1097/1104 | 99.37% | 2025-06-01 |
| b1..b6_racer_id | 各1104/1104 | 各100.00% | 2025-06-01 |
| b1..b6_class | 各1104/1104 | 各100.00% | 2025-06-01 |
| b1..b6_avg_st | 1101 / 1100 / 1098 / 1085 / 1041 / 994 | 99.73 / 99.64 / 99.46 / 98.28 / 94.29 / 90.04% | 2025-06-01 |
| b1..b6_national_rate | 各1104/1104 | 各100.00% | 2025-06-01 |
| b1..b6_local_rate | 各1104/1104 | 各100.00% | 2025-06-01 |
| b1..b6_motor_rate2 | 各1104/1104 | 各100.00% | 2025-06-01 |
| b1..b6_ex_time | 各1086/1104 | 各98.37% | 2025-06-01 |
| b1..b6_ex_rank | 各1086/1104 | 各98.37% | 2025-06-01 |
| b1..b6_ex_dev | 各1086/1104 | 各98.37% | 2025-06-01 |
| b1..b6_ex_st | 各1086/1104 | 各98.37% | 2025-06-01 |
| b1..b5_kimarite_rate_*（各6決まり手） | 各1104/1104 | 各100.00% | 2025-06-01 |
| b6_kimarite_rate_*（各6決まり手） | 各1102/1104 | 各99.82% | 2025-06-01 |
| b1..b5_accident_rate | 各1104/1104 | 各100.00% | 2025-06-01 |
| b6_accident_rate | 1102/1104 | 99.82% | 2025-06-01 |
| result_sanrentan / payout_sanrentan | 各1092/1104 | 各98.91% | 2025-06-01 |
| result_nirentan / payout_nirentan | 各1094/1104 | 各99.09% | 2025-06-01 |
| result_tansho / payout_tansho | 各1094/1104 | 各99.09% | 2025-06-01 |

### 元DB全期間の蓄積調査

生成期間だけでは開始日を判断できないため、`data/boatrace.db` 全期間
（557,425レース、3,153,894番組行）もquery-onlyで調査した。

| 元データ項目 | 値あり最古日 | 値あり数/母数 | 充足率 |
|---|---|---:|---:|
| racer_id / class | 2016-06-13 | 各3,153,894/3,153,894 | 100.00% |
| national_rate / local_rate / motor_rate2 | 2016-06-13 | 各3,153,894/3,153,894 | 100.00% |
| avg_st | 2025-05-08 | 409,486/3,153,894 | 12.98% |
| exhibition_time（レース単位） | 2024-11-24 | 94,736/557,425 | 17.00% |
| weather（レース単位） | 2024-11-24 | 94,866/557,425 | 17.02% |
| wind_direction（レース単位） | 2024-11-24 | 94,268/557,425 | 16.91% |
| wind_speed（レース単位） | 2016-06-13 | 550,338/557,425 | 98.73% |

番組表転記列は `avg_st` を除き、2022-05より前も2016-06-13まで100%遡れる。
`avg_st` は2025-05-08以前には値がない。展示、weather、wind direction は
2024-11-24から蓄積されているため、それ以前はNULLのまま保持される。

## 定義と判断

### 事故率

窓は `[asof_date-364日, asof_date]` の365日（両端含む）。分母は
`race_results` が存在する実出走行数とし、番組だけ存在する中止レースは除く。
分子は公式K票パーサが非数値着順として保持可能な
`K0, K1, S0, S1, S2, F, L, 失, 失格, 転, 落, 妨`。実DBで観測された
`remarks` は現時点で `S0, S1, S2` の3種だった。出走0はNULL。

### 展示順位・平均との差

6艇すべてに有限の展示タイムがある場合だけ算出。順位は
`1 + 自艇より速い艇数`（同タイムは同順位）、偏差は
`自艇タイム - 6艇平均`（負なら平均より速い）。1艇でも欠測なら順位・偏差は
6艇ともNULL。元の展示タイムと展示STは再計算せず転記し、F展示STの負値も保持する。

### wind_dir

元DBには `wind_direction_number` はあるが、24会場の水面向きを監査可能な形で
固定した対応表がない。推測分類による誤ラベルを避け、`wind_dir_raw` に生コードを
保存し、`wind_dir` はNULLとした。会場向き対応表が確定する後続Stepで分類可能。

### day_index / daypart / tide_phase

- day_index: 会場+開催タイトルの開催日を日付順にし、初日/中日/最終日を付与。
  同名開催の再発を避けるため2日を超える空白で別開催に分割し、1日の順延・中止は
  同一開催として扱う。
- daypart: 独立した発走時刻列がないため、第1Rの公示締切予定を代理値とし、
  10:30未満=モーニング、14:00以上=ナイター、その間=デイ。
- tide_phase: 既存 `race_tides` の予定時刻ベース分類を転記変換し、
  high/low/rising/falling を満潮前後/干潮前後/上げ潮/下げ潮とした。

## 既知の制限・作業時の失敗

- `daily` は翌日分を同じ生成関数で作るが、Step 1仕様どおり当日観測・結果の
  後日更新は行わない。
- class_mix と female_present は6艇の必要値が揃わない場合NULL。
- 最初の読み取り調査でDBパスを省略した `connect()` が `.env` の外部Postgresを
  選択した。サンドボックスが接続を拒否し、接続・読取り・書込みはいずれも不成立。
  以後は必ず `connect(str(config.DB_PATH))` とし、CLIにも同じ固定を実装した。
- 新規対象ファイルの存在確認を含む一括 `rg` は、正常な「該当なし」exit 1を
  束全体の失敗として扱った。変更は発生せず、以後は検索を分離した。

## コミット

ローカルmainへ、指定メッセージ
`Add as-of feature snapshot builder (kachisuji step 1)` で1コミット。
コミットハッシュは作成後の最終引き渡しに記載する。pushは行わない。
