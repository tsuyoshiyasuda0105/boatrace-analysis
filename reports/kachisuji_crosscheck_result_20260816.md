# 勝ち筋サーチ Step 10 クロスチェック結果

実施日: 2026-08-16  
対象期間: **2026-07-01〜2026-07-31（両端を含む）**  
基準: `src/web/app.py` の `ROI_STRATEGIES` と、同ファイルの `_l4_daily_stats` が実際に選んだ既存ROI手法  
比較先: `src.search.roi_search.search_roi`

## 結論

- 登録94手法を分類し、`A=4 / B=33 / C=57` となった。
- 突合は **6手法**（A全4手法 + 主要B 2手法）で実行した。
- race_id集合まで含めた判定は **一致2 / 軽微差0 / 不一致4**。
- 不一致原因（手法単位、非排他的）は `data-source=3`、`condition-gap=1`、`kachisuji-bug=0`、`legacy-bug=0`、`period-mismatch=0`、`unknown=0`。
- **kachisuji-bug 疑いはない。** 事故率3手法の差は、同名フィールドが別定義であることを具体的race_idから確認した。A1エースM手法は集計値が偶然一致したがrace_id集合が違うため、不一致と判定した。

## 1. サマリ表

ここでROIは仕様の「回収率」（払戻合計÷購入額×100）を表す。`一致` はN・的中・回収率だけでなくrace_id集合も一致した場合に限る。

| 手法key | ラベル | 再現分類 | 既存N | 新N | 既存的中 | 新的中 | 既存ROI | 新ROI | 判定 | 原因分類 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| `wakamatsu_13_weak2_strong3_exa` | 若松 1-3 | A | 3 | 3 | 1 | 1 | 106.7% | 106.7% | 一致 | — |
| `tamagawa_13_acc2n30_m3_40_exa` | 多摩川 1-3 事故2型 | A | 0 | 1 | 0 | 0 | 0.0% | 0.0% | 不一致 | data-source |
| `kojima_12_acc3_m3_n23_exa` | 児島 1-2 事故3型 | A | 0 | 1 | 0 | 1 | 0.0% | 350.0% | 不一致 | data-source |
| `edogawa_13_acc2_n23_m3_exa` | 江戸川 1-3 事故2型 | A | 0 | 2 | 0 | 0 | 0.0% | 0.0% | 不一致 | data-source |
| `a1_ace_motor_123_corr_tri` | 1号艇A1エースM 1-2-3 | B | 18 | 18 | 4 | 4 | 128.3% | 128.3% | 不一致 | condition-gap |
| `omura_132_weak2_ex3_tri` | 大村 1-3-2 弱2展示 | B | 2 | 2 | 0 | 0 | 0.0% | 0.0% | 一致 | — |

`a1_ace_motor_123_corr_tri` はN・的中・回収率が同じでも、既存のみ1件・検索のみ1件が入れ替わっている。数字だけの辻褄一致を採用せず、不一致とした。`omura_132_weak2_ex3_tri` は既存の `<35` を検索の `<=35` で近似したB分類であり、この1か月で同じ集合になっただけで完全再現へ昇格させない。

## 2. 不一致の詳細

### `tamagawa_13_acc2n30_m3_40_exa`

- 差分: 検索のみ `20260720-05-10`。
- 勝ち筋側: 1号艇A1、2号艇事故率 `0.5181347150`、2号艇全国2連率 `35.87`、3号艇モーター2連率 `42.47` なので検索条件を満たす。結果は2連単 `1-4`、払戻270円で、買い目 `1-3` は不的中。
- 既存側: 2号艇レーサー3712について、`racer_accident_period_stats` の `source_kind='reconstructed'` / `rule_version='official_table_2025_05_reconstructed_v2'` / 2026-05-01期 / レース日前の最新行がなく、既存SQLは `COALESCE(..., 0)` により事故率0として扱う。
- 同race_idでは既存側が別手法 `tamagawa_13_weak_sashi2_exa` を代表選択していたが、対象事故率手法そのものは事故率0で非該当であり、代表選択を差分原因とは断定しない。
- 原因: `data-source`。勝ち筋の事故率は過去365日 `race_results.remarks` の事故件数÷出走数×100、既存は審査期の復元事故点ベースで、名称が同じでも定義と欠損処理が違う。
- 推奨対応: 次ラウンドで条件名と定義を分離し、どちらを正式な「事故率」として検索UIに出すか仕様決定する。今回は修正していない。

### `kojima_12_acc3_m3_n23_exa`

- 差分: 検索のみ `20260705-16-02`。
- 勝ち筋側: 1号艇A1、2号艇全国2連率 `33.83`、3号艇事故率 `1.0582010582`、3号艇全国2連率 `35.63`、3号艇モーター2連率 `37.93`。結果は2連単 `1-2`、払戻350円。
- 既存側: 3号艇レーサー4809の上記審査期・source/rule行が存在せず、事故率0として非該当。
- 原因: `data-source`。結果・払戻の解釈差ではなく、購入前事故率の定義差。
- 推奨対応: 同上。`kachisuji-bug` とは判定しない。

### `edogawa_13_acc2_n23_m3_exa`

- 差分: 検索のみ `20260713-03-12`, `20260726-03-12`。
- `20260713-03-12`: 勝ち筋の2号艇事故率 `0.8474576271`。既存審査期行は存在せず0。2号艇全国2連率 `32.67`、3号艇全国2連率 `29.07`、3号艇モーター2連率 `37.14`。結果は `5-2` で不的中。
- `20260726-03-12`: 勝ち筋の2号艇事故率 `0.8810572687`。既存審査期行は `period_end=2026-07-25`、事故率 `0.1724137931`、事故点10で閾値0.5未満。2号艇全国2連率 `34.29`、3号艇全国2連率 `32.14`、3号艇モーター2連率 `35.0`。結果は `4-6` で不的中。
- 原因: `data-source`。2件とも既存定義では閾値未満、勝ち筋の365日事故件数率では閾値以上。
- 推奨対応: 事故率の単位・窓・欠損時の意味を条件JSONに明示できる設計を検討する。今回は修正していない。

### `a1_ace_motor_123_corr_tri`

- 既存のみ `20260703-11-07`: 4号艇平均STが生の `race_entries` とas-ofの双方でNULL。既存は `float(value or 9.9)` により9.9として `>=0.17` を通すが、勝ち筋は条件列NULLを除外する。
- 検索のみ `20260719-21-05`: 会場21（芦屋）。既存条件は会場 `(5,7,12,21,24)` を除外するが、検索JSONは単一会場指定しかなくNOT-INを表現できないため、部分再現JSONでは除外を省いた。
- どちらも買い目 `1-2-3` は不的中だったため、N=18・的中4・回収率128.3%が偶然同じになった。
- 原因: `condition-gap`（会場NOT-IN未対応とNULLの扱い差）。
- 推奨対応: 条件JSONに会場除外と明示的なNULL方針が追加されるまでB分類を維持する。今回は修正していない。

## 3. 再現不可（分類C）

以下57手法は中核条件を検索JSONで表現できず、**数値突合は検証していない**。

| 中核の未対応条件 | 手法key | 件数 |
|---|---|---:|
| 合成fit/dash/stretch/turnスコア | `tokoname_coursefit_boat2_win`, `tokoname_coursefit_boat3_general_win`, `biwako_coursefit_boat4_gap10_general_win`, `shimonoseki_coursefit_boat2_win`, `biwako_coursefit_boat4_gap5_general_win`, `biwako_coursefit_boat4_rank1_general_win`, `biwako_coursefit_boat4_gap10_all_win` | 7 |
| 180日ST分散・2コース率・差し率による壁スコア | `nov_wall_break_31_41_exa`, `marugame_wall_hold_123_tri`, `miyajima_wall_break_31_41_exa`, `july_wall_hold_12_exa`, `shimonoseki_late_wall_hold_12_exa`, `hamanako_wall_hold_12_exa`, `miyajima_wall_hold_123_132_tri`, `g23_wall_hold_12_exa`, `tamagawa_late_wall_hold_123_132_tri` | 9 |
| コース別まくり/まくり差し勝利数と合算率 | `kiryu_win4_ace_kimarite_late`, `amagasaki_win3_ace_kimarite_late`, `amagasaki_win3_ace_kimarite_m40`, `amagasaki_win3_ace_kimarite_no_rain`, `amagasaki_win3_ace_kimarite_late_no_rain`, `amagasaki_win3_ace_kimarite_all`, `naruto_win4_ace_kimarite_all`, `naruto_win4_ace_kimarite_no_rain`, `naruto_win3_ace_kimarite_late_no_rain`, `ashiya_win4_ace_kimarite_no_rain` | 10 |
| 直線/周回タイム順位 | `fukuoka_ex12_b_exa`, `fukuoka_tri124_c`, `fukuoka_123_late_foot_tri`, `omura_124_original_t5_tri` | 4 |
| 数値潮位差・潮位レンジ・波高 | `miyajima_tide_132_tri`, `gamagori_tide_132_tri`, `marugame_tide_123_tri`, `fukuoka_tide_132_tri`, `gamagori_123_general_practical_tri`, `gamagori_13_exa` | 6 |
| 専用/180日ローリング履歴・展示改善量 | `gmkf_132_tri`, `shimonoseki_123_tri`, `tsu_124_tri`, `omura_123_tri`, `omura_132_tri`, `omura_13_exa`, `tokuyama_13_exa`, `tsu_123_tri`, `suminoe_123_tri`, `ashiya_boat4_exa` | 10 |
| 一般戦/G2-G3区分とT-5以外を含むオッズ規則 | `g23_optb_tri`, `toda_123_tri`, `kojima_123_tri`, `gamagori_123_tri`, `naruto_123_tri`, `karatsu_132_tri` | 6 |
| その他の中核未対応（展示順位・2コース過去勝率・厳密区分） | `amagasaki_143_tri`, `amagasaki_13_exa`, `tokuyama_12a_exa`, `tamagawa_13_weak_sashi2_exa`, `tsu_143_tri` | 5 |

## 4. 検証範囲

- `ROI_STRATEGIES`: コードから94手法を取得。`adopted_strategies.md` の「合計79本」と食い違う。仕様に従いコードを正とした。
- 分類: A 4 / B 33 / C 57。
- 実行: A 4/4、B 2/33、C 0/57。突合手法数は6。
- 未実行: Bの残り31手法とC全57手法、合計88手法は**検証していない**。
- 未実行B: `amagasaki_12_acc3_fl3_exa`, `amagasaki_dent3_makuri4_41`, `ashiya_13_pts2_m23_exa`, `biwako_dent2_makuri3_31`, `edogawa_132_weak4_t5_tri`, `edogawa_a_accident4_12_exa`, `edogawa_late_dent2_makuri3_31`, `hamanako_12_pts3_m23_exa`, `hamanako_14_exa`, `heiwajima_13_acc2_late_exa`, `karatsu_123_weak4_t5_tri`, `kiryu_13_fl2_n23_exa`, `kojima_124_tri`, `kojima_13_exa`, `marugame_123_late_weak4_t5_tri`, `marugame_123_tri`, `marugame_123_weak4_t5_tri`, `marugame_13_pts2_m23_exa`, `omura_13_acc2_fl2_m23_exa`, `omura_14_exa`, `shimonoseki_132_tri`, `shimonoseki_a_accident4_13_exa`, `suminoe_124_weak3_t5_tri`, `tamagawa_123_fl3_n3_30_m2_35_tri`, `toda_a_accident2_13_exa`, `toda_dent2_makuri4_41`, `tokoname_123_late_exst_tri`, `tokoname_12_late_a_exa`, `tokoname_14_winter_exa`, `tokuyama_123_tri`, `tri134_acc2_ex3_tri`。
- 対象期間: 2026-07-01〜2026-07-31。既存側・検索側へ同じ両端包含日付を渡した。実行時間を有界にしつつ、31日全日・9,624レースのschema v4 as-ofカバレッジがある期間として固定した。期間外は検証していない。
- 突合対象race_id: 既存側23件、検索側27件、両側和集合28件（手法別延べは既存23 / 検索27）。
- ローカルDB範囲: `boatrace.db` と `kachisuji_search.db` はともに557,617 race_id、2016-06-13〜2026-08-16。検索DBの対象月はschema v4 9,624件。

## 5. 検証方法・安全性

1. `src.web.app.create_app` をimportし、`member_strategy` viewをunwrapしてclosureから `ROI_STRATEGIES`、`BET_UNIT_MAP`、`_l4_daily_stats` を取得した。既存判定ロジックは写経していない。
2. `DATABASE_URL` をimport前に空文字へ固定し、`db_connect` をローカルSQLiteの `mode=ro` + `PRAGMA query_only=ON` 接続へ置換した。
3. `_l4_daily_stats(..., force_full_scan=True)` の既存signal dumpから、同一race_idで回収率最大の代表手法選択後のrace_id・的中・払戻を取得した。
4. 勝ち筋側は `search_roi` を実行した。race_id付与クエリは同モジュールの `_compile_conditions` をimportして使い、N・的中・回収率が公開集計と完全一致しない場合は異常終了する。
5. DBは終始読み取り専用。既存 evaluator のキャッシュ保存SQLはSQLiteにより `attempt to write a readonly database` として拒否され、DBファイルのSHA-256・サイズ・mtimeを前後で確認する。Supabase、ネットワーク、サーバー、scheduler、deploy、pushは使用していない。

## 6. テストと制約

- `tests/test_crosscheck_roi_strategies.py`: 読み取り専用拒否、分類94件の完全性、race_id付与集計の一致、払戻計算、失敗時dump削除、race_id集合不一致を一致扱いしないことを検証。
- 既存 evaluator はapp factory内ローカル関数で直接importできないため、importしたview closureから再利用した。この制約を理由にロジックを再実装していない。
- 事故率差は定義差が実データで説明できるため `data-source` とした。どちらかのバグとは断定していない。
- `kachisuji-bug` / `legacy-bug` と分類した事例は0件。したがって、バグ根拠としての生データ提示対象はない。不一致調査の監査材料として上記race_id・entries・results・payoutは記載した。
