# cron自動監視・即通知・自己修復 作業ログ（2026-08-16）

## 実施範囲

- 実装コミット: `7acf7d4` (`Add cron watchdog alerts and self-healing`)
- push / deploy: 未実施（指示どおりローカル `main` のみ）
- `render.yaml`: 変更なし
- 新規cron / schedule: 追加・変更なし。監視は既存 `boatrace-regular-cron` のtickに相乗り。
- ROI戦略、予測、DBスキーマ、収集ロジック: 変更なし
- web error notifier: `src/web/app.py` で `install_error_notifier(app.logger)` とroot loggerへの配線が既に存在したため変更なし

## cron最終失敗通知

既存 `src/notifications/cron_alerts.py::notify_cron_failure` を以下へ配線した。

- `boatrace-regular-cron`: mainの非zero終了と例外
- `boatrace-odds-cron`: base mainの非zero終了、非zero `SystemExit`、例外
- `boatrace-exhibition-detail-cron`: 非zero終了、非zero `SystemExit`、例外
- `boatrace-accident-external-check-cron`: 非zero終了、非zero `SystemExit`、例外

通知関数の呼び出し自体は全経路でbest-effortとし、通知例外は元のexit code / 元例外を変えない。宛先と送信経路は既存の `BOATRACE_ERROR_NOTIFY_TO` とmailer切替をそのまま使う。maintenance / program-bootstrap / race-detailの既存通知は変更していない。

## regular-cron統合ウォッチドッグ

通常tickの既存stale reaper直後に `run_cron_watchdog()` を追加した。通常時は08:00以外4クエリ、08:00は前日結果確認を加えた5クエリのスナップショットで、修復後の再確認も対象項目だけを1クエリで再確認する。

| 検知項目 | 条件 | 自己修復 | 残存時の通知 |
|---|---|---|---|
| 本日詳細ページ | 現行版ページが本日raceの50%未満 | 既存 `run_detail_pages_selfheal`（既存30分ガード維持） | 修復後も50%未満なら通知 |
| 前日結果 | 08時台に6艇結果未完raceが3件以上 | 既存 `run_yesterday_results_backfill`（同日成功ガード維持） | 再確認後も3件以上なら通知 |
| cron反復失敗 | 直近6時間、当日成功がなく `run_count >= 3` のfailure task | なし | 通知 |
| DB/プール異常多発 | `transient_db_error.detail_json.recent` で直近30分に3件以上 | なし（既存プール回復に委譲） | 通知 |
| stale running | 6時間超のrunningが初回reaper後も存在 | 既存reaperを再実行 | 再確認後も残れば通知 |

各異常/修復結果は既存 `system_status` に `cron_watchdog_*` の日次upsertで記録する。ウォッチドッグ通知は異常種別ごとのjob名と `cooldown_hours=24` を既存通知関数へ渡すため、同一異常は24時間に最大1通。スナップショット、状態記録、自己修復、通知の例外はいずれもregular tick本流から隔離した。

## キャッシュ版数非依存

詳細ページのprefixは `v15` / `v16` 等を文字列で持たず、`src.web.app._race_detail_page_cache_key("")` から実行時に取得する。テストでは同関数を仮想 `v99` に差し替え、SQL引数が `race_detail_page:v99:` へ追従し、0件を被覆不足として扱うことを確認した。

ローカルDBをSQLite URI `mode=ro` + `PRAGMA query_only=ON` で確認したところ、07:55時点の2026-08-16は192 raceに対して現行v16ページが0件だった。この状態は新しい50%判定で自動検知され、既存selfhealが起動する。DBへの書込みやローカルscheduler起動は行っていない。

## テスト・整合性確認

- 焦点テスト: `108 passed`（新規11件 + cron通知/cooldown + scheduler + reaper + skip + detail schedule）
- Python compile: 対象4schedulerすべて成功
- `git diff --check`: 成功
- 指定全体テスト: `pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e`
  - `952 passed / 7 failed`（959 collected）
  - 新規watchdogと既存cron系は全件green
  - 7件は同時進行で追加・変更された対象外の事故履歴/as-of/ROI作業に再現: `test_accident_history` 2件、`test_db_pk_map_parity` 1件、`test_kachisuji_correctness_round3` 3件、`test_roi_search` 1件
  - 今回のcron差分は失敗traceに含まれず、ROI/予測/スキーマ/収集ロジック不可侵のため本作業では修正していない
- read-onlyデータ確認（ローカル07:55 snapshot）:
  - 2026-08-15: 216 race、6艇結果完了152 race（64 gap。新watchdogの閾値対象）
  - 事故rank snapshot: snapshot date 2026-08-16、1,622行
  - 2026-08-15 ROI daily cache: 行あり、JSON 28,085 bytes
  - `DATABASE_URL` は実行環境に無く、本番DBのread/writeは未実施

## 運用上の残件

- 実メール送信にはRender側の `BOATRACE_ERROR_NOTIFY_TO` とSMTP/Brevo/Resend設定が必要。未設定時は既存仕様どおり安全にno-op。
- デプロイ後、regular-cronの次tickで `cron_watchdog_*` 行、v16 detail selfheal、前日結果backfill、通知cooldownを本番read-onlyで確認する。
- 全体テスト7件は別タスクの作業ツリーが整った後に再実行する。cron側に既知の失敗はない。
