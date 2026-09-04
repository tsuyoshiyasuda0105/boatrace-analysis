# 作業指示書: P1-1 フェーズA — 戦略ロジックの棚卸し + 特性化テスト (Codex CLI 用)

作成: 2026-08-15 / 発注者: リッキー / 検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本のみ)
背景: `src/web/app.py` (21,849行) に戦略評価ロジックが散在・多重実装され、**残る最大の
バグ発生源** (TODO #5 / P1-1)。将来 `src/strategies/` へ外出しするが、**いきなり動かすと
挙動が変わって ROI 判定が壊れる**。そこで本フェーズは「**コードは1行も動かさず**、現在の
挙動を golden テストで固定 + 全評価関数を棚卸し」だけを行う。抽出は次フェーズ。

## このフェーズの目的 (これだけ)

1. app.py の戦略/シグナル評価ロジックの**完全な棚卸し表**を作る。
2. 現在の出力を**特性化テスト (characterization test)** で固定する。
   → 次フェーズで外出ししたとき、このテストが割れなければ「挙動不変」を保証できる。

## 絶対ルール

1. **origin/main への push 禁止**。ローカル main まで。
2. **本フェーズでは app.py および既存モジュールのロジックを一切変更しない。**
   追加してよいのは `tests/` 配下と `reports/` 配下のみ。`src/strategies/` もまだ作らない。
3. ROI 戦略・予測・DB スキーマ・render.yaml 不可侵。
4. テスト: `.venv/Scripts/python.exe -m pytest tests/ -q` — 現行 main は **670 passed**。
   1件も割らない。
5. 作業ログ `reports/p1_1_phaseA_work_log_20260815.md` に成果を記録。
6. コミットは 2〜3個目安 (棚卸し / 特性化テスト)。

## やること

### A. 棚卸し (inventory)

`src/web/app.py` を読み、**戦略・シグナル判定に関わる関数と定義**を洗い出して
`reports/strategy_inventory_20260815.md` に表でまとめる。最低限の列:

- 関数名 / 定義行 / トップレベルかネストか (どの親関数の中か)
- 役割 (例: 「大村23 optB の採用可否」「女性混入除外」「単勝1.3閾値」)
- 依存する入力 (preds / conditions / odds / grade / stadium / racers.gender 等)
- **同じ判定が他所にも実装されていないか** (例: `market_signals_for_date` /
  `_l4_daily_stats` / `_l4_races_for_date` / `scripts/sync_l4_summary_to_supabase.py` /
  `src/evaluation/l4_strategy.py` の重複箇所を突き合わせる)

既知の中心関数 (行番号は自分で再確認): `market_signals_for_date` (~7796),
`_l4_daily_stats` (~15908), `_l4_races_for_date` (~20204),
`_evaluate_g23_optb_signal` (~11083), `_evaluate_candidate_134_signal` (~9989),
`_evaluate_tsu_suminoe_123_signal` (~7508), `_evaluate_shimonoseki_123_signal` (~7623),
`_detect_niche_signals` (~3831), `_allow_market_signal_with_female` (~10229),
`_prefer_adopted_signal_over_general200` (~10245), `_pick_best_market_signal` (~10094)。
CLAUDE.md「整合性を保つべきファイル群」節も参照。

### B. 特性化テスト (現挙動の固定)

**外から観測できる戦略判定の出力**を、固定入力で pin する golden テストを作る。
狙いは「関数のリファクタで出力が1ビットでも変わったら落ちる網」。

推奨アプローチ (実装しやすい方を選ぶ / 併用可):

1. **エンドツーポイント寄り**: 既存のテスト基盤 (`tests/` の Flask test client や
   `market_signals_for_date` を呼ぶ既存テスト、`conftest.py` の DB fixture) を調べ、
   **固定した過去日 (例: 2026-05-06 等、DB に予測とオッズが揃う日)** に対する
   `market_signals_for_date` の戻り値 (採用/観察の race_id 集合・バッジ種別・
   単勝/三連単フラグ) を JSON 化し golden として保存・比較する。
2. **関数単体寄り**: 上記の純粋な判定関数 (`_evaluate_g23_optb_signal` 等) に
   代表的な入力ベクトル (採用/非採用/観察/B除外/女性混入/雨 など各分岐を1つずつ)
   を与え、返り値を pin する。ネスト関数で直接呼べない場合は、モジュールとして
   import できる形での**参照テストの足場だけ用意**し、呼べないものは棚卸し表に
   「単体テスト不可 (ネスト)」と明記する (無理に app.py を改造して公開しない)。

**重要**: テストは現在の出力を「正」として固定するだけでよい (挙動の正しさの判断は
しない)。既に DB を使う既存テストの流儀 (fixture/monkeypatch) に合わせる。
DB が要る場合は既存 conftest の仕組みを使い、無ければ最小の in-memory/fake で代替。
**実行に外部ネットワークや本番 DB を要するテストは作らない。**

### C. どうしても pin できない箇所

DB 依存が重すぎて固定困難な判定は、テストにせず**棚卸し表に「特性化未カバー・要注意」**
と赤字で列挙する (次フェーズでの危険地帯マップになる)。

## 受け入れ条件

- [ ] `reports/strategy_inventory_20260815.md` に評価関数の棚卸し表 + 重複実装の対応表
- [ ] 主要な戦略判定の特性化テストが追加され、現行出力を pin している
- [ ] pin 不能な箇所は棚卸し表に明記
- [ ] `pytest tests/ -q` 全件 green (670 を割らない) / app.py 無改変 / push なし / 作業ログ

## 検品 (リンが実施)

「app.py が本当に無改変か」「棚卸しが網羅的か (重複実装の突き合わせがあるか)」
「特性化テストが実際に分岐を握っているか」「全テスト green か」を照合する。
次フェーズ (実際の外出し) はこの網が揃ってから発注する。
