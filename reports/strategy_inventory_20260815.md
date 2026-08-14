# P1-1 フェーズA 戦略ロジック棚卸し（2026-08-15）

## 結論と数え方

`src/web/app.py` の AST を使い、次を「戦略評価関連関数」として数えた。

1. 名前が `_evaluate_` / `_detect_` で始まる関数。
2. 採用選択、女性混入ゲート、表示補正、鉄板度、雨判定、開始予測フィルタ、ROI 買い目解釈を担う明示的な補助関数。
3. 外部出力・日別集計を作る `market_signals_for_date`、`_l4_daily_stats`、`_l4_races_for_date`。

この再現可能な基準では **61関数**。内訳はトップレベル4、`create_app` 直下5（2 evaluator、market endpoint、2集計入口）、`market_signals_for_date` 内52である。単なる DB loader、型変換だけの `_to_float`、表示専用 formatter は数えていない。

## 評価関数一覧

「pin」は今回追加した特性化テストの直接対象。`未` は棚卸し済みだが今回の直接 pin 対象外、`危険` は固定 DB・時刻・履歴・request/cache 状態なしに安定 pin できない箇所を示す。

| # | 関数（定義行） | 階層 | 役割 | 主な依存入力 | 重複・対応先 | pin |
|---:|---|---|---|---|---|---|
| 1 | `_parse_market_signal_bets_for_roi` (2194) | top | 表示文字列から単勝・2連単・3連単の実行券を抽出 | `l4.bet` | `_l4_daily_stats::_parse_market_signal_bets` | 既存 |
| 2 | `_market_signal_is_inside_escape` (2215) | top | 買い目が1号艇頭か判定 | `l4.bet` | ROI表示・日別 overlay | 既存 |
| 3 | `_detect_market_inefficiency` (3631) | top | 確定払戻または朝予測から旧 +EV/L4 詳細シグナルを生成 | DB払戻、preds、grade、stadium、class | `_evaluate_l4`、`_l4_daily_stats` と旧L4判定が重複 | 危険 |
| 4 | `_detect_niche_signals` (3831) | top | 艇番・チルト・級別の大穴情報を分類 | preds、conditions.boats、tilt、class | 独立 | **pin** |
| 5 | `_evaluate_tsu_suminoe_123_signal` (7508) | `create_app` | 津/住之江 1-2-3 の採用・朝監視・展示後除外 | ctx、全国/当地率、ST、展示、motor | `_l4_daily_stats` 内の履歴集計 | 危険 |
| 6 | `_evaluate_shimonoseki_123_signal` (7623) | `create_app` | 下関 1-2-3 の採用・朝監視 | ctx、race_no、全国/当地率、展示、motor | `_l4_daily_stats` の対応カウンタ | 危険 |
| 7 | `market_signals_for_date` (7796) | `create_app` route | 指定日の採用/監視シグナル、badge、単勝/連単フラグを統合してJSON化 | request日付、DB、cache、preds、odds、conditions、現在時刻 | 日別・日別レース・cron・通知の中心重複 | 危険 |
| 8 | `_apply_start_prediction_filter_annotations` (7987) | `market_signals_for_date` | 展示後スタート予測フィルタ注記 | signal rows、予測確率差 | cached payload補正と重複 | 未 |
| 9 | `_apply_l4_reference_start_prediction_candidates` (8045) | 同上 | L4参考行を ST 上位30%候補へ昇格 | signal rows、post-head確率 | start prediction API/集計 | 未 |
| 10 | `_apply_start_prediction_filters_to_cached_payload` (8084) | 同上 | 古いcacheにも現行STフィルタを再適用 | cached JSON | #8/#9 | 未 |
| 11 | `_rain_exclusion_active` (9767) | 同上 | 締切5分前から雨除外を有効化 | close時刻、現在JST | 日別集計は確定weatherを直接除外 | 未 |
| 12 | `_compute_tetsuban` (9780) | 同上 | grade/F1/11-12R/1c80/PRO/rankを1〜5★へ圧縮 | signal dict、race_no | live/morning L4双方 | **pin** |
| 13 | `_evaluate_l4_portfolio_strong` (9839) | 同上 | 10年検証の強監視A/Bタグ | venue、grade、全国率、ST、年齢、2号motor、月、風、女性 | candidate_134 と条件軸が重複 | 未 |
| 14 | `_apply_l4_portfolio_strong` (9935) | 同上 | 基本L4へ強監視属性をoverlay | base、portfolio | live/morning/no-dataの3経路 | 未 |
| 15 | `_evaluate_l4_general_200` (9955) | 同上 | 廃止済general200互換hook（常にNone） | 旧一般戦入力 | 日別に旧counterが残存 | **pin** |
| 16 | `_apply_l4_general_200` (9961) | 同上 | 旧general200属性を基本L4へoverlay | base、general200 | #15、#20 | 未 |
| 17 | `_evaluate_candidate_134_signal` (9989) | 同上 | 候補1/3/4の重複成立を集約し最終候補を選択 | venue、grade、R、全国率、年齢、course、motor、ST、雨、女性、月 | `_candidate_134_daily_stats`、portfolio strong | **pin** |
| 18 | `_pick_best_market_signal` (10094) | 同上 | 採用手法を一般候補より優先し、同群では回収率最大を選択 | 任意signal群、採用key集合 | `MARKET_SIGNAL_ADOPTED_LEVELS` / `ROI_STRATEGY_KEYS` | **pin** |
| 19 | `_allow_market_signal_with_female` (10229) | 同上 | 女性混入時もニッチ/ROI採用/明示許可だけ通す | signal種別、女性人数 | L4 core SQLの女性全除外とは方針差あり | **pin** |
| 20 | `_prefer_adopted_signal_over_general200` (10245) | 同上 | general200と採用手法の同時成立時に採用labelを残す | selected、adopted | #16/#18 | **pin** |
| 21 | `_evaluate_boat3_trifecta_niche` (10313) | 同上 | 3-2-4/3-2-1 の確定ニッチ選択 | ctx、3号class/ST/motor、他艇比較、venue | 日別 adopted counters | 未 |
| 22 | `_evaluate_boat3_trifecta_niche_watch` (10433) | 同上 | #21 の展示前監視 | ctx（展示前入力） | #21 | 未 |
| 23 | `_evaluate_tokoname_12_late_a_exacta` (10510) | 同上 | 常滑後半A級 1-2 | ctx、R、class、motor、ST | `_l4_daily_stats` | 未 |
| 24 | `_evaluate_tokoname_14_winter_exacta` (10564) | 同上 | 常滑冬季 1-4 | ctx、月、R、4号class/motor | `_l4_daily_stats` | 未 |
| 25 | `_evaluate_tokoname_123_late_exst` (10622) | 同上 | 常滑後半展示ST 1-2-3 | ctx、R、展示ST/rank | `_l4_daily_stats` | 未 |
| 26 | `_evaluate_tokoname_123_late_exst_watch` (10675) | 同上 | #25 の朝監視 | ctx、R、class/motor | #25 | 未 |
| 27 | `_evaluate_omura_tokuyama_13_exacta` (10730) | 同上 | 大村/徳山 1-3 採用 | ctx、venue、R、class、motor、事故/展示条件 | `_l4_daily_stats` | 未 |
| 28 | `_evaluate_tri124_132_trifecta_niche` (10831) | 同上 | 蒲郡等1-3-2 / 1-4-3の確定採用 | ctx、venue、R、class、展示rank/差、motor | 日別同名counter | 未 |
| 29 | `_evaluate_tri124_132_trifecta_niche_watch` (10960) | 同上 | #28 の朝監視 | ctx（展示前） | #28 | 未 |
| 30 | `_evaluate_g23_optb_signal` (11083) | 同上 | G2/G3 optB 1-2-3採用 | venue cap、grade、A1、500-999、全国/当地率、年齢、平均/展示ST、2号motor、雨、女性 | `_l4_daily_stats` 17539付近に同条件 | **pin** |
| 31 | `_evaluate_g23_optb_watch` (11126) | 同上 | #30 の展示/オッズ前監視 | #30から展示ST・払戻を除いた入力 | #30 | 未 |
| 32 | `_evaluate_fukuoka_wind_exa_signal` (11172) | 同上 | 福岡強風時2-1 | venue、class、2号率、風速 | 日別counter | 未 |
| 33 | `_evaluate_general_c_signal` (11202) | 同上 | 一般C 1-2-3採用 | grade5、A1、B除外、L4帯、全国/当地率、1/2号motor、3号率、雨、女性 | `_l4_daily_stats` 17732付近に同条件 | **pin** |
| 34 | `_evaluate_general_c_watch` (11287) | 同上 | #33 のオッズ前監視 | #33からL4帯を除いた入力 | #33 | 未 |
| 35 | `_evaluate_omura_123_watch` (11341) | 同上 | 大村1-2-3朝監視 | R、4号当地率、2/3号率、風、展示 | 日別counter | 未 |
| 36 | `_evaluate_miyajima_fl132_signal` (11407) | 同上 | 宮島FL条件1-3-2 | ctx、FL、R、class/motor | 日別counter | 未 |
| 37 | `_evaluate_tide_tri_signal` (11458) | 同上 | 宮島/蒲郡/丸亀/福岡の潮位3連単採用・監視 | venue、天候、風波、潮位差/range/high/low | 日別 tide SQL/集計 | 危険 |
| 38 | `_evaluate_gamagori_adopted_signal` (11695) | 同上 | 蒲郡1-2-3 / 1-3採用 | ctx、grade、R、motor、展示 | 日別counter | 未 |
| 39 | `_evaluate_omura_124_original_signal` (11772) | 同上 | 大村オリジナル展示1-2-4 | race_id、venue、風、DB展示rank | `src.evaluation.omura_124_original_strategy` | 危険 |
| 40 | `_evaluate_fukuoka_exhibition_foot_signal` (11817) | 同上 | 福岡展示足の1-2 / 1-2-4 / 1-2-3 | race_id、R、風、DB展示データ | 日別展示集計 | 危険 |
| 41 | `_evaluate_current_motor_adopted_signal` (11910) | 同上 | 現モーター期の11戦略を束ねて選択 | 大型ctx（venue/R/motor/class/展示/オッズ等） | `_l4_daily_stats` の個別分岐群 | 危険 |
| 42 | `_evaluate_13_series_adopted_signal` (12166) | 同上 | 1-3系7戦略と朝監視を束ねる | 大型ctx、事故率、展示、motor、級別 | `_l4_daily_stats` の個別分岐群 | 危険 |
| 43 | `_match_ace_kimarite_win_strategies` (12587) | 同上 | まくり/差し履歴とエースmotor単勝定義を照合 | ctx、過去決まり手DB、course、class/motor | `ACE_KIMARITE_WIN_*` 定義と日別集計 | 危険 |
| 44 | `_evaluate_ace_kimarite_win_signal` (12625) | 同上 | #43の候補から回収率最大を採用 | #43結果 | #18、日別counter | 危険 |
| 45 | `_evaluate_a1_ace_motor_123_corr_signal` (12656) | 同上 | 会場補正付きA1エースmotor 1-2-3 | ctx、venue補正、A1、motor、odds | 日別counter | 未 |
| 46 | `_evaluate_accident_dent_adopted_signal` (12712) | 同上 | 事故率/ST凹みポートフォリオ | ctx、事故率/母数、ST、class/motor | `src.evaluation.accident_dent_strategy` と日別backtest | 未（既存unit有） |
| 47 | `_evaluate_non_exhibition_core_signal` (12737) | 同上 | 徳山/下関/丸亀/児島の展示非依存6戦略 | 大型prerace入力 | `_l4_daily_stats` 個別SQL/分岐 | 危険 |
| 48 | `_evaluate_exacta_niche` (12993) | 同上 | 尼崎/浜名湖/大村/下関等2連単6戦略 | venue、R、motor、全国率、ST、女性、企画、相性、grade、雨 | 日別counter | 未 |
| 49 | `_ensure_exacta_niche_display_confirmed` (13196) | 同上 | 古い採用2連単cacheへ表示確定flag補完 | signal flags | cached payload補正 | 既存 |
| 50 | `_evaluate_ashiya_boat4_lift` (13210) | 同上 | 芦屋4号A1展示上昇 4-1 | ctx、4号class/motor、展示course/rank | 日別履歴・既存source assertion | 未 |
| 51 | `_evaluate_ashiya_boat4_watch` (13301) | 同上 | #50の展示前監視 | ctx、4号class/motor、履歴 | #50 | 未 |
| 52 | `_evaluate_ashiya_4head_flow` (13424) | 同上 | 芦屋4頭全流し | ctx、4号条件、展示/事故 | 日別counter | 未 |
| 53 | `_evaluate_toda_42_flow` (13486) | 同上 | 戸田4-2全流し | ctx、4/2号条件 | 日別counter | 未 |
| 54 | `_evaluate_miyajima_boat4_watch` (13537) | 同上 | 宮島4号まくり監視 | ctx、class/motor/ST/展示 | 日別counter | 未 |
| 55 | `_evaluate_win_niche` (13618) | 同上 | 桐生2号艇単勝 | 1/3/4号予測top2差 | `_l4_daily_stats` 17520付近 | 未 |
| 56 | `_evaluate_l4` (13648) | 同上 | 確定オッズL4、F1、L4-Mid、rank/1c80/PRO | venue、grade、class、odds、全国/当地率、ST、年齢、展示、2号率、R、3号率 | daily/races/sync/alerts/legacy detail | 危険 |
| 57 | `_evaluate_morning_l4` (13970) | 同上 | 朝prob_first基準のL4候補/監視 | #56のうちprob_first中心、展示状態 | alerts morning、result scraper fallback | 危険 |
| 58 | `_boat2_wall_strategy_ok` (14224) | 同上 | 2号壁の履歴score/会場月級別/形状条件 | strategy定義、ctx、過去ST分散/course2/差し率 | `_l4_daily_stats::_boat2_wall_daily_ok` | 危険 |
| 59 | `_evaluate_boat2_wall_adopted_signal` (14288) | 同上 | 壁戦略群を評価して最良signalを選択 | `BOAT2_WALL_STRATEGIES`、ctx | 日別wall集計 | 危険 |
| 60 | `_l4_daily_stats` (15908) | `create_app` | 全L4/採用手法の日別bets/hits/pay/ROI集計とcache overlay | 広範なDB履歴、odds、結果、weather、gender、各戦略入力 | live evaluators、sync cronと最大規模で重複 | 危険 |
| 61 | `_l4_races_for_date` (20204) | `create_app` | 指定日L4レースと買い目損益を再構成 | DB odds/payout/results、grade/class、B除外、雨、女性 | `_evaluate_l4`、`_l4_daily_stats`、sync | 危険 |

## 戦略定義・集合の棚卸し

| 定義 | 場所 | 用途・注意 |
|---|---|---|
| `_LOSING_VENUES`, `_QUESTIONABLE_VENUES` | app.py:3261-3262 | 4+4会場からB除外8会場を構成。旧詳細判定にも使う。 |
| endpoint内 `EXCLUDE_B` | app.py:9765 | 上記2集合から再構成。`_evaluate_l4` / morning用。 |
| `EXCLUDE_B_VENUES` | app.py:15529 | 日別・日別レース・general C用。同じ8会場を再記述。 |
| L4 shared import | app.py:8220-8230 | `l4_rank`、1c80、PROは `src/evaluation/l4_strategy.py` に委譲済み。 |
| `STRICT_ODDS_DAILY_START` | app.py:15530 | 2026-05-30以降は払戻fallbackを許さない境界。 |
| `BOAT2_WALL_STRATEGIES` | app.py:15604 | live評価とdaily評価が同じ定義tupleを読むが、判定関数は別実装。 |
| `MARKET_SIGNAL_ADOPTED_LEVELS` | app.py:20682以降 | 採用優先・表示対象集合。途中で複数回連結される。 |
| `ROI_STRATEGIES` / `ROI_STRATEGY_KEYS` | app.py:20825-20943 | ROI表示・女性混入例外・採用優先の実質的な採用集合。最後に `MARKET_SIGNAL_ADOPTED_LEVELS` を上書き。 |
| `COURSE_FIT_STRATEGIES` | `src/evaluation/course_fit_strategy.py` | appへimport。live/backtestの共通定義。 |
| `ACCIDENT_DENT_STRATEGIES` | `src/evaluation/accident_dent_strategy.py` | appへimport。live/backtestの共通定義。 |

## 重複実装の対応表

| 判定 | リアルタイム/UI | ROI・履歴 | cron/別経路 | 重複リスク |
|---|---|---|---|---|
| L4本流（A1、B除外、500-999、雨・女性、grade/F1） | `market_signals_for_date::_evaluate_l4` / `_evaluate_morning_l4` | `_l4_daily_stats` / `_l4_races_for_date` | `scripts/sync_l4_summary_to_supabase.py::compute_summary`、`scripts/send_l4_alerts.py::{detect_l4_alerts,detect_morning_l4_candidates}`、`result_scraper.py` fallback | **最重要**。共有moduleはrank/閾値の一部だけで、候補抽出SQLと分岐は5経路以上。 |
| B除外8会場 | `_LOSING_VENUES + _QUESTIONABLE_VENUES`、endpoint内`EXCLUDE_B` | `EXCLUDE_B_VENUES` | `l4_strategy.EXCLUDE_VENUES`、syncの`EXCLUDE_B`、result scraperのlocal tuple、odds schedulerにも再記述 | 集合の片側更新でUI/ROI/収集対象がずれる。 |
| G2/G3 optB | `_evaluate_g23_optb_signal` / watch | `_l4_daily_stats` 17539付近の`g23_match` | なし | venue別motor cap、ST、年齢、女性、雨、払戻帯が二重実装。 |
| general C | `_evaluate_general_c_signal` / watch | `_l4_daily_stats` 17732付近 | なし | `l4_band_ok` fallback、B除外、motor閾値、雨・女性が二重実装。 |
| candidate 1/3/4 | `_evaluate_candidate_134_signal` | `_candidate_134_daily_stats` | なし | overlap時の優先順位（最後のcand4）が別集計とずれる危険。 |
| 1c80 / L4 PRO | shared `is_1c80` / `is_l4_pro` をliveが利用 | `_l4_daily_stats`もsharedを利用 | sync cron内に `_is_1c80` / `_is_l4_pro` を再実装 | syncだけ閾値・欠損処理が漂流し得る。 |
| start prediction filter | endpointの3補助関数 | cache overlay | start prediction側の生成処理 | cache存続中に定義が変わるため互換補正が必要。 |
| current motor / 1-3系列 / 非展示core / exacta niche | liveの束ね evaluator | `_l4_daily_stats` の巨大な個別分岐・counter群 | 一部は独立evaluation module | key、入力欠損の既定値、優先順位がずれやすい。 |
| 2号壁 | `_boat2_wall_strategy_ok` | `_boat2_wall_daily_ok` | 共通tuple `BOAT2_WALL_STRATEGIES` | 定義は共通でもfeature算出と判定関数が二重。 |
| 採用集合 | `_pick_best_market_signal` 内の巨大set | `ROI_STRATEGIES` / `ROI_STRATEGY_KEYS` | templatesにも表示key列挙 | 同じ採用keyが複数箇所。関数内setは生成後の`ROI_STRATEGY_KEYS`を直接使っていない。 |
| L4通知 | UI JSON | ROI cache | `scripts/send_l4_alerts.py` | CLAUDE.md記載の `src/notifications/send_l4_alerts.py` は存在せず、実体は `scripts/send_l4_alerts.py`。 |

## 特性化未カバー・要注意（危険地帯マップ）

- <span style="color:red">`market_signals_for_date` 全体</span>: request context、複数cache、現在時刻、DB方言、odds snapshot、予測、展示、事故・潮位履歴を同時に要求する。固定過去日の自己完結fixtureがないため、外部DBなしのendpoint goldenは今回作成不能。
- <span style="color:red">`_l4_daily_stats` / `_l4_races_for_date`</span>: 数千行のSQL・cache overlay・結果確定状態に依存し、既存fixtureは当該全スキーマと固定過去日を持たない。ここが次フェーズ最大の移植リスク。
- <span style="color:red">`_detect_market_inefficiency`</span>: `race_payouts`を読む事後判定と朝予測判定が一関数に混在し、旧L4実装も抱える。最小fakeではSQL結果以外の履歴整合を保証できない。
- <span style="color:red">津/住之江・下関</span>: endpoint外側で構築・更新する履歴seedと展示ctxに依存。評価関数だけの入力固定では履歴生成側をpinできない。
- <span style="color:red">潮位、オリジナル展示、福岡展示足、決まり手、current motor、1-3系列、非展示core、2号壁</span>: DB由来の派生ctxや過去時点限定履歴が広く、単体入力を捏造すると実際のloaderとの結合を固定できない。
- 直接pin可能だが今回未対象の小型pure evaluatorも表の「未」に残した。次フェーズでは抽出単位ごとに同じtest-only loaderで追加pinし、巨大entrypointを一度に移動しないこと。

## 今回の特性化テスト対応

`tests/test_strategy_characterization_phase_a.py` は、app.pyを変更せずASTから対象のネスト関数定義を1個ずつ読み込む足場を使用する。外部ネットワーク、本番DB、ローカルDBへ接続しない。

- G2/G3 optB: 採用dictを完全一致し、会場外・1000円上限・motor cap超過・雨・女性混入を固定。
- candidate 1/3/4: 3候補同時成立時にcand4を主結果とし、全matched key/labelを残す現挙動を固定。
- general C: 採用dictを完全一致し、B除外・雨・女性混入を固定。
- best signal選択: 高回収の一般候補より低回収でも採用keyを優先し、matched metadataは全候補を保持する現挙動を固定。
- 女性混入gate: generic拒否、ニッチ・ROI key・明示許可は通す現挙動を固定。
- general200 overlay: 採用labelへ譲りつつ旧overlay metadataを保持する現挙動を固定。
- 鉄板度: 全bonusが立っても5★へ圧縮する現挙動を固定。
- retired general200 evaluator: 常にNoneの互換no-opを固定。
- チルトニッチ: 5号艇/A2/tilt3.0の完全な出力dictを固定。
