# 勝ち筋サーチ Round 4 修正結果

実施日: 2026-08-16

## 変更ファイル

- `src/features/asof_builder.py`
- `src/search/roi_search.py`
- `tests/test_asof_builder.py`
- `tests/test_roi_search.py`
- `tests/test_kachisuji_correctness_round3.py`
- `docs/kachisuji_asof_fix_round4_result_20260815.md`
- `docs/handoff.md`（作業記録）

## スキーマ差分

- 新規生成行の `schema_version` を 3 から 4 へ更新した。
- `asof_race_features` に次の6列を加算的に追加した。既存schema 2/3行は保持され、新列はNULLとなる。
  - `result_tansho_json`, `payout_tansho_json`
  - `result_nirentan_json`, `payout_nirentan_json`
  - `result_sanrentan_json`, `payout_sanrentan_json`
- 結果JSONは勝ち組合せの配列、払戻JSONは組合せをキー、100円当たり払戻額を値とするオブジェクトである。
- 従来の単一結果・払戻列は着順由来の代表値として維持した。schema 2/3は従来列、schema 4はJSON集合を使ってROIを計算する。

## BUG-R3-001（Critical）

- 結果組を `race_payouts` の行順から取得する処理を廃止した。
- `race_results.finishing_position` を唯一の結果順位ソースとし、単勝・2連単・3連単の勝ち組を導出する。
- payoutの全角・異体ハイフン等を正規化したうえで、導出組に一致する行だけを採用する。
- 一致行が0件または複数件、払戻が不正、着順が不足・矛盾する場合は、その券種の単一列とJSON列をNULLにしてwarningを出す。
- 現行 `race_results` は `(race_id, boat_number)` 主キーで複数版を保持しないことを読み取り専用SQLで確認した。将来の入力で同じ艇に相反する着順が複数来た場合も曖昧としてNULL化するガードを実装した。

## BUG-R3-002（High、同着）

- 同着順位内の順列を展開し、公式に的中する全組合せをJSON配列へ保存する。
- 各勝ち組に一致する払戻をJSONオブジェクトへ保存する。
- `roi_search` はschema 4でユーザー買い目が勝ち組集合に含まれるかを判定し、的中した組合せ固有の払戻を使用する。
- schema 2/3の従来読み取りは維持した。

## BUG-R3-003（Medium、履歴整合ガード）

- 決まり手は `finishing_position == 1` の行だけを集計する。
- 事故は事故コードがあり、かつ着順が数値1〜6ではない行だけを集計する。事故コードと通常着順が同居する古い矛盾行は除外する。
- 判定基準を `_load_histories` のdocstringに明記し、非勝者決まり手・数値着順事故を含む合成フィクスチャで確認した。

## サンプル再生成・独立SQL突合

- `data/boatrace.db` をSQLite read-only URIで開き、2016-06-13の108レースだけを一時DBへ生成した。
- 独立SQLは `race_results` から1〜3着を再構成し、その組合せに一致する `race_payouts` を相関サブクエリで取得して、一時DBの値と比較した。
- `20160613-13-01` の結果:
  - schema version: 4
  - 単勝: `1 = 110`、一致
  - 2連単: `1-4 = 350`、一致
  - 3連単: `1-4-5 = 1550`、一致
- 108レース中1件（`20160613-04-09`）は着順由来の単勝 `2` に一致する払戻行がなく、仕様どおり単勝をNULL化してwarningを1件記録した。
- 同着実データ `20251211-17-01` でも、単勝・2連単・3連単それぞれの複数公式組合せと組合せ別払戻がJSONにすべて保持されることを確認した。

## テスト結果

- 必須バンドル: 132 passed
  - `tests/test_asof_builder.py`
  - `tests/test_roi_search.py`
  - `tests/test_strategies.py`
  - `tests/test_kachisuji_web.py`
  - `tests/test_kachisuji_correctness_round3.py`
- Round 3の旧strict-xfail 7件は、BUG-R3-001が3件、002が3件、003が1件のすべて通常passとなった。xfail/xpassは0件。
- Pythonコンパイルと `git diff --check` は成功した。

## 全期間再生成と既知の残課題

`data/kachisuji_search.db` の既存schema 2/3行にはRound 3で確認した古い誤結果・誤払戻が残るため、**本修正の反映には全期間再生成が必要**である。全期間再生成はリンが実行する。Codexは本番DBを書き換えず、サンプル生成だけを行った。

既知の残課題は、ソース側で着順由来の勝ち組に一致する払戻行が欠落しているレースが存在することである。この場合は誤値を代入せずNULL + warningとなるため、ROI対象から安全に除外される。
