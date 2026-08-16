# 勝ち筋サーチ Step 11 実装結果

実施日: 2026-08-16

## 実装概要

- `asof_race_features` を schema version 5 に更新した。
- 事故指標を「審査期・既存ROI互換」と従来の「過去365日」に分離した。
- `venue` は整数または1〜24の整数配列（OR）を受け付ける。空配列・範囲外・非整数はエラーとした。
- 確定オッズの全期間復元はできなかったため、レース全体の指標は `t5_odds_favorite`（最新T-5minスナップショットにある全3連単買い目の最低正数オッズ）として実装した。買い目オッズ条件とは独立している。
- 16方位から相対風向を導出するロジックと24会場マスタを追加した。根拠不足の会場方位は推測せず `null` とした。

## 事故率: 既存ROIから読み取った選択規則

正としたのは、読み取り専用の `src/web/app.py` にある事故型3手法の日次ROI SQLである。踏襲した規則は次のとおり。

1. `racer_accident_period_stats` を `source_kind='reconstructed'` かつ `rule_version='official_table_2025_05_reconstructed_v2'` に限定する。
2. レース月が5〜10月なら当年5月1日、11〜12月なら当年11月1日、1〜4月なら前年11月1日を `period_start` とする。
3. 同じ `period_start`・source・ruleの全体から、`period_end < race_date` を満たす最大の `period_end` を1つ選ぶ。当日行以降は使わない。
4. その全体スナップショットに選手行があれば `accident_rate` / `accident_points` を採用する。選手行が無い場合は、既存SQLの `LEFT JOIN` + `COALESCE(..., 0)` と同じく両方を0とする。選手ごとの最新行へフォールバックはしない。

schema v5 の列は次の意味になった。

- `bN_accident_rate`: 上記の既存ROI互換・審査期事故率。
- `bN_accident_points`: 上記の審査期事故点。
- `bN_accident_source`: 行ありは `period`、行なしの0補完は `missing_zero`。
- `bN_accident_rate_365d`: 旧 `bN_accident_rate` の365日事故備考件数率。

既存の schema 4 以下の特徴DBは、列追加時に旧 `bN_accident_rate` を `bN_accident_rate_365d` へ退避し、旧列をNULLにして意味の混在を防ぐ。全期間の審査期事故率は再生成後に入る。

保存済み手法には `conditions_schema_version` を追加した。既存行はversion 4、新規保存はversion 5である。version 4以下の保存条件にある `boats.N.accident_rate` は読み取り時に `accident_rate_365d` へ変換し、旧手法の意味を維持する。実DBの既存2手法には事故条件が無いことも読み取り専用で確認した。

## 1番人気オッズの調査結果

`data/boatrace.db` を `mode=ro` / `query_only=ON` で調査した。

- `odds_trifecta` は `race_id, combination, odds, is_final, recorded_at, snapshot_label` を持つ。
- 全レース数は557,617。
- `is_final=1` を持つレースは4,958だけで、全期間の1番人気確定オッズは復元不能だった。`snapshot_label='final'` の明示行は1,952レース、ラベルNULLのfinal行などを合わせても4,958レースに留まる。
- `snapshot_label='T-5min', is_final=0` は107,110レースにあり、確定オッズより大幅に広い。

したがって `final_odds_favorite` は作らず、仕様のフォールバックどおり `t5_odds_favorite` を採用した。各買い目について最新 `recorded_at` のT-5行を選び、そのレースの正数オッズの最小値を使う。0は未発売・無効値として除外する。払戻÷100は使用していない。値が無いレースはNULLで、条件使用時は `condition_null` 除外となる。

2026-07-01〜07-31のサンプル再生成では4,932レース中1,044レースに値が入り、範囲は2.4〜22.1倍だった。UIには「レース全体の人気帯（T-5・1番人気）」と明記し、締切前に確定しない傾向分析用情報で、当日照合はpendingになる旨を常時表示した。

## 風向きマスタと判定規約

`master/stadium_orientations.json` は1〜24の全会場を列挙し、「1マークから2マークへ進むホームストレッチの方位角（北0度・時計回り）」を格納する形式にした。

今回の実装はネットワーク禁止であり、リポジトリ内には公開水面レイアウトから絶対方位を裏付ける引用可能な資料が無かった。`src/web/app.py` の桐生・浜名湖・徳山の値も、追い風方向の経験的／best-effort seedであってホームストレッチ絶対方位ではない。桐生の既存検証資料も絶対方位は別途確認が必要と明記している。このため、値を変換・推測せず、全24会場を低確度として `null` にした。

null会場: 桐生、戸田、江戸川、平和島、多摩川、浜名湖、蒲郡、常滑、津、三国、びわこ、住之江、尼崎、鳴門、丸亀、児島、宮島、徳山、下関、若松、芦屋、福岡、唐津、大村。

各エントリの `basis` に、nullとした根拠を記録した。公開資料をネットワーク利用可能な別作業で確認できた会場から、角度と出典を追記する設計である。

導出規約は次のとおりで、境界をテストで固定した。

- BOATRACE風向コードは「風が吹いてくる方角」とし、1=北、以後22.5度ずつ時計回り、17=無風。
- `theta = wind_from_bearing - travel_heading` を -180〜180度へ正規化する。
- `abs(theta) <= 45` は向かい風。
- `abs(theta) >= 135` は追い風（135度を含む）。
- それ以外でtheta正は `横風(右)`、負は `横風(左)`。
- 風速0またはコード17は `無風`。方位nullでそれ以外はNULL。

実サンプルでは方位値を推測していないため、`wind_dir` 非NULLの197レースは無風判定のみである。

## Step 10 クロスチェック再実行

期間はStep 10と同じ2026-07-01〜07-31。schema v5でサンプル再生成後、既存スクリプトを変更せず指定3手法だけ再実行した。

| 手法 | 既存ROI | 検索 | レースID差分 | 結果 |
|---|---:|---:|---:|---|
| `tamagawa_13_acc2n30_m3_40_exa` | N=0 / hits=0 / ROI=0.0% | N=0 / hits=0 / ROI=0.0% | 0 | 一致 |
| `kojima_12_acc3_m3_n23_exa` | N=0 / hits=0 / ROI=0.0% | N=0 / hits=0 / ROI=0.0% | 0 | 一致 |
| `edogawa_13_acc2_n23_m3_exa` | N=0 / hits=0 / ROI=0.0% | N=0 / hits=0 / ROI=0.0% | 0 | 一致 |

Step 10で検索側だけ存在した1件、1件、2件が既存ROI事故率へ統一したことで除外され、既存ROI側の数字を変更せず一致した。

## サンプル生成・整合性

- 2026-07-01〜07-31の4,932レースだけをschema v5で再生成した。全期間再生成は実施していない。
- 100件サンプル検証: chronology error 0、事故率を含むfeature mismatch 0。
- `kachisuji_search.db`: `PRAGMA quick_check = ok`。
- 元 `boatrace.db`: SHA-256 `543EC0EA8D36D429E9E7281EC70DB2693F25B689B53B4C865FCB3C1AFAB16970`、サイズ10,365,046,784 bytes、mtime不変。
- ビルダーは既存の結果／払戻不整合7件（同着順ギャップ3件、勝ち単勝払戻欠損4件）を警告した。4,932行は全件挿入され、Step 11列の構築例外は無かった。数字や払戻は補作していない。

## テスト結果

- Python compilation: pass。
- Step 11/Kachisuji unit・contract: 187/187 pass。
- main E2E（Step 11 UIケースを含む）: 56/56 pass。
- round3 E2E: 3/3 pass。
- 全non-E2E suite: 921/923 pass。

全体suiteの残り2件はStep 11開始前からhandoffに記録済みの別件である。

1. `test_db_pk_map_parity`: `odds_snapshot` の共有PK-map未登録。
2. `test_graceful_db_degradation`: 本番race-detailのstale-header期待に対しHTTP 400。

いずれも今回の変更許可範囲外であり、`src/web/`変更禁止を守って未修正とした。Step 11/Kachisuji対象テストは全てグリーンで、E2E終了後に8090/8091のリスナーは残っていない。

## 次の必須作業

リンが `data/kachisuji_search.db` の全期間再生成を実行する必要がある。再生成前のschema 4行は旧365日値を専用列へ退避済みだが、新しい審査期事故率・事故点・出典、T-5 1番人気帯、相対風向は全期間には入っていない。
