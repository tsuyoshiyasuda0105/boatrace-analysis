# 勝ち筋サーチ Step 18 実装結果 — 発掘フィードバックとマイ手法の育成表示

実施日: 2026-08-16

## 変更ファイル

- `src/kachisuji_web/templates/search.html`
  - 検索結果の発掘ステータス判定・表示
  - マイ手法の成長バー、検証量レベル、経過表示
- `src/kachisuji_web/static/kachisuji.css`
  - 発掘ステータスと育成表示のスタイル
  - 金脈表示の1回限りの控えめな演出と `prefers-reduced-motion` 対応
- `tests/e2e/test_kachisuji_e2e.py`
  - ランク、安全性、警告維持、動きの抑制、育成境界、既存判定バッジのE2E回帰
- `tests/test_kachisuji_web.py`
  - 表示層の必須要素に対するHTML回帰
- `docs/kachisuji_gamify_step18_result_20260816.md`
  - 本結果報告

`src/kachisuji_web/app.py`、`src/search/roi_search.py`、`src/search/strategies.py`、本番 `src/web/` は変更していない。

## ランク判定の実装箇所

`src/kachisuji_web/templates/search.html` の `discoveryStatusHtml(data)`。既存の検索APIが返す `n`、`roi`、`roi_ci_low` だけで次の順に判定する。

1. `n < 30`: 🌫️ 未探査
2. `n >= 100 && roi >= 100 && roi_ci_low >= 100`: ⛏️ 金脈発見
3. `roi >= 100`: ✨ 鉱脈の気配
4. `roi >= 70`: 🪨 要検証
5. 上記以外: ⛰️ ハズレ鉱区

## n<30で金脈を出さない保証

- 判定を排他的な `if / else if` とし、最初の分岐を必ず `n < 30` にした。ここで未探査を確定するため、後続の金脈条件へ到達しない。
- 金脈分岐自体にも独立して `n >= 100` を必須条件としている。優先順位と金脈条件の二重ガードで保証する。
- E2Eで `n=12`、`roi=999`、`roi_ci_low=500` の極端な高ROIケースを描画し、「🌫️ 未探査」が表示され「金脈発見」が表示されないことを検証した。
- 同じケースに既存警告 `n<30: 偶然の可能性が高い` を加え、未探査表示と警告が同時に残ることも検証した。

## マイ手法の育成表示

`strategyGrowthHtml(item)` が既存の `forward.n` と `races_until_verdict` を表示に利用する。N30未満は0〜30の成長バーと残りレース数を表示し、N30以上では既存の判定バッジを主役としてバーを表示しない。レベルは `forward.n` の境界 30 / 100 / 300 だけで切り替える。

画面上に「レベルは検証したレース数を表します。成績は判定バッジで確認します。」と明記し、検証量と成績評価の役割を分離した。既存の 🟢 / ⚪ / 🔴 判定バッジは維持している。

## 使用した語彙

- 発掘ステータス: 発掘、未探査、金脈発見、鉱脈の気配、要検証、ハズレ鉱区、検証、検証継続、判断材料
- 育成表示: 育成中、判定可能、実績あり、長期検証済み、成長、検証量、レース消化、判定待ち
- アイコン: 🌫️、⛏️、✨、🪨、⛰️、🌱、🌿、🌳、🏔️、🟢、⚪、🔴

射幸性を煽る禁止語や金額を煽る表示は追加していない。

## テスト結果

- `tests/test_kachisuji_web.py`: 38 passed
- Step 18対象E2E: 10 passed
- 全非E2E: 1010 passed, 1 skipped
- 全メインE2E（ポート8090）: 77 passed
- Round 3 E2E（ポート8090を明示）: 3 passed
- `git diff --check`: passed
- E2E終了後のポート8090: closed

各pytest実行では、既存の `.pytest_cache` に対するWindows ACL由来の `PytestCacheWarning` が1件出たが、テスト結果には影響していない。

## 既知の制限

- 発掘ステータスは表示層の補助情報であり、検索計算や既存warningsの代替ではない。
- 鉱脈の気配は統計的な確証を示さず、N100未満では残り検証量、N100以上では95%信頼区間下限が基準未達であることを明示する。
- レベルはフォワード検証量だけを表し、成績の良さは表さない。成績評価は既存の判定バッジが担う。
