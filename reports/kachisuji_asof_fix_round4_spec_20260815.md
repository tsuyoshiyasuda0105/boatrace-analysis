# 勝ち筋サーチ Round 4 修正仕様書 — 結果・払戻の正当性（BUG-R3-001/002/003）

作成: 2026-08-15 リン（Claude Code）/ 発注先: Codex
背景: Round 3 で ROI 計算の根幹に関わる Critical バグを検出。
`data/boatrace.db` の `race_payouts` には、同一 race_id / bet_type に対して
**古い（誤った）組合せと正しい組合せが両方残っている**レースが 2016-06〜2020-11 に多数あり、
`asof_builder` が払戻行を順不同で1行に潰すため、誤った結果・払戻を採用していた。

## 根本原因と正しい設計（リン確認済み）

権威ある情報源は `race_results.finishing_position`（着順）である。実例 `20160613-13-01`:
- race_results: 1着=1号艇, 2着=4号艇, 3着=5号艇（着順は正しく1組だけ存在）
- race_payouts: `trifecta 3-2-1=6130`(誤/古) と `trifecta 1-4-5=1550`(正) が両方存在

→ **結果組は payout の行順ではなく、必ず `race_results` の着順から導出**し、
   **払戻はその導出した組合せに一致する payout 行から取得**する。これで 001 は解消する。

## 絶対的な制約（違反禁止）

1. 変更してよいファイル: `src/features/asof_builder.py`, `src/search/roi_search.py`（同着の的中判定が
   必要な場合のみ）, 対応する `tests/`（`test_asof_builder.py`, `tests/test_kachisuji_correctness_round3.py` の
   xfail 解除等）。他の既存プロダクトファイルは変更禁止。
2. `data/boatrace.db` は読み取りのみ。`data/kachisuji_search.db` の**全期間再生成はリンが後で実行する**
   ので、Codex は小規模サンプル（対象レースを含む数日〜数週間）を一時DBに生成して検証すること。
   本番 `data/kachisuji_search.db` を書き換えてよいが、巨大再生成は不要（サンプル検証で足りる）。
3. スキーマ変更は**pre-launch につき自由**。ただし schema_version を 4 に上げ、既存の読み取りは
   壊さないこと。
4. ネットワーク・スケジューラ・実サーバー起動しっぱなし・push 禁止。
5. コミットは main へのローカルコミット1つ。メッセージ: `Fix result/payout derivation from finish order (kachisuji round 4)`。

## 修正内容

### BUG-R3-001（Critical）: 結果を着順から導出
- 各レースの `result_tansho` = 着順1位の艇番、`result_nirentan` = "1位-2位"、`result_sanrentan` = "1位-2位-3位"
  を **`race_results.finishing_position` から導出**する。
- 対応する払戻は、導出した組合せ文字列に**一致する** `race_payouts` 行から取得する
  （win/exacta/trifecta の combination 表記を確認し、正規化して突合。行順に依存しない）。
- 一致する払戻行が無い/複数ある異常時は、当該レースの該当券種を NULL とし warning。黙って誤値を入れない。
- `race_results` に複数版がある場合の扱いも調査し、着順が一意に決まらないレースは該当券種 NULL + warning。

### BUG-R3-002（High）: 同着（dead heat）
- 着順1位（または2位/3位）が複数艇のレースでは、公式に的中となる組合せが複数存在する。
- 結果列を単一スカラーのまま誤って1つに潰さない設計にする。推奨: 各券種の**勝ち組合せを配列（JSON）**で
  保持する列（例 `result_tansho_json` = `["1","2"]`, `payout_tansho_json` = `{"1":130,"2":380}`）を追加し、
  通常レースは要素1個、同着はN個とする。
- `roi_search` の的中判定を「ユーザーの買い目が勝ち組合せ集合に含まれれば的中、その組合せの払戻を使用」に変更。
  既存の単一列（result_tansho 等）は表示用に**着順由来の代表値**として残してよい。
- 同着は稀（2021年以降で計25件程度）。まず正しく動くことを優先。

### BUG-R3-003（Medium）: 履歴原料の整合性ガード
- 決まり手の集計は **`finishing_position == 1` の行のみ**から数える（非勝者に残る決まり手を無視）。
- 事故回数の集計は、事故コードと着順が矛盾する古い行を弾く（判定基準を docstring に明記）。
- これにより 2016-2020 の壊れた原料が前日率に混入しない（現行は 2023-05 開始で実害は隔離済みだが、
  再生成後も安全にするための恒久ガード）。

## テスト

1. `20160613-13-01` 等の既知レースで、結果組・払戻が着順由来の正しい値（単勝1=110, 2連単1-4=350,
   3連単1-4-5=1550）になる。
2. Round 3 の xfail（BUG-R3-001/002/003 に紐づく7件）が **XPASS/pass** になる。
3. 同着レースで、複数の勝ち組合せそれぞれが的中判定され、正しい払戻が使われる。
4. 決まり手集計が finishing_position==1 のみを数える（合成フィクスチャで非勝者決まり手を無視）。
5. 未来情報遮断が引き続き保たれる（既存の verify / 不変性テスト）。
6. 既存の `test_asof_builder` `test_roi_search` `test_strategies` `test_kachisuji_web` 全件グリーン。

## DoD

1. 上記テスト全件グリーン、Round 3 xfail 解消。
2. サンプル再生成で `20160613-13-01` を含む期間を作り、独立SQL突合で結果・払戻一致を確認。
3. 結果レポート `docs/kachisuji_asof_fix_round4_result_20260815.md` に:
   変更ファイル / スキーマ差分 / 001-003の対応 / サンプル突合結果 /
   **全期間再生成が必要である旨の明記**（リンが実行する）/ 既知の残課題。
4. ローカルコミット1つ（push しない）。
