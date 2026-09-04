# 作業指示書: P1-1 フェーズB — 特性化済み純粋評価関数の外出し (Codex CLI 用)

作成: 2026-08-15 / 発注者: リッキー / 検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本のみ)
前提: フェーズA完了。棚卸し `reports/strategy_inventory_20260815.md` と
特性化テスト `tests/test_strategy_characterization_phase_a.py` (17ケース/9関数) が
現挙動を pin 済み。現行 main は **687 passed**。

## このフェーズの原則 (超重要)

**「挙動を1ビットも変えない」リファクタ**である。ロジックの改善・統合・簡約は**一切しない**。
やるのは「app.py に埋まっている評価関数を `src/strategies/` へ物理的に移し、app.py 側は
import して呼ぶだけ」にする移設。フェーズAの golden 値が変わったら**失敗**とみなす。

## スコープ (これだけ。広げない)

**フェーズAで特性化テスト済みの純粋関数だけ**を外出しする。これらは Phase A で
「app.py を変えずに単独 exec で動く」ことが実証済み＝安全に切り出せる。対象:

| 関数 | 定義行(要再確認) |
|---|---|
| `_detect_niche_signals` | ~3831 (top-level, 既に import 可能) |
| `_compute_tetsuban` | ~9780 |
| `_evaluate_l4_general_200` | ~9955 (常に None の互換 no-op) |
| `_evaluate_candidate_134_signal` | ~9989 |
| `_pick_best_market_signal` | ~10094 |
| `_allow_market_signal_with_female` | ~10229 |
| `_prefer_adopted_signal_over_general200` | ~10245 |
| `_evaluate_g23_optb_signal` | ~11083 |
| `_evaluate_general_c_signal` | ~11202 |

## 絶対にやらないこと (危険地帯 — 今回は触れない)

- `market_signals_for_date` 本体 / `_l4_daily_stats` / `_l4_races_for_date` /
  `_detect_market_inefficiency` / `_evaluate_l4` / `_evaluate_morning_l4` /
  潮位 / オリジナル展示 / 福岡展示足 / 決まり手 / current motor / 1-3系列 /
  非展示core / 2号壁 / 津・住之江 / 下関 — **棚卸しで「危険」の全て**。
- 重複実装 (`_l4_daily_stats` 側の双子ロジック等) の**統合はしない**。今回は live 側の
  評価関数を移すだけ。daily 側との一本化は将来フェーズ。
- スキーマ・ROI 数値・予測・render.yaml・cron・通知ロジックの変更。
- push (ローカル main まで)。

## 絶対ルール

1. **origin/main へ push 禁止**。
2. **挙動不変**: フェーズAの golden 期待値 (出力 dict の中身) は**変更禁止**。
   テストは「読み込み元を app.py の AST から `src/strategies/` の import へ張り替える」
   だけにする。期待値を1文字でも変えたら差し戻し。
3. 各関数が閉包 (enclosing scope) の変数・定数・ヘルパーを参照している場合、
   それらは**引数で渡す**か、共有定数として `src/strategies/` 側にも import する。
   Phase A テストの `injected_globals` が「その関数が必要とする外部名」の一覧なので、
   それを移設の入力仕様として使う。**app.py 側の呼び出しで従来と同じ値を渡す**こと。
4. app.py 側は「ネスト def を削除 → `from src.strategies... import ...` して同じ引数で
   呼ぶ」薄い置換に留める。呼び出し順・返り値の使われ方は不変。
5. テスト: `.venv/Scripts/python.exe -m pytest tests/ -q` — **687 passed を割らない**。
   特性化テストが張り替え後も同じ golden で green であること。
6. 作業ログ `reports/p1_1_phaseB_work_log_20260815.md` に、移した関数・新モジュール構成・
   app.py の差し替え箇所・閉包依存をどう引数化したか・テスト結果を記録。
7. **1関数=1コミット**目安 (最大でも小グループ)。巨大な一括コミットにしない。
   各コミット後に全テストを回し、green を確認してから次へ。

## 推奨手順 (1関数ずつ)

1. `src/strategies/__init__.py` を作る (パッケージ化)。関連する純粋評価関数を
   テーマ別モジュール (例: `niche.py`, `l4_candidates.py`, `selection.py`) に置く。
   分類に迷ったら1ファイル `src/strategies/signals.py` にまとめてもよい。
2. 対象関数を app.py から**そのままの本体で**新モジュールへ移動 (ロジック無改変)。
   閉包依存があれば署名に引数を追加 (デフォルト値で従来挙動を保つ)。
3. app.py: 旧ネスト def を削除し、モジュール関数を import。呼び出し箇所は
   従来と同じ引数で呼ぶ (閉包変数は明示的に渡す)。
4. 特性化テスト: `_load_local_function("X")` を新モジュールからの import に張り替え。
   **期待 dict は不変**。
5. `pytest tests/ -q` 全緑を確認 → コミット。次の関数へ。

## 受け入れ条件

- [ ] 対象9関数が `src/strategies/` に移り、app.py は import 呼び出しに置換
- [ ] 危険地帯には一切触れていない
- [ ] 特性化テストの**期待値は不変**のまま、読み込み元だけ新モジュールに張り替え済み
- [ ] `pytest tests/ -q` 687 passed を維持 (増減は新規テスト追加分のみ)
- [ ] push していない / 作業ログ提出 / 1関数=1コミット基調

## 検品 (リンが実施)

「移動前後で関数本体が字義的に同一か (ロジック改変が混ざっていないか)」
「特性化テストの golden が不変か」「app.py の呼び出しが従来と同じ引数か」
「危険地帯に触れていないか」「全テスト green か」を照合する。
1文字でもロジックが変わっていたら差し戻す。デプロイは発注者承認後。
