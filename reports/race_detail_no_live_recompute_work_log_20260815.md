# レース詳細 no-live-recompute 作業ログ (2026-08-15)

## 目的

2026-08-15 の502障害の原因になった、レース詳細のキャッシュミス時に人間のHTTPリクエストスレッドで約8〜13秒のライブ生成を行う構造を廃止する。既存キャッシュとバックグラウンド生成経路は維持する。

## 実装

- `race_detail` は、通常アクセスで fresh/stale の永続ページキャッシュが両方無い場合、`_race_basic_info` 以下の詳細生成処理へ進まず、HTTP 200 の軽量な準備中ページを返す。
- 準備中ページは「レース詳細を準備しています」「数十秒後に自動で更新されます」と案内し、30秒の meta refresh、`Retry-After: 30`、`Cache-Control: no-store, max-age=0` を付ける。
- Flask の通常の `render_template` は全ページ共通の `system_status` DBコンテキスト処理を実行するため、準備中ページだけは既存Jinja環境から直接描画する。これにより、ページキャッシュ参照以外のDB問い合わせを行わない。
- `cached` デコレータは `no-store` レスポンスをプロセス内TTLキャッシュへ保存しない。したがって、30秒後の再読込時には、バックグラウンド生成済みのHTMLへ直ちに切り替えられる。
- `recompute=1` かつ既存の `_effective_force_recompute()` が許可した `EXPENSIVE_RECOMPUTE_TRIGGERS` コンテキストでは、従来どおりキャッシュ読みを飛ばしてライブ生成・永続キャッシュ保存へ進む。トリガー判定は新設していない。
- fresh（当日180秒）キャッシュ、当日staleフォールバック、過去staleキャッシュの分岐と返却順序は変更していない。

## 裏側でのキャッシュ補充

新しいテーブル・リクエスト時の記録・DB書き込みは追加していない。既存の `scripts/prewarm_race_detail_pages.py` は対象日の全レースを生成し、`--missing-only` 時は `page_html_cache` の欠損を先に抽出できる。既存maintenanceのdetail phaseも全ページをprewarmし、regular schedulerのself-healも現行バージョンのカバー率低下を検出して同じprewarmを呼ぶ。このため、準備中になったキャッシュ欠損レースは既存prewarmの対象へ自然に含まれる。

## テスト

- 変更前ベースライン: `702 passed`。
- 通常アクセス（`recompute=1` をURLに付けても未許可）で、詳細DB・予測・fallback・conditions等を一切呼ばず、準備中ページを200で返すことを確認。
- 準備中レスポンスが外側TTLキャッシュに残らず、次のアクセスでprewarm済みHTMLを返すことを確認。
- `render-detail-prewarm` と `render-cron` で `recompute=1` の場合、`_race_basic_info` 以下の従来ライブ生成経路へ進むことを確認。
- 当日fresh、当日stale、過去staleをそれぞれそのまま返し、詳細生成へ進まないことを確認。
- 最終専用テスト: `tests/test_race_detail_page_prewarm.py` は `20 passed`。
- 最終全テスト: `709 passed`（既存702 + 新規7）、警告は既存の `.pytest_cache` 書き込み警告1件のみ。
- `python -m py_compile src/web/app.py` と `git diff --check` は成功。

## 運用上の確認点

デプロイ後は `slow_request` の `/race/...` 件数と最大時間が減ることを成功指標とする。準備中レスポンスは `race_detail preparing served`、通常の生成成功は既存 `race_detail built`、キャッシュ利用は既存 hit/stale ログで区別できる。

## 禁止領域・変更範囲

ROI/戦略、予測、DBスキーマ・データ、新テーブル、`render.yaml`、cronスケジュール、ワーカー数には触れていない。ローカルscheduler・production writer・serverは起動していない。pushおよびデプロイは実施しない。
