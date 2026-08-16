# 勝ち筋サーチ Step 15 結果 — 平均STの自前復元

## 結論

公式K成績ファイルの着順行を、Step 13事故復元と同じ
`src.parsers.official_k._parse_result_row` 経路で1回だけ解析し、実測STを
`data/kachisuji_search.db.start_timing_events` に保存できるようにした。
`asof_race_features.bN_avg_st` は schema version 7 から「レース前日までの直近180日」の
自前集計値に切り替え、有効走数 `bN_avg_st_n` と番組表値
`bN_avg_st_official` を別列で保持する。

全期間の復元・schema-v7再生成はリンが実行する。Codexは全期間処理を実行していない。

## 既存パーサの再利用と統合

- `parse_official_result_text()` の1回の走査で、Step 13の出走・事故とStep 15のSTを同時に生成する。
- 従来は診断用 `parse_k_text()` と固定幅プレフィックス処理が同じ着順行を二重解析していた。
  Step 15では `parse_k_text()` の先行走査を廃止し、各候補行を
  `_parse_result_row` に1回だけ通す構造へ統合した。
- 既存正規表現が受け付けない `F0.02` と着順 `L0`/`L1` は、意味を変えない最小正規化後に
  `_parse_result_row` を1回呼ぶ。項目のない取消行だけは既存のrank/boat/racerプレフィックスを使い、
  course/STを推測せずNULLにする。解析不能・重複・6艇不一致は警告と集計対象であり、黙って欠測にしない。
- 既存事故復元関数・テーブルは変更せず、新規 `restore_start_timing.py` は同じ解析結果型を再利用して
  `start_timing_events` だけを書く。すでに事故履歴が復元済みの実DBを再書込みしないため、
  復元CLI自体はStep 13 CLIと分離した。

## STの符号規約と平均への含め方

- 通常 `0.14` → `start_timing=+0.14`, `is_flying=0`, `is_late=0`。
- `F0.02` → フライングは0.02秒早いので `start_timing=-0.02`, `is_flying=1`。
- L/L0/L1または `.`/解析不能ST → `start_timing=NULL`。L系は `is_late=1`。
- 平均には `start_timing IS NOT NULL AND is_flying=0 AND is_late=0` の通常STだけを含める。
  K成績の負のF値を公式の審査期平均に含めるという権威ある根拠は、ネットワーク禁止下の
  リポジトリ資料・仕様内では確認できなかった。仕様のデフォルトに従い、平均を歪めるFは除外した。
  LとNULLも除外する。
- 有効通常STが0走なら `bN_avg_st=NULL`, `bN_avg_st_n=0`。0秒という値は作らない。

## 180日窓とas-of

レース日を `D` とすると窓は **`[D - 180日, D)`**。すなわち下限日を含み、レース前日までの
180暦日を含み、レース当日と未来を除外する。F/L/NULL除外後の件数が `bN_avg_st_n` である。
合成境界テストではD-181日を除外、D-180日とD-1日を採用し、D当日・未来を除外した。
`verify_features()` もschema 7では同じ履歴から平均とnを再計算して照合する。

## 番組表由来の公式平均STとの差

番組表の `race_entries.avg_start_timing` は審査期通算の公式掲載値で、2025年5月以降しかない。
Step 15の `bN_avg_st` は公式成績の各走実測値を使うローリング180日平均であり、期間・母集団・
F等の取扱いを同一視できない。このためschema 7では自前180日値を `bN_avg_st`、番組表値を
参考列 `bN_avg_st_official` に分離した。検索と艇間比較の `avg_st` は自前180日値を使う。

## 年別固定幅フォーマット監査

2016～2026年について各年8ファイル、合計88 TXTを期間内で等間隔抽出した。全88ファイルで
スキップ行0、6艇不一致0だった。fallbackはST等の項目がない取消/欠測行で、NULLとして明示保存した。
`late` は `missing` の内数である。

| 年 | ファイル | ST行 | 通常有効 | F | L | NULL | fallback | skip | 6艇不一致 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2016 | 8 | 7,200 | 7,156 | 32 | 1 | 12 | 11 | 0 | 0 |
| 2017 | 8 | 7,848 | 7,815 | 26 | 0 | 7 | 7 | 0 | 0 |
| 2018 | 8 | 7,344 | 7,308 | 27 | 2 | 9 | 8 | 0 | 0 |
| 2019 | 8 | 7,356 | 7,304 | 41 | 0 | 11 | 11 | 0 | 0 |
| 2020 | 8 | 6,756 | 6,699 | 42 | 1 | 15 | 15 | 0 | 0 |
| 2021 | 8 | 7,200 | 7,148 | 39 | 3 | 13 | 12 | 0 | 0 |
| 2022 | 8 | 7,524 | 7,469 | 39 | 0 | 16 | 16 | 0 | 0 |
| 2023 | 8 | 7,920 | 7,863 | 36 | 0 | 21 | 21 | 0 | 0 |
| 2024 | 8 | 7,560 | 7,501 | 50 | 0 | 9 | 9 | 0 | 0 |
| 2025 | 8 | 7,464 | 7,413 | 25 | 1 | 26 | 26 | 0 | 0 |
| 2026 | 8 | 6,624 | 6,589 | 29 | 0 | 6 | 6 | 0 | 0 |

## サンプル復元

平均計算の180日ウォームアップを欠かさないため、復元対象は2019-07-05～2020-06-30、
評価対象は2020-01-01～2020-06-30とした。書込みは実DBの新規 `start_timing_events` のみ。
`asof_race_features`、Step 13テーブル、`boatrace.db`、rawファイルは書き換えていない。

復元全体（362ファイル）は324,252行、通常有効322,438、F 1,234、L 53、NULL 580、
fallback 566、ファイルskip 0、行skip 0、6艇不一致0。LはNULLの内数。

評価期間2020-01～06の内訳:

- STイベント 164,412行、通常有効 163,427、F 639、L 30、NULL 346。
- 公式K側レースID 27,402件。boatrace側評価対象は24,757レース / 148,542艇。
- 180日平均が入った艇: 148,504 / 148,542（99.9744%）。
- 1艇以上に平均が入ったレース: 24,757 / 24,757（100%）。
- 6艇すべてに平均が入ったレース: 24,719 / 24,757（99.8465%）。
- 有効走数nの中央値111、最小0、最大182、n>=4は148,383艇。
- `PRAGMA integrity_check=ok`。既存 `asof_race_features` は557,617行・最大schema 6のままで、
  サンプル処理による再生成はしていない。

## 変更ファイル

- `src/features/accident_history.py`
- `scripts/restore_start_timing.py`
- `src/features/asof_builder.py`
- `src/search/roi_search.py`
- `src/kachisuji_web/templates/search.html`
- `tests/test_accident_history.py`
- `tests/test_start_timing_history.py`
- `tests/test_asof_builder.py`
- `tests/test_roi_search.py`
- `tests/test_kachisuji_web.py`
- 本結果レポート

CSSは既存の `restored-condition` / `chip restored` で要件を満たしたため変更していない。
本番 `src/web/` は変更していない。

## テスト

- focused（parser/既存事故/as-of/ROI/Web契約）: 148 passed。
- non-E2E全体: 959 passed, 1 skipped。skipは既存のデータ依存モジュール、warningは既存
  `.pytest_cache` ACLのみ。
- main E2E: 56 passed。round3 E2E: 3 passed。8090/8091は開始前後ともlistener 0。
- Python compile / `git diff --check`: pass。

## 既知の制限と次の作業

- サンプル期間しか実データ復元していない。リンが2016-06以降を復元し、schema-v7を全期間再生成する。
- fallback行はcourse/STを推測せずNULL。年別監査では黙ったskipや6艇不一致はなかった。
- Fを公式審査期平均に含めるかの権威ある根拠は未確認で、仕様どおり通常STのみ平均した。
- `bN_avg_st_n` は検索DB列として保持する。現UIは母数の意味を案内するが、個別nの表示追加は別UI要件。
- push、network、scheduler、deployは実行していない。
