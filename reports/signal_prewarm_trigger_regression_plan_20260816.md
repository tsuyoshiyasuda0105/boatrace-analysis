# 作業指示書: シグナル prewarm の再計算がcronでブロックされる回帰の修正 (Codex CLI 用)

作成: 2026-08-16 / 発注者: リッキー / 検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本のみ)
現行 main: 本番 `26ea21b`。テスト基準 `--ignore=tests/e2e --ignore=tests/round3_e2e` (1010 passed)。
**緊急度: 高** — 放置すると夜間に誤アラームメール (1時間1通) + 台帳汚染が続く。

## 背景 (リンが特定した回帰)

直前コミット `a3f81c2` の market-signals 再計算ガード
(`_effective_market_signals_recompute`: `BOATRACE_TASK_TRIGGER in EXPENSIVE_RECOMPUTE_TRIGGERS`
のときだけ recompute 許可) が、**正当な cron のシグナル再計算までブロック**している。

連鎖:
1. `boatrace-exhibition-detail-cron` は `BOATRACE_TASK_TRIGGER="render-exhibition-detail-refresh"`
   (`scripts/refresh_race_detail_after_exhibition.py:22`) で起動。
2. その中の `refresh_market_signals_if_needed` が
   `scripts/prewarm_strategy_pages.py --mode signals` を subprocess 実行。
3. prewarm は `/api/market-signals?...&recompute=1` を叩き、レスポンスヘッダが
   **`X-Boatrace-Cache: recomputed` であることを検証** (`prewarm_strategy_pages.py:48`)。
4. しかし親から継承した trigger `render-exhibition-detail-refresh` は
   **`EXPENSIVE_RECOMPUTE_TRIGGERS` (=render-prewarm/render-cron/render-maintenance/
   render-detail-prewarm/db-maintenance) に含まれない**ため、新guardが recompute を拒否。
   → キャッシュ/last-good を返し、ヘッダは "recomputed" にならない。
5. prewarm 検証失敗 → `signal_summary.ok=False` → exhibition cron `succeeded=False`
   → `_notify_failure` → **メール + インシデント台帳** (誤アラーム)。

**市場シグナル計算は本来 cron/prewarm では実行されるべき**もの。人間リクエストだけ
ブロックする意図だったが、cron の正当な recompute まで巻き込んだのが回帰。

## ゴール

**正当な prewarm/cron のシグナル再計算は許可**しつつ、**人間リクエストの重い再計算は
引き続きブロック** (メール洪水根絶を維持) する。exhibition cron が成功で終わり、
誤アラームメール/台帳記録が止まる。

## 絶対ルール

1. **origin/main へ push 禁止** (ローカル main まで)。
2. **人間リクエストで /api/market-signals が重い再計算に入らない**という直前の修正を
   壊さない (メール洪水根絶を維持)。
3. ROI・予測・DB スキーマ・render.yaml・収集は変更しない。market_signals の**数値は不変**。
4. `pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e` を割らない + 新規 green。
5. 作業ログ `reports/signal_prewarm_trigger_regression_work_log_20260816.md`。コミット1〜2個。

## やること (どちらか、より安全・的確な方)

**方針A (推奨・的確): prewarm 側で許可トリガーを明示**
- `scripts/prewarm_strategy_pages.py` が**自プロセスの `BOATRACE_TASK_TRIGGER` を
  許可済みの値 (例 `render-prewarm`) に設定**してから market-signals を叩く
  (prewarm は本来 prewarm 操作なので `render-prewarm` が自然)。
  これで親 cron の trigger に関係なく、**prewarm の recompute だけが許可**され、
  人間リクエストのガードには影響しない。
- 他に `--mode signals` を呼ぶ経路 (refresh_race_detail_after_exhibition 等) が
  同様に正しい trigger で動くことを確認。

**方針B (代替): 許可トリガー集合に追加**
- `render-exhibition-detail-refresh` を market-signals recompute の許可対象に含める。
  ただし `EXPENSIVE_RECOMPUTE_TRIGGERS` は race-detail 側とも共有なので、**人間リクエストの
  ガードを緩めない**範囲で行うこと (market-signals 専用の許可集合を別に持つ方が安全なら
  そうする)。

**いずれの方針でも**:
- **人間の /api/market-signals?recompute=1 は依然として重い再計算に入らない**ことを
  テストで担保 (直前の回帰防止テストを壊さない/強化)。
- exhibition cron 経由のシグナル prewarm が `X-Boatrace-Cache: recomputed` を得て
  検証を通り、`signal_summary.ok=True` になることを担保。

## テスト (`tests/` に追加)

- prewarm/cron 経路 (許可トリガー) では market-signals が recompute される
  (ヘッダ recomputed / signal prewarm 検証が通る)。
- **人間リクエスト (トリガー無し) では依然 recompute されない** (メール洪水根絶の維持)。
- exhibition cron の `succeeded` 判定が、正当なシグナル refresh 成功時に True になる
  (誤 failure にならない) — 可能なら fake で。

## 受け入れ条件

- [ ] 正当な cron/prewarm のシグナル再計算が許可され、exhibition cron が誤 failure しない
- [ ] 人間リクエストの重い再計算ブロック (メール洪水根絶) は維持
- [ ] market_signals の数値不変 / 誤アラームメール・台帳記録が止まる
- [ ] `pytest ... --ignore=e2e --ignore=round3_e2e` 維持 + 新規 green / push なし / 作業ログ

## 検品 (リンが実施)

「正当な cron 再計算が通るか」「人間リクエストのブロックが維持されているか
(メール洪水根絶)」「exhibition cron が成功で終わるか」「数値不変か」「テスト green か」
を照合。デプロイは発注者承認後。
