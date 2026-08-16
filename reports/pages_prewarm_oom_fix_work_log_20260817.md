# 朝メンテ・ページprewarm OOM根治 作業ログ（2026-08-17）

## 結論

`prewarm_race_detail_pages.py` を、予測モデル系をimportしないcached-only Flask appと、25レース単位のサブバッチで動かすように変更した。生成ルートとテンプレートには手を加えておらず、既存経路とのHTMLバイト同等テストも通過した。regular-cronの`render_detail_pages_selfheal`は、未被覆が残る限り30分間隔のbounded sliceを繰り返し、100%到達後にskipする既存設計であることを回帰テストで固定した。

## 原因調査

- Render環境では既存の`create_app()`が`predictor.load()`をskipするため、ranker/cascade/calibratorのartifact本体はロードしていなかった。
- ただし`src.web.app`のトップレベルで`src.web.predictor`を無条件importしていた。このimport連鎖により、ページ生成に不要なpandas、feature builder、model/cascade/calibrationモジュールがプロセス起動時に常駐していた。
- さらにページprewarmは、当日全レース（実測168件）のprefetch結果と1本のDB接続を全処理中保持し、process-local JSON/HTML cacheもレース数に応じて蓄積していた。
- ローカルfresh processのRSS実測:
  - 修正前: `src.web.app` import後 95.1 MB、`create_app()`後 111.7 MB。`src.web.predictor=True`、`pandas=True`。artifact本体は未ロード。
  - 修正後: ページprewarm import後 57.6 MB、cached-only `create_app()`後 60.8 MB。`src.web.predictor=False`、`pandas=False`、`lightgbm=False`。
  - 起動・app生成時点で約50.9 MB（111.7→60.8 MB）削減した。実測はWindows上のRSSであり、Render Linuxでは各バッチログとsummaryへ`/proc/self/status`の`VmHWM`（`peak_rss_mb`）を出す。

## 実装

### モデル非ロード経路

- `src/web/app.py`
  - `Predictor`のトップレベルimportを廃止し、通常appだけ`create_app()`内で遅延importする。
  - `create_app(cached_predictions_only=True)`を追加。この経路はversionと`artifact=None`だけを持つ軽量predictorを使い、予測モジュールをimportしない。
- `scripts/prewarm_race_detail_pages.py`
  - ページ生成appを必ず`cached_predictions_only=True`で作る。
  - ページは従来どおりDBの`predictions`と永続cache入力から描画し、live predictorへは入らない。

### サブバッチとメモリ解放

- デフォルト25レース（`--batch-size`で変更可能）ごとにprefetch・ページ生成を行う。
- 各バッチでDB接続のcontextを終了し、prefetch参照を解放する。
- バッチ間で`_CACHE`と`_PAGE_HTML_MEM_CACHE`をclearし、`gc.collect()`を実行する。
- 既存の`--budget-sec`判定を全体経過時間のまま維持し、処理済みページは1件ずつ永続化、次回`--missing-only`で未処理分から再開する。
- summaryへ`batch_size`、`batches`、`peak_rss_mb`を追加した。

## HTMLバイト同等

- race route、template、cache key、永続化関数は変更していない。
- 同じ固定入力について、通常`create_app()`経路と新しいcached-only + prefetch context経路の`response.data`を直接比較し、完全一致を確認した。

## self-heal安全網と予算配分

- 朝メンテはtags 600秒とpages 600秒を別々のsubprocess/budgetで実行するため、tagsがpagesの予算を食い切る構造ではない。pagesはtagsの成否にかかわらず実行される既存動作を維持した。
- regular-cronは`render_detail_pages_selfheal`でcoverageを確認し、不足時にtags/pagesを各240秒のbounded sliceで実行する。partialでも次のregular tickで再評価し、30分cooldown後に`--missing-only`で続きから埋める。
- 4/10→7/10→10/10→skipを模擬するテストを追加し、100%まで繰り返し埋めることと、各pages subprocessの240秒budget・360秒timeoutを確認した。

## テスト結果

- focused page/self-heal: 82 passed
- maintenance/cron focused: 27 passed
- 必須full non-E2E: `pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e --basetemp=.pytest_tmp_pages_oom_full`
  - 1022 passed, 1 skipped
- 対象script/tests Ruff: pass
- 対象Python compileall: pass
- `git diff --check`: pass

## 制約・非変更範囲

- ROI、予測計算、DBスキーマ、`render.yaml`、収集処理は変更していない。
- production scheduler/writer、deploy、pushは実行していない。
- 既存の別タスク変更（kachisuji/as-of等）は保持し、本件commitから除外する。

## 調査中の失敗と再発防止

- 最初のRSS probeで継承された`DATABASE_URL`を隔離せず`create_app()`まで呼び、production pool接続を試みた。接続取得前に`TooManyRequests`で失敗し、DDL/DMLは実行されなかった。以後は`DATABASE_URL`を空にし、DB初期化をmonkeypatchしたisolated probeだけを使用した。
- legacy `src/web/app.py`全体へのRuffは既存lint debt 95件で失敗した。本件外ロジックは変更せず、対象script/testsのRuff、compileall、focused/full pytest、scoped diffで検証した。
