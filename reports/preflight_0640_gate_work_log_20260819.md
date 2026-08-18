# 06:40公開前プリフライトゲート 作業ログ

- 実施日: 2026-08-19
- 対象: `scripts/render_maintenance_scheduler.py` 内の既存maintenance scheduler
- 基準コミット: `1f99a5b`（セキュリティ実装 `b49ca49` を含む）
- 制約順守: pushなし、deployなし、Supabase schema変更なし、`render.yaml`変更なし、採用ROI戦略ロジック変更なし、本番書込みなし

## 実装内容

1. 既存の10分間隔maintenance schedulerへ`preflight`フェーズを06:40 JSTで追加した。06:40–06:59は遅延中の旧フェーズよりpreflightを優先し、07:00以降の手動recoveryは従来順序を維持する。
2. 生成段で`prewarm_strategy_pages.py --mode realtime --date YYYY-MM-DD`をcron子プロセスとして実行し、当日の市場シグナルcacheを07:00公開前に生成する。
3. 検査段で13項目を測定し、各項目を`id/name/critical/status/ok/actual/expected`形式にする。全結果、生成診断、修復履歴、gate判断、実測値を`render_maintenance_preflight_v1`の`task_runs.detail`へJSON保存する。
4. 同じJSONを`system_status(check_name='preflight_0640_gate')`へupsertし、`/healthz?full=1`の`checks.preflight`から参照できるようにした。
5. critical #1/#2/#5失敗時は、対応する`program`、詳細HTML missing-only prewarm、当日候補signal prewarmをジョブ単位で重複排除し、それぞれ最大1回だけ実行する。修復後は全13項目を再測定する。
6. 修復後もcriticalが残り、かつ`BOATRACE_PREFLIGHT_GATE=1`の場合だけ、system_status JSONへ`extend_maintenance=true`を記録する。Webは07:00–07:29 JSTだけその当日行を15秒cacheで参照し、TOP snapshotも含め503を維持する。07:30以降、DB障害、JSON異常は必ずfail-openで公開する。
7. noncritical失敗、残存critical、生成失敗は1通に集約し、既存`notify_cron_failure`経路へ送る。検査上の失敗でもpreflightフェーズ自体は完了扱いにし、schedulerの次tickによる追加修復を防ぐ。
8. b49ca49のf-string SQL全数監査へ、内部生成した`?` bind placeholderだけを補間する新規1件を明示監査し、固定件数を155から156へ更新した。

## 13項目の判定基準

| # | 項目 | critical | OK条件 |
|---:|---|:---:|---|
| 1 | 本日レースデータ | yes | `races > 0`かつ`entries == races * 6` |
| 2 | レース詳細HTML | yes | 現行versionの`race_detail_page` cache件数が`races`と一致 |
| 3 | モーター情報 | no | 現行motor history cache件数が`races * 6`と一致 |
| 4 | タグ付与 | no | 現行race-detail tag cacheが全レースを覆い、当日signal cacheが存在しpendingでない |
| 5 | 本日のレース候補ページ | yes | 内部会員sessionで`/member/today-races`がHTTP 200、候補数をHTMLから整数取得（0件も正常な算出結果） |
| 6 | 事故率処理 | no | 当日snapshot task成功、integrity task成功、`post_run_accident`がok、check_dateが当日または前日 |
| 7 | 昨日バックテスト取込 | no | `KACHISUJI_DB`（既定`data/kachisuji_slim.db`）の`asof_race_features.MAX(race_date) >= 昨日` |
| 8 | predictions | no | 当日のprediction存在レース数が`races`と一致 |
| 9 | signal cache payload | no | 当日cacheが存在し、`computed_at`とdict型`signals`を持つ（候補0件の空dictは正常） |
| 10 | `race_closed_at` | no | 非NULL・非空の件数が`races`と一致 |
| 11 | incident/cron失敗 | no | open/investigating incidentが0件、直近12時間のfailure状態Render taskが0件 |
| 12 | `/healthz` | no | HTTP 200かつbodyの`status`が`error`/欠落でない |
| 13 | DB接続余裕 | no | `SELECT COUNT(*) FROM pg_stat_activity`の実測が45未満 |

## ゲートフロー

`06:40 tick` → realtime signal生成 → 13項目初回検査 → criticalなしなら結果保存・通常公開 → criticalありなら対応ジョブを各1回だけ修復 → 13項目再検査 → 解消なら結果保存・通常公開 → 残存時は結果保存・集約通知 → env既定`0`なら07:00公開、env`1`なら07:00–07:29のみ503延長 → 07:30必ず公開。

noncriticalだけの失敗はsystem_statusをwarningにし、集約通知するが公開を止めない。critical残存はerrorにする。Webの延長判定は当日`preflight_0640_gate`行だけを参照し、DB読取不能時も公開を優先する。

## テスト・検証結果

- 構文: `python -m py_compile scripts/render_maintenance_scheduler.py src/web/app.py` 成功。
- Ruff（schedulerと追加・更新テスト）: 成功。
- focused + source/security + signal recompute guard + render cron構成: **152 passed**。
- 指定non-E2E全体: `python -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e --basetemp .pytest_tmp_preflight_full` → **1152 passed, 1 skipped**。
- `git diff --check`: 成功。
- `render.yaml`、`src/search/roi_search.py`、`src/search/strategies.py`に差分なし。b49ca49のsecurity focusedテスト10件を含む全体試験が成功。

### 本番データ読取整合性確認

`check_post_run_integrity.py --date 2026-08-19 --stage morning --no-persist --warnings-ok`を使用し、Supabaseへ一切保存せず確認した。

- detail_rows: 144/144 OK
- predictions: 144/144 OK
- motor cache: 864/864 OK
- race-detail tags: 144/144 OK
- 現行v18 race-detail HTML: 0/144（144件欠落）

最後の欠落は現在の本番状態に対する既存問題であり、本実装のcritical #2が検出し、06:40に`prewarm_race_detail_pages.py --missing-only`を1回実行する対象である。検査は`--no-persist`で実行し、本番DB/cacheへの書込み、scheduler起動、deployは行っていない。

## 発生した問題と対処

1. 最初のfocused runは、06:40優先処理が08:00手動recoveryにも適用され1件失敗した。優先範囲を06:40–06:59に限定し解消した。
2. 最初の全体runはWindowsホストtempのACLで`tmp_path` setupが失敗した。記録済みのrepository内`--basetemp`で再実行した。
3. 次の全体runはf-string SQL監査固定件数が155のままで1件失敗した。追加SQLはローカル生成した`?`列だけを補間し、cache key値は全てbind parameterであることをASTと目視で監査し、ガードを156へ更新した。
4. 本番読取検査の初回はsandboxの外向きPostgres接続拒否で失敗した。承認済みのread-only/no-persist再実行で上記実測を取得した。

## コミット

- 実装コミットID: 作業完了時のローカルコミットを最終応答に記載する。
- origin/mainへのpush、Render deployは実施しない。
