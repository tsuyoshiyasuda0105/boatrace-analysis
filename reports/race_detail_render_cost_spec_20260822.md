# 作業指示書: レース詳細ページ生成が本番でだけ 12-16 秒かかる原因の特定と解消

作成: 2026-08-22 / 発注: リッキー / 診断・検品: リン (Claude)
テスト: .venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e
(現状 1197 passed, 1 skipped。割らないこと)

## 症状 (リン実測済み・確定)

/race/<race_id> の**再生成 (キャッシュ期限切れ時)** が本番で 12-16 秒。
これが全ての障害の根本。今日はこれを回避する対症療法を重ねたが解決しなかった。

**実測データ:**
- ローカル (本番 DB 使用、`?recompute=1` + BOATRACE_ALLOW_EXPENSIVE_WEB_RECOMPUTE=1):
  **20 クエリ / 2.27 秒**
- 本番 slow_request 記録: 全体 13-16 秒、**db_queries=0 / db_time_ms=0.0**
  (BOATRACE_MEASURE_SQL が本番で無効なため 0 表示。DB 時間の実値は不明)
- 15 秒前後で値が揃う (13.2 / 13.3 / 13.4 / 14.7 / 14.9 / 15.0 / 15.1 / 16.1)
- 外部 HTTP 呼び出しは無し (grep 済み)
- DB 側は健全: pg_stat_activity は active=1 / idle 15、長時間クエリ無し
- プール計測: failures=0 / peak_concurrent=1 / max_hold_ms=296 / max_wait_ms=2571

**キャッシュ状態との相関 (18:07 実測):**
- 作り置きが新鮮なレース → 0.6-0.9 秒で正常表示
- 作り置きが期限切れのレース → 502 または 50 秒超

## 調べてほしいこと (推測で直さず、まず計測)

1. **本番で 12-16 秒の内訳を取る**
   - 本番でも SQL 回数と DB 時間が記録されるようにする
     (BOATRACE_MEASURE_SQL 相当を本番の再生成経路でだけ有効化する等。
      常時計測が重いなら再生成時のみ)
   - レンダリング / DB / モデル読込 / テンプレート の各段階の所要を
     slow_request か task_runs に残す
2. **ローカル 2.27 秒 → 本番 12-16 秒 の差の正体**
   候補 (これに限らない):
   - CPU: 本番は 1 コア。ローカルより遅い処理が支配的か
   - 同時実行: gunicorn 2 worker x 4 threads で GIL 競合
   - 起動コスト: 再生成は test_client で自プロセスに再入する
     (`_rebuild_race_detail_page_in_background`)。この再入が重くないか
   - 15 秒付近で揃う点が「何かのタイムアウト待ち」を示唆。該当する
     タイムアウト設定がないか (statement_timeout, lock_timeout,
     BOATRACE_DB_POOL_TIMEOUT_SEC, gunicorn timeout など)
3. 判明した支配要因を削る。**削れない場合は「なぜ削れないか」を作業ログに書く**

## 制約
- push 禁止・デプロイ禁止・本番 Supabase 書込み禁止
- 採用ROI戦略の判定結果を変えない / render.yaml を変更しない
- 展示データ (race_previews / race_original_exhibitions) の反映内容を減らさない
  (展示タイム・展示ST・進入コース・チルト・天候風波は予想の核心)
- 直近の a6b2397 / 32fdd04 / 6ce6fa5 / a70ba80 / 6620bdc / 2409a40 を壊さない
- **今日は本番が不安定だったので、変更は最小限かつ可逆に**
- 作業ログ: reports/race_detail_render_cost_work_log_20260822.md
  (計測結果 / 原因 / 変更点 / テスト結果 / コミットID)
