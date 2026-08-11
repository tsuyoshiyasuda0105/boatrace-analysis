# Render Cron Overview

## 2026-08-12 canonical program flow

- `boatrace-program-bootstrap-cron`: every five minutes from 23:00 through 09:59 JST.
  - 23:30: acquire tomorrow's official B program.
  - 00:10: acquire today's Open API program and run the cross-source gate.
  - Failed source attempts persist a 15, 30, then 60 minute backoff in `task_runs`.
  - Retries write only venues still marked incomplete. The daily source endpoint may still return one full payload.
  - A PostgreSQL advisory lock prevents overlapping bootstrap runs.
  - 06:30: one final forced recovery attempt.
  - 07:30: unresolved source state is written to `system_status` for the admin warning.
- `boatrace-race-detail-cron`: 06:45 JST. It exits before cache generation unless the source gate is ready.
- `boatrace-odds-cron`, `boatrace-regular-cron`, and `boatrace-exhibition-detail-cron`: every five minutes from 08:00 through 22:59 JST.
- The dedicated bootstrap owns program acquisition. The regular cron only consumes the persisted source-gate success before tags, pages, TOP snapshots, or ROI signals are generated.
- Blueprint schedules are part of the deployment. A code-only service deploy does not prove schedule synchronization.

最終確認日: 2026-08-09
参照元: `render.yaml`

## 対象 cron

### `boatrace-odds-cron`
- 実行間隔: 毎分
- 実行時間帯: 08:00-22:59 JST
- 実行コマンド: `python scripts/odds_scheduler_render.py --no-jitter`
- 役割:
  - オッズ系スナップショットを取得する
  - Render 本番では負荷を下げるため、通常レースは `T-5min` と `T-1min` だけ取得する
  - 主要レースだけ `T-1d` も追加で取得する
- 内部処理:
  - `scripts/odds_scheduler.py` の `find_due_snapshots()` で「今この時点で取得すべき race_id / snapshot_label」を抽出する
  - `src.collectors.odds.collect_one_race()` を呼び、対象レースの3連単オッズを保存する
  - `T-5min` の取得直後は `_auto_paper_trade()` を呼び、紙トレード記録も更新する
- 主な入出力:
  - 入力: `races`, 既存 `odds_trifecta`
  - 出力: `odds_trifecta`, paper trade 系データ
- 負荷が出やすい点:
  - 日中は毎分起動される
  - 取得対象レースが同時刻に多いと、外部オッズ取得の待ち時間が増える
  - DB では `odds_trifecta` への追記回数が最も多い部類

### `boatrace-regular-cron`
- 実行間隔: 5分ごと
- 実行時間帯: 08:00-22:59 JST
- 実行コマンド: `python scripts/render_regular_scheduler.py`
- 役割:
  - レース結果の軽量ポーリング
  - `check_post_run_integrity.py` による結果整合性チェック
  - `evaluate_start_predictions.py` による予測評価更新
  - TOPページ用スナップショット更新
  - 時間帯に応じた補助処理
    - 日次の事故率セルフヒール
    - 終了後の翌日データ準備
    - ROI 日次再計算のセルフヒール
  - 注意:
    - 展示情報取得とレース詳細の展示後更新はこの cron では行わない
    - それらは `boatrace-exhibition-detail-cron` に分離されている
- 通常の 5 分ループで実際に走るもの:
  - `scripts/poll_results.py --no-jitter`
    - 結果確定済みレースの反映
  - `scripts/check_post_run_integrity.py --date <today> --stage post-result`
    - 結果反映後の整合性確認
  - `scripts/evaluate_start_predictions.py --date <today>`
    - 予測と結果の照合評価
  - `run_top_page_snapshot(..., lightweight=True)`
    - TOP の軽量スナップショット生成
- 条件付きで走るもの:
  - 朝 06:00-09:00 JST:
    - `run_morning()`
    - `scripts/backfill_official.py --start <today> --end <today>`
    - `scripts/daily_collect.py --date <today>`
    - `scripts/render_cache_predictions.py --date <today>`
    - `scripts/check_data_quality.py`
    - 事故率セルフヒール
    - TOP の完全スナップショット生成
  - 朝の補完:
    - `run_morning_catchup_if_needed()`
    - 朝タスク成功フラグがあっても実データ不足なら再実行する
  - 毎時 00-04 分:
    - `run_hourly()`
    - `scripts/sync_l4_summary_to_supabase.py --recent-days 3`
    - `scripts/check_data_quality.py`
    - `scripts/agent_monitor.py --quiet`
  - 07:30-07:34 JST:
    - `run_accident_self_heal()`
    - 事故率の再構築、外部照合、タグ再生成を行う
  - 23:30 以降:
    - `run_nightly()`
    - 当日締め処理と翌日準備を行う
  - 特定スロット:
    - `run_roi_history_slot()`
    - 重い ROI 履歴ページ再生成を live loop から分離して実行する
- `run_nightly()` の中身:
  - 当日:
    - `backfill_official.py`
    - `daily_collect.py`
    - `sync_l4_summary_to_supabase.py --recent-days 5`
  - 翌日:
    - `backfill_official.py --start <tomorrow> --end <tomorrow>`
    - `daily_collect.py --date <tomorrow>`
    - `render_cache_predictions.py --date <tomorrow>`
    - `build_racer_entry_change_stats.py --date <tomorrow>`
    - `prewarm_race_detail_tags.py --date <tomorrow>`
    - `prewarm_strategy_pages.py --mode signals --date <tomorrow>`
  - 日次集計:
    - `aggregate_start_prediction_metrics.py --date <today>`
    - `backfill_accident_dent_daily_cache.py --recent-days 400`
    - `run_accident_full_refresh(today)`
    - `run_db_maintenance()`
- 主な入出力:
  - 入力: `races`, official 系データ, results, 各種派生テーブル
  - 出力: TOP スナップショット, 予測キャッシュ, ROI 系キャッシュ, task_runs
- 負荷が出やすい点:
  - 役割が広い
  - 5分ごとに起動されるので、1回が長引くと次の tick と重なりやすい
  - 朝・夜は補助タスクが増え、最も重くなりやすい

### `boatrace-race-detail-cron`
- 実行間隔: 1日1回
- 実行時刻: 04:00 JST
- 実行コマンド: `python scripts/prewarm_race_detail_data.py`
- 役割:
  - 当日レース詳細の安定データを朝前にまとめて構築する
  - 主な対象:
    - 選手詳細
    - モーター履歴
    - 表示タグ
    - レース詳細ページ本体キャッシュ
  - 展示後の変動データは含めない
- 実行順:
  - race_id 一覧取得
  - 選手系データ構築
  - モーター履歴キャッシュ生成
  - 表示タグ生成
  - HTML ページキャッシュ生成
  - `check_post_run_integrity.py` による morning scope の検証
- 主な入出力:
  - 入力: `races`, モーター関連データ, 選手関連データ
  - 出力:
    - `motor_history_v9:<race_id>:<boat>`
    - race detail タグ
    - race detail HTML キャッシュ
    - cron 実行ログ
- 負荷が出やすい点:
  - 1日分全レース × 6艇分のモーター履歴をまとめて作る
  - レース詳細ページ全件の事前生成も行う
  - ただし 04:00 JST の単発実行なので、日中の応答速度には有利

### `boatrace-exhibition-detail-cron`
- 実行間隔: 5分ごと
- 実行時間帯: 08:00-22:59 JST
- 実行コマンド: `python scripts/refresh_race_detail_after_exhibition.py`
- 役割:
  - 展示情報のライブ収集
  - original exhibition 系データの補完
  - 展示反映後に必要なレース詳細キャッシュ更新
  - モーター詳細や展示系表示の更新を定期反映する
  - regular cron と役割を分離し、重複取得を防ぐ
- 内部処理:
  - `collect_live_exhibition()`
    - `scrape_beforeinfo_live` を使って展示前情報を取得する
    - original exhibition 欠損レースを補完する
  - `refresh()`
    - 更新期限に達したレース詳細を抽出する
    - `race_conditions` キャッシュを書き直す
    - 各艇の `motor_history_v9:<race_id>:<boat>` を再生成する
    - race detail ページキャッシュを invalidate する
  - `refresh_market_signals_if_needed()`
    - 展示更新後に必要な場合だけ派生指標を再生成する
- 主な入出力:
  - 入力: beforeinfo live, original exhibition, `races`
  - 出力:
    - `race_original_exhibitions`
    - race conditions キャッシュ
    - motor history キャッシュ
    - detail page 再描画対象
    - task_runs / cron_run_log
- 負荷が出やすい点:
  - 外部取得と detail 再生成の両方を持つ
  - 5分ごとに走るため、対象レースが多い時間帯は重くなりやすい
  - ただし重複起動防止と最小間隔ガードが入っている

### `boatrace-accident-external-check-cron`
- 実行間隔: 1日1回
- 実行時刻: 05:10 JST
- 実行コマンド: `python scripts/check_external_accident_snapshot.py`
- 役割:
  - 外部の事故率基準データと内部集計結果を照合する
  - 事故率系の静かなズレや欠損を検知する
  - 事故タグや関連 ROI 戦略の前提データの監視に使う
- 内部処理:
  - InterQ の外部事故率ページと JS を取得する
  - 期間情報を解析する
  - 外部ランキング行をパースする
  - 内部事故率スナップショットと比較する
  - 結果を `racer_accident_external_snapshots` 系へ保存する
- 主な入出力:
  - 入力: 外部 HTML / JS, 内部事故率集計
  - 出力: 外部比較結果テーブル, 監査ログ
- 負荷が出やすい点:
  - 毎日1回なので頻度負荷は低い
  - 外部サイト応答に引きずられる可能性はある

## 時刻メモ

- `* 23,0-13 * * *` は JST で 08:00-22:59
- `*/5 23,0-13 * * *` は JST で 08:00-22:59 の5分ごと
- `0 19 * * *` は JST で 04:00
- `10 20 * * *` は JST で 05:10

## 補足

- 現在の Render cron は、重い処理を1本に集めず、`odds` / `regular` / `race-detail` / `exhibition-detail` / `accident-check` に分割している
- 負荷面では、毎分の `boatrace-odds-cron` と、5分ごとの `boatrace-regular-cron` / `boatrace-exhibition-detail-cron` が日中の主な定常負荷になる
- 特に Render の health check 失敗につながりやすいのは、web 本体と同じ DB に対して cron が短時間に集中するケース
- 現状の構成では、TOP 表示速度に効くのは主に次の3つ
  - `boatrace-race-detail-cron` による事前生成
  - `boatrace-exhibition-detail-cron` による差分更新
  - `boatrace-regular-cron` 側での軽量 TOP スナップショット更新
