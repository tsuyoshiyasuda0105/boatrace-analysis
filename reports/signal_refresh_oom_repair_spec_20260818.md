# 作業指示書: 市場シグナル夜間更新のOOM障害 修理 (Codex CLI 用)

作成: 2026-08-18 朝 / 発注: リッキー / 診断・検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本のみ / 本番DBは Supabase Postgres)
テスト基準: `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e`

## 背景 (8/18 朝のデータチェックで判明)

本日8/18のレース詳細ページ144/144は正常だが、**市場シグナル(本日候補=L4)の夜間更新が失敗**し、
本番トップに本日候補が出ない。連鎖でメンテ窓が degraded 終了した。

### 確定している事実
- `render_signal_refresh_06_4/_6/_8` が3回連続失敗。**task_runs の detail が空**で原因不明。
- `render_maintenance_snapshot_v1` 失敗: `{"signals_ok": false, "top_ok": false, "attempt_count": 3}`。
- `system_status`: `maintenance_window ended degraded: snapshot, integrity`。
- `system_status`: 8/17 23:30 に **`/api/market-signals` が 254.7秒**・slow request 384件。
- `page_html_cache` の `market_signals:last-good:2026-08-18` が **0件**。

### 診断 (リンの仮説 — Codex は裏取りしてから直すこと)
- 実処理は `scripts/render_regular_scheduler.py::run_signal_refresh_slot` →
  `run_py(["scripts/prewarm_strategy_pages.py","--mode","signals","--date",today], timeout=1800)`。
- `prewarm_strategy_pages.py --mode signals` は Flask test client で
  **`/api/market-signals?date=...&recompute=1`** を叩く。この recompute が全144レースで
  **カスケード再計算 (モデル読込, ~1.7秒/レース=254秒)** を行い、**Render 512MB でOOM killされている**
  疑いが濃厚 (昨夜直したページ生成OOMと同クラスだが別経路)。
- `run_signal_refresh_slot` 末尾は `record_task(task, today, "success" if ok else "failure")` で
  **失敗理由を detail に残していない** → これが「空エラー」の正体。

## 絶対ルール (厳守)

1. **origin/main へ push 禁止** (ローカル main まで)。デプロイもしない (リンが検品後に手動)。
2. **本番DB (Supabase) への書込み・スキーマ変更はしない**。調査は読み取りのみ。
3. `pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e` を**割らない**。関連に回帰テスト追加。
4. 既存の L4 判定ロジック整合 (CLAUDE.md「整合性を保つべきファイル群」) を壊さない。
5. 作業ログ `reports/signal_refresh_oom_repair_work_log_20260818.md`。コミットは論理単位で分割可。
6. Layer1/2/3 の収集ロジックや scheduler の稼働タスク一覧は勝手に増減しない。

## やること (優先順位順)

### [必須1] 失敗理由を必ず detail に記録する (診断可能化)
- `run_signal_refresh_slot` (および同種の `run_py` 呼び出し) で、失敗時に
  **サブプロセスの exit code と stderr 末尾 (数百字)** を `record_task(..., "failure", detail=...)` に残す。
- OOM kill の場合 (exit code が負 / 137 等) は「OOM疑い」と明示。
- これは根本原因が何であれ**常に有益**なので最優先。回帰テストを1本追加。

### [必須2] market-signals 再計算のメモリ削減 (OOM 恒久対策)
- まず **OOM 仮説を裏取り**: `--mode signals` を**ローカルでメモリ計測**しながら実行
  (`tracemalloc` かプロセス RSS ログ)。ピーク RSS を作業ログに記録。
- 対策の第一候補は **永続 predictions からのシグナル組み立て** (昨夜のページ修正 `_CachedOnlyPredictor`
  と同じ哲学: 学習モデルを import/ロードしない)。`/api/market-signals?recompute=1` の計算が
  `predictions` テーブルの確率を再利用できるなら、カスケード再実行を避ける。
- それが困難なら **バッチ処理 + `gc.collect()` + native stderr 抑制** でピーク RSS を下げる
  (ページ修正と同手法)。目標: Render 512MB 内で確実に完走。
- **出力 (market signals の内容) が従来と一致すること**を確認 (少数レースで before/after 突合)。
  L4 候補の判定結果を変えてはいけない (整合性が最優先)。

### [必須3] signals 失敗時も degrade してトップを空にしない
- `run_snapshot_phase` は `signal_ok` が false だと top_ok も false になり丸ごと失敗する。
- signals 更新が落ちても、**前日までの last-good か、当日 predictions ベースの最小シグナル**で
  トップページ snapshot を出せるよう degrade 経路を用意 (本日候補が古い旨のフラグは付けてよい)。
- 「本番トップが真っ白 / 本日候補ゼロ」を避けることが目的。

### [任意4] ガーディアンにも signals バックアップを追加
- ローカルのガーディアン (夜間バックアップ) がページに加え **market-signals last-good も**
  先回り生成するようにし、Render cron が落ちても本日候補が出るようにする。
  (どのスクリプトがガーディアンか不明なら作業ログに質問を残す。無理はしない)

### [任意5] 8/17 の結果不完全 1レースを特定
- `post_run_result: result rows incomplete 1/168 closed races` の該当 race_id を特定し、
  結果取込を1レース分リトライ (本番書込みが必要ならリンに委譲、コードでの再取得手順のみ用意)。

## 受け入れ条件

- [ ] signal-refresh 失敗時に exit code + stderr 末尾が task_runs.detail に残る (回帰テスト付き)
- [ ] `--mode signals` のピーク RSS を計測・記録し、512MB 内で完走する対策を実装
- [ ] market signals の出力が従来と一致 (L4 判定を変えない / before-after 突合を作業ログに)
- [ ] signals 失敗時もトップが本日候補ゼロで真っ白にならない degrade 経路
- [ ] `pytest ... --ignore=e2e --ignore=round3_e2e` 維持 / push なし / デプロイなし
- [ ] 作業ログ: 診断裏取り (RSS 計測結果)・変更点・残課題・コミットID

## 検品 (リンが実施)

「OOM 仮説の裏取りができているか」「market signals の出力が従来と一致するか (L4 を変えていないか)」
「push/デプロイしていないか」「テストが緑か」を確認。問題なければリンが本番へ手動デプロイし、
翌朝の signal-refresh 成功を確認して発注者へ報告する。
