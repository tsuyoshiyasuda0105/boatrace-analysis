# 作業指示書: P0-3 公式サイト過剰アクセスの止血 (BAN リスク解消)

作成: 2026-08-14 / 発注者: リッキー / 単体で完結する指示書。
リポジトリ: `C:\boat_project\boatrace-analysis` (正本。他の場所に checkout を作らない)
背景: 監査 [reports/codebase_audit_20260813.md] の P0-3。boatrace.jp への実効リクエスト
間隔が規約値 (2.0秒 = `config.REQUEST_INTERVAL_SECONDS`) の約3倍の頻度になっており、
BAN されるとデータ供給が全停止する。商用許諾交渉の前提としても解消必須。

## 絶対に守るルール

1. **origin/main への push 禁止**(push=本番デプロイ)。コミットはローカル main まで。
   デプロイは発注者の承認ゲート。
2. ROI 戦略ロジック・予測ロジック・DB スキーマの変更禁止。
3. `REQUEST_INTERVAL_SECONDS = 2.0` は変更しない (短縮は論外、延長も勝手にしない)。
4. 各タスク完了ごとに、変更ファイル・テスト結果を `reports/p0_3_work_log_20260814.md` に記録。
5. テストは `.venv/Scripts/python.exe -m pytest tests/ -q` (既存の16失敗は既知、増やさないこと)。

## 問題の全体像 (調査済み・検証済み)

| # | 問題 | 場所 | 影響 |
|---|---|---|---|
| 1 | 決まり手の永久再スクレイプループ | `src/parsers/result_html.py:200` が `race_kimarite: None` をハードコード (パーサー未実装) なのに、`src/collectors/result_scraper.py:258-276` が「決まり手が空のレース」を締切後24時間、5分毎のcronで毎回再取得 | 埋まることのない条件で全レースを永久再取得。純粋な無駄リクエストの最大源 |
| 2 | レート制限がプロセス局所 | `src/collectors/_http.py:58-65` の `_last_request_at` はプロセス内グローバル。Render では同一 cadence `*/5 23,0-13` の3つのcron (odds / regular / exhibition-detail) が並走 | 合算で実効 ~0.67秒/リクエスト = 規約の3倍 |
| 3 | 失敗時に全レース取得へフォールバック (fail-open) | `src/collectors/result_scraper.py:219-220` — L4候補の絞り込みクエリが失敗すると `l4_only = False` で対象を全レースに拡大 | DB 不調時ほどリクエストが激増する逆保険 |
| 4 | official_dl が共通レート制限を迂回 | `src/collectors/official_dl.py:57-61` — 生 `requests.get`、間隔制御は `fetch_range` 内 (1.5s) のみ。`fetch_one`/`download_lzh` 直呼びは無制御 | mbrace.or.jp への無制御アクセス |
| 5 | odds cron に多重実行ガードなし | `scripts/odds_scheduler_render.py` は lock なしで `base.main()` 直呼び | 遅い回と次の5分 tick が重複しリクエスト倍増 |
| 6 | パーツ交換情報の破壊的更新 | `src/collectors/beforeinfo.py:80-90` `_upsert_parts` — 無条件 DELETE 後、パース結果が空なら**何も挿入しない** | HTML 構造変化時に正常データを消して空にする (再取得誘発) |

## タスク

### タスク1: 決まり手パーサーの実装 (最優先・効果最大)

1. `src/parsers/result_html.py` に決まり手 (逃げ/差し/まくり/まくり差し/抜き/恵まれ) の
   抽出を実装する。結果ページ (`RESULT_URL` 形式) の HTML に決まり手表示があるため、
   保存済み HTML (`data/raw/` 配下や `page_html_cache`) を fixture にして確認しながら実装。
2. `"race_kimarite": None` のハードコードを抽出値に置き換える。
3. 抽出できた決まり手が `race_results.kimarite` へ書き込まれる経路
   (result_scraper 側の upsert) が機能することを確認。
4. **安全弁**: 万一パースできないページでも従来と同じ None を返す (例外で落とさない)。
   併せて `result_scraper.py` の kimarite 再取得クエリに **1レースあたりの再試行上限**
   を追加する (例: `task_runs` か既存の仕組みで試行回数を数え、5回失敗したら当日は対象外)。
   これでパーサー実装が万一不完全でもループは有限になる。
5. fixture HTML を使った回帰テストを追加 (`tests/test_result_html_kimarite.py`)。

### タスク2: プロセス横断の共有レートリミッタ

1. `src/collectors/_http.py` の `_wait_interval()` を拡張:
   - `DATABASE_URL` が Postgres かつ env `BOATRACE_SHARED_RATE_LIMIT` が有効 (デフォルト
     有効で良い) の場合、DB 上の単一行テーブルでリクエスト枠を取得する方式にする。
   - 実装案: `scrape_rate_slots (host TEXT PRIMARY KEY, last_request_at TIMESTAMPTZ)` を
     `CREATE TABLE IF NOT EXISTS` し、
     `UPDATE scrape_rate_slots SET last_request_at = now() WHERE host = ? AND last_request_at <= now() - make_interval(secs => ?) RETURNING 1`
     が行を返すまで 0.3〜0.5秒スリープでリトライ (上限あり)。行が無ければ INSERT。
   - host 単位 (boatrace.jp / mbrace.or.jp) で枠を分ける。
   - **フェイルセーフ**: DB にアクセスできない場合は従来のプロセス内リミッタに
     フォールバック (止血が本体を殺さないこと)。
2. 接続は `connect(direct=True)` (2026-08-14 追加済み) を使い、Webの共有プールを
   汚さないこと。1リクエスト2秒間隔の用途なので接続は使い回してよい。
3. テスト: SQLite 環境ではフォールバック動作になることのテスト + 共有スロット取得
   ロジックの単体テスト (fake connection で可)。

### タスク3: fail-open の修正

`src/collectors/result_scraper.py:219-220` を fail-closed に変更:
候補クエリ失敗時は `l4_only = True` を維持し、そのパスでは候補以外を取得しない
(warning ログは残す)。既存テスト `test_result_scraper_market_signal_targets.py` を
確認し、必要なら回帰テストを追加。

### タスク4: official_dl の共通ペーシング

`src/collectors/official_dl.py` のダウンロードを `_http.py` の共有リミッタ経由に変更
(または `_wait_interval` を import して呼ぶ)。User-Agent は `config.USER_AGENT` を使用。
`fetch_range` 内の独自 1.5s sleep は共有リミッタに一本化。

### タスク5: odds cron の多重実行ガード

`scripts/odds_scheduler_render.py` に `scripts/render_maintenance_scheduler.py` と同型の
`pg_try_advisory_lock` ガードを追加。取得失敗時は「前回実行中」としてスキップし、
**success を記録しない** (偽装成功パターンの再現禁止。監査で
`refresh_race_detail_after_exhibition.py:601` の前例あり)。

### タスク6: パーツ情報の破壊的 DELETE 防止

`src/collectors/beforeinfo.py` `_upsert_parts`: パース結果が空 (rows が falsy) の場合は
DELETE も行わずスキップして warning を出す。既存データを空で上書きしない。
回帰テスト追加。

## 受け入れ条件

- [ ] 決まり手が実HTML fixtureから抽出でき、再スクレイプ対象クエリの件数が収束する
      (同一レースが毎パス対象になり続けない) ことをテストで示す
- [ ] 共有リミッタ有効時、複数プロセスの合算リクエスト間隔が 2.0 秒を下回らない
      設計であることをコードとテストで示す (DB不通時はプロセス内制御に自動フォールバック)
- [ ] `l4_only` は例外時に False にならない
- [ ] pytest: 既存16失敗から増えていない。新規テストは全て green
- [ ] `reports/p0_3_work_log_20260814.md` に変更一覧・テスト結果・
      「デプロイ待ち」の明記
- [ ] push していない (デプロイは発注者ゲート)

## やらないこと (スコープ外)

- cron スケジュール自体の変更 (render.yaml の cadence 変更は別途判断)
- スクレイピング対象の拡大・縮小 (L4 判定条件には触れない)
- 回復ロジック全体の再設計 (P1-4 で実施)
