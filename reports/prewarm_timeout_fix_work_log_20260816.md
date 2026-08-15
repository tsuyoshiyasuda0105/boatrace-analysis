# 朝のレース詳細 prewarm タイムアウト修正 作業ログ

作業日: 2026-08-16
対象: `render_maintenance_detail_v1` / `render_detail_pages_selfheal`

## 障害と原因

- 2026-08-14〜2026-08-16、`prewarm_race_detail_tags.py` が外側の900秒制限で3回ともkillされ、後続のページ・TOP・market signals生成へ進めなかった。
- タグキャッシュの保存処理そのものは `page_html_cache` へ1レース単位でcommitしていたが、スクリプトは全レースを毎回再計算し、時間予算・既存キャッシュskip・部分完了の契約を持っていなかった。
- self-healは被覆50%で完了扱いし、同日success後は再実行しないため、残件を日中に埋め切れなかった。

## 実装

- タグとHTML prewarmへ `--budget-sec` を追加した。予算到達時は処理中の1レースを完了後に止まり、`remaining` と `budget_exhausted` を出力してexit 0とする。
- 現行バージョンの永続キャッシュがあるレースを先に除外し、未処理だけを会場・レース番号順に処理する。各タグ/HTMLは既存の1レース単位commit経路で保存されるため、次回は残件から再開する。
- タグとHTMLの各レース所要時間、および平均・中央値・最小・最大をstdout summaryへ追加した。
- 朝メンテはタグ600秒、HTML600秒、各subprocess timeout 900秒とした。HTMLは `--missing-only` で実行する。残件がありintegrityが未達でも、両prewarmが正常終了した場合は `partial=true` の部分成功として後続フェーズへ進む。
- 日中self-healはタグ240秒、HTML240秒、各timeout 360秒とした。100%被覆まで残件を再実行でき、完了した試行から30分未満は再実行しない。残件数と実行前後の被覆を `task_runs.detail` に残す。
- ROI、予測ロジック、DBスキーマ、`render.yaml` は変更していない。

## 1レース所要時間の読み取り実測

本番DBへ書き込まない `_build_race_detail_tag_snapshot` を、2026-08-16の先頭・中央・末尾の3レースで実測した。

| race_id | 秒 |
|---|---:|
| 20260816-01-01 | 0.661 |
| 20260816-11-01 | 0.475 |
| 20260816-24-12 | 0.359 |

- 平均0.498秒、中央値0.475秒、最小0.359秒、最大0.661秒。
- 計測前後の `page_html_cache` は44,326件で、最大更新時刻も不変だった（書き込みなし）。
- 孤立実測を192件へ単純換算すると約96秒であり、計算単体では900秒超過を再現しなかった。ビルダーは1レースごとに複数の逐次DB readを行うため、朝フェーズ中のDB接続競合・checkout待ち・N+1型の待ち時間増幅が有力な追加調査点。ただし指示どおり本タスクでは性能ロジックを変更していない。

## テスト

- 近接テスト: 93 passed。
- cron/prewarm拡張束: 146 passed。
- Kachisuji E2E単独: 49 passed。
- 現在のローカルmainは指示書作成後の8コミットにより917件を収集する（指示書の811件から106件増）。今回追加は4件。
- 全体実行では913 passedまで確認。既知の範囲外問題は次のとおり。
  - `tests/round3_e2e/test_kachisuji_round3_web.py`: 本番データ更新により固定期待 `matched=search=0` が実値4/3となる1件。残り2件は単独passだが、全E2E同時実行ではPlaywright Sync API/async loop競合でsetup error。
  - `tests/test_graceful_db_degradation.py::test_race_detail_transient_error_uses_stale_then_preparing`: 単独でも既存のstale header期待が再現失敗。
- 上記は今回の差分外で、ROI/検索および別のWeb劣化処理に属するため修正していない。今回変更した4スクリプトと4テストファイルの回帰はすべてgreen。
- Python compile、`git diff --check` はpass。

## 安全確認

- production prewarm、scheduler、writer、ローカルサーバーは起動していない。
- 読み取り計測以外の本番アクセスなし。読み取り計測のキャッシュ指紋は不変。
- push、deployなし。`render.yaml`、ROI、予測、DBスキーマは未変更。
