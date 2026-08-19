# シグナル再計算 DB 圧迫 恒久修理 作業ログ（2026-08-19）

## 結論

展示後の定期シグナル更新を、毎回の全日再構築から「展示入力ハッシュが不変なら即時スキップ、変更時は該当レースだけ再計算」へ変更した。変更レース1件のローカル実測は **230 SQL / 48.424秒 → 30 SQL / 6.627秒**、展示不変時は **2 SQL / 0.007秒**。修正前後のシグナル判定ハッシュは一致した。

朝のフル再計算、採用ROI戦略の判定、Web入口の再計算拒否、preflight、ProxyFix・レート制限・開発秘密鍵拒否は維持した。`render.yaml`、Supabaseスキーマ、cron本数は変更していない。push・deploy・本番DB書き込みは行っていない。

## 原因

- `market_signals_for_date` がレースごとの詳細タグキャッシュを144件個別に読み、モーター閾値・決まり手履歴なども反復照会していた。
- 1-2-3、1-3-2、他の採用戦略が同じ `odds_trifecta` 日付範囲を別々に走査していた。
- 展示枠は展示値が変わっていなくても全日分を再構築した。
- `task_runs` の直近実行確認は check-and-claim の競合余地があり、`exhibition_detail_refresh` 本体と別系統の `signal_refresh` を相互排他にしていなかった。
- 実行時間とSQL数が `task_runs.detail` に残らず、退行を運用上検知しにくかった。

## 実装

### SQL往復削減

- SQL計測をDB接続ラッパーへ追加し、`BOATRACE_MEASURE_SQL=1` の内部prewarmでAPI発行単位を計測する。
- レース詳細タグキャッシュを `IN (...)` で一括取得し、欠損もプロセス内negative cacheへ登録する。旧N+1の145 SELECTを1 SELECTへ統合した。
- 1-2-3、1-3-2、1-4-3、1-2-4の対象オッズを1回で取得し、判定ごとの帯域抽出はメモリ上で行う。
- エースモーター閾値を場ごとのループから集合取得へ変更した。
- エースモーター決まり手履歴を対象選手・コース集合で一括取得した。
- 展示タイムは既取得の `all_race_info` を再利用する。
- JSONキャッシュはmarket、last-good、TOPの更新を一括 `executemany` で保存する。TOPは全再構築せず既存スナップショットのmarket部分だけ差し替える。
- ROI履歴の払戻・確定確認を候補集合単位へ変更した。展示増分では同日未確定のROI履歴を書き直さず、朝のフル再計算を正本とする。
- 内部prewarmのslow-request永続化を抑止し、同じ高負荷時間帯に診断用DB書き込みを重ねない。

### 増分再計算

- `race_previews`、`derived_start_stats`、`race_original_exhibitions` の展示後可変入力からレース単位SHA-256 fingerprintを1 SQLで生成し、last-good payloadへ保存する。
- 前回fingerprintと同じならアプリ生成前に終了する。
- 差分があれば `race_ids` で対象レースのみ評価し、last-goodの非変更レースとマージする。
- 朝8時前、nightly、履歴backfillは `--full` を付けて従来の全再計算を維持する。

### 重複実行防止

- `boatrace-exhibition-signal-refresh-v1` をSHA-256からsigned 64-bit keyへ変換し、PostgreSQL `pg_try_advisory_lock` / `pg_advisory_unlock` でセッションロックする。
- regular signal refreshは派生ST構築からsignal prewarm完了まで同じロックを保持する。
- exhibition detail refreshは展示取得・詳細再構築・検証を同じロックで直列化し、終了後のsignal refreshも同じロックを再取得する。別サービスが先に取得した場合は安全にskipする。
- SQLiteは単体テスト・ローカル検証用としてロック取得成功扱いにし、本番スキーマ追加は行わない。

### 計測記録

- `prewarm_strategy_pages.py` は `SIGNAL_REFRESH_METRICS` として `duration_seconds`、`sql_count`、`scope`、`changed_races`、skip理由を出力する。
- regular/exhibition両経路は上記を解析し、各signal refreshの `task_runs.detail` にJSONで保存する。失敗・gate停止・lock busyもduration/scope/sql_countを含む。

## before / after 実測

fixture: ローカルSQLite `data/boatrace.db`、対象日 `2026-08-18`（144レース、preview 864行、シグナル12件）。SQL数は接続ラッパーの `execute` / `executemany` API発行数。

| 経路 | SQL | 時間 | 備考 |
|---|---:|---:|---|
| 修正前・全日再計算 | 230 | 48.424秒 | レース詳細cache 145、決まり手等N+1を含む |
| 修正後・展示1レース増分 | **30** | **6.627秒** | 展示枠の主経路、目標30以下達成 |
| 修正後・展示不変skip | **2** | **0.007秒** | last-good読取 + fingerprint集合取得 |
| 修正後・朝フル | 43 | 18.123秒 | 朝のみ維持する正本再構築、ローカル60秒以下 |

Render本番の実時間はdeploy禁止のため未計測。実測障害時の230〜236秒はSupabase往復が支配的だったため、頻繁な展示経路を230→30 SQLへ落としたことが本番60秒目標に対する主要な恒久対策となる。deploy後は `task_runs.detail` の実測値で60秒以下を確認できる。

## 出力同一性

比較対象は `signals` objectをUTF-8、key sort、compact JSONで直列化したSHA-256。

- 修正前: `23d7e11f6b2eaec0273d54e1907fff1547cb40cf422f7f8de68c635dfffb965e`
- 修正後フル: `23d7e11f6b2eaec0273d54e1907fff1547cb40cf422f7f8de68c635dfffb965e`
- 修正後1レース増分を全日payloadへマージ: `23d7e11f6b2eaec0273d54e1907fff1547cb40cf422f7f8de68c635dfffb965e`
- 件数はいずれも12。**完全一致**。

`computed_at`、`refresh_scope`、fingerprint、`incremental_race_ids` は運用メタデータのため判定ハッシュから除外した。採用ROIの戦略定義・閾値・bet内容は変更していない。

## テスト・監査

- 新規単体テスト: 展示fingerprint不変時の全再構築skip、advisory lockの同一key解放、lock busy時の展示signal非実行。
- focused: `153 passed`。
- ROI集合化回帰: `38 passed`。
- 指定全体回帰: `pytest --ignore=tests/e2e --ignore=tests/round3_e2e --basetemp=.pytest_tmp_signal_refresh_full` → **1155 passed, 1 skipped**。
- `py_compile`: 変更したPython 9ファイル成功。
- `git diff --check`: 成功。
- `render.yaml` / `src/roi_contract.py`: diffなし。
- f-string SQL監査: 新規補間はbind placeholderまたは内部固定OR句のみ。値は全てbind parameter。
- foregroundのローカルSQLite計測のみ。本番writer、scheduler、server、deploy、pushなし。

## 作業中に検出・是正した失敗

- 最初のbefore計測で `.env` がSupabase URLを再読込したため接続を試み、sandbox networkで失敗した。書き込みは発生していない。以後 `DATABASE_URL=sqlite-local` を明示した。
- 1回のPowerShell検索が引用符不整合で実行前に失敗した。以後patternを分割した。
- 増分引数を誤ったrouteへ一時挿入しローカルでNameError/500を検出した。該当挿入を除去し正しいmarket routeへ移した。本番影響なし。
- 最初のfocused runはホストTemp ACLで9 fixture error。記録済みrepository内basetempへ切り替えた。
- 旧テスト7件が旧detail文字列・旧fullコマンド・旧単一cache writeを期待して失敗した。新しい計測JSON、朝full、bulk cache契約へ更新した。
- 最初の全体回帰はROI schema readyをSQLite接続間で共有したため7件失敗した。共有キャッシュをPostgres限定へ戻し、settled集合SQLを元の確定定義と等価なUNIONへ直した。再実行は1155/1155 pass。
- 全体テスト起動を2回、誤って1秒timeoutで開始しcollection中に終了させた。ファイル・DB変更なし。その後180秒timeoutで完走した。

## コミット

コミットID: 本作業のローカルコミット（実IDはコミット作成後の最終報告に記載）。

origin/mainへのpush、Render deployは行っていない。
