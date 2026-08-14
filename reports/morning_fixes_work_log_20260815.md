# 朝障害修正 作業ログ（2026-08-15）

## 実施範囲

- 対象: `reports/morning_outage_fixes_plan_20260815.md` のタスク1〜3
- 作業ブランチ: ローカル `main`
- push / deploy: 未実施
- 不変: ROI戦略、予測ロジック、DBスキーマ、`render.yaml`、`scripts/poll_results.py`
- task_runs: `src/db/cron_runtime.py` を利用する既存 `record_task` / `task_success_exists` 経路のみ使用

## タスク1: メンテ detail フェーズ

- 失敗経路: `render_maintenance_detail_v1` は tags prewarm の非0終了で pages prewarmを呼ばず failure となり、3回目の06:31に circuit breaker が開いた。監視上も detail page cache が0件のまま残った。
- 修正: tags の成否にかかわらず pages を実行し、その後 `detail_rows` / `motor_cache` / `detail_cache` を `--warnings-ok` で検査する。ページ生成と最終整合性が成功すれば、全ゼロ率・履歴未確立 warning を残したままフェーズ成功とする。実キャッシュ欠落は引き続き failure。
- テスト: focused 20 passed、変更後 full 651 passed。
- コミット: `355b597` (`Keep detail prewarm running through motor warnings`)

## タスク2: 朝の詳細キャッシュ self-heal

- 修正: lite 経路に現行版 race-detail page cache の被覆率確認を追加。`RACE_DETAIL_PAGE_CACHE_VERSION` を import し、50%未満なら tags (900秒) → pages (1800秒) を実行する。
- 同日ガード: `render_detail_pages_selfheal` の success を前提に再実行を抑止。被覆十分の場合も成功として記録。失敗時は failure のため次 tick で再試行可能。
- テスト: 被覆不足、被覆十分、同日成功済み、キャッシュ版追従を追加。focused 53 passed、変更後 full 655 passed。
- コミット: `c9628b5` (`Self-heal missing morning detail caches`)

## タスク3: 前日結果の自動再取込

- 修正: lite 経路の8時台に、前日を明示した `scripts/poll_results.py --date <yesterday> --no-jitter` を timeout 900秒で実行する。
- 同日ガード: `render_results_backfill_yesterday`、run_date=前日。success があればスキップし、failure は次 tick で再試行する。
- テスト: 8時台1回、同日前日分の2回目スキップ、failure後の再試行を追加。focused 65 passed、最終 full 658 passed。
- コミット: 本ログを含むタスク3コミット（IDは完了報告に記載）。

## テストと検証

- 初回の指定コマンドは共有 Windows temp への書き込み権限で `608 passed / 42 setup errors`。製品 assertion failure は0。
- repository-local `--basetemp .pytest_tmp_morning_fixes_20260815` でベースラインを再実行し `650 passed`。
- 最終: `.venv/Scripts/python.exe -m pytest tests/ -q --basetemp .pytest_tmp_morning_fixes_20260815` → `658 passed`。
- focused: タスク1 `20 passed`、タスク2 `53 passed`、タスク3関連 `65 passed`。
- `git diff --check`: pass。

## 保留・運用上の注意

- push / Render deploy / 本番 cron 実行 / production writer は未実施。デプロイは発注者承認待ち。
- `.pytest_cache` は既存の Windows cache path warning が1件出るが、テスト結果には影響なし。
- 実データでの次回確認事項: 8時台の `render_detail_pages_selfheal` と `render_results_backfill_yesterday` の task_runs success、および detail page cache 被覆率・前日結果件数。
