# 作業指示書: 進入変更スナップショットの定期実行を復活 (Codex CLI 用)

作成: 2026-08-19 / 発注: リッキー / 診断・検品・管理: リン (Claude)
テスト: .venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e
(現状 1155 passed, 1 skipped。割らないこと)

## 症状と原因 (リン診断済み・確定)

レース一覧の「!」進入注意バッジ (`entry-change-watch-badge`) の判定元データ
`racer_entry_change_snapshots` が **2026-08-12 を最後に更新停止**。

原因: 2026-08-14 のコミット **3b1bfbb "Remove production-disabled regular cron paths"** で
無効化経路を整理した際、`scripts/render_regular_scheduler.py` から
```
-    ok &= run_entry_change_snapshot(today)
-    ok &= run_entry_change_snapshot(tomorrow)
```
の2行が**巻き添えで削除**された。関数 `run_entry_change_snapshot` (413行) と
`entry_change_snapshot_row_count` (393行) は残っているが**どこからも呼ばれていない**。
`task_runs` の `render_entry_change_snapshot` も 2026-08-12 00:11 の1件が最後
(races=155 rows=595 build_ok=True で成功していた)。

影響: 進入癖の統計が7日以上古いまま。該当13選手の判定に使われる出走数・変更率が
更新されず、新しく該当条件を満たした選手が拾われない。

## やること

### [必須1] 定期実行の復活
`run_entry_change_snapshot` を夜間フェーズから再び呼ぶ。呼び出し位置は、同種の
日次派生データ生成 (`run_roi_daily_self_heal` 内の `run_derived_start_stats` /
`backfill_accident_dent_daily_cache` 付近, 1243-1266行) と同じ夜間の流れに合わせる。
- 対象日は削除前と同じく **today と tomorrow の2本**
- **失敗しても他の夜間ジョブを巻き込んで全体を failure にしない**こと
  (このデータは無くてもサイトは動く。`ok &=` で全体を落とす形にしない。
   個別に task_runs へ success/failure を記録し、アラートは既存経路で)
- 既存の `record_task("render_entry_change_snapshot", ...)` の記録形式は維持

### [必須2] 停止の再発検知
同じ「呼び出しだけ消える」事故を再発させないため:
- `tests/test_source_regression.py` に **`run_entry_change_snapshot` が
  render_regular_scheduler 内から実際に呼ばれていること**を確認する静的テストを追加
  (定義だけで呼び出しゼロなら fail する形)
- 可能なら、スナップショットが N 日以上古い場合に警告を出す仕組みを
  既存の system_status / cron alert 経路に追加 (閾値は 3 日を提案)

### [必須3] 空白期間の埋め戻し
8/13〜8/19 のスナップショットが欠けている。**バックフィルの手順**を作業ログに記載する
こと (コマンド例)。実行はリンが行うため、**Codex は本番DBに書き込まない**。

## 絶対ルール
- origin/main へ push 禁止・デプロイ禁止 (リンが実施)
- **本番 Supabase への書込み禁止** (調査は読取りのみ。バックフィルもリンが実行)
- 採用ROI戦略の判定結果を変えない / 進入注意の**判定閾値は変更しない**
  (ENTRY_CHANGE_MIN_STARTS=100 / HIGH_RATE=0.20 / INNER_MIN_RATE=0.10 は現状維持。
   閾値調整は発注者が別途判断する)
- render.yaml の cron 構成を増やさない
- 今日入れた 58955c9 (signal refresh 最適化)・7223440 (preflight)・b49ca49 (security) を壊さない
- 作業ログ: reports/entry_change_snapshot_restore_work_log_20260819.md

## 受け入れ条件
- [ ] `run_entry_change_snapshot` が夜間フェーズから today/tomorrow で呼ばれる
- [ ] 失敗時に他ジョブを巻き込まない (テストで保証)
- [ ] 呼び出し消失を検知する回帰テスト追加
- [ ] 鮮度警告の実装 (または見送り理由を作業ログに)
- [ ] 8/13-8/19 バックフィル手順のコマンドを作業ログに記載
- [ ] pytest 1155+ passed / push なし / デプロイなし / 本番DB書込みなし
