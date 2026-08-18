# 作業ログ: /api/market-signals Web経路の詰まり 恒久修理

日付: 2026-08-18 / 実装: Codex CLI / 診断・検品: リン (Claude) / 発注: リッキー
指示書: `reports/market_signals_web_slowpath_repair_spec_20260818.md`

## 症状

会員が「アプリにアクセスできない」瞬間が毎晩発生。サーバー自体は生存
(healthz ok / 通常 0.15-0.45秒 / DB 20-60) だが `/api/market-signals` が
230秒級に膨らみ、gunicorn の同時スロットを食い潰して他リクエストを巻き添えにする。

| 日時 | slow_request 件数 | 最遅 |
|---|---|---|
| 8/16 22:58 | 551件 | 277.1秒 |
| 8/17 23:30 | 384件 | 254.7秒 |
| 8/18 21:23 | 313件 | 230.4秒 |

## 真因 (計測で特定)

**Web プロセス内で当日全レースの市場シグナル再計算が走っていた。**

ローカル実測 (キャッシュミス状態から):

| 項目 | 実測 |
|---|---|
| 総所要 | **42.231 秒** |
| 発行SQL | **212 本** |
| `fetchall` | 26.838 秒 |
| `execute` | 15.106 秒 |

本番の 230秒 は、この再計算が高負荷時に伸びたもの。

補足: 8/18 朝の修理 (85f2658) は `scripts/prewarm_strategy_pages.py` にだけ
`cached_predictions_only=True` を適用していた。本番 Web は `render.yaml` の
startCommand で `create_app()` を**引数なし**起動しており、既定値 False のまま
= 重い経路が生きていた。cron 側 signal_refresh は 08:00 以降 30回連続 success
だったため、cron の成功だけを見ていると気付けない穴だった。

## 修正

1. `src/web/app.py`: `create_app()` に `allow_market_signals_recompute` (既定 False) を追加。
   market-signals の `force_recompute` を
   `allow_market_signals_recompute and _effective_market_signals_recompute()` に変更。
   → 環境変数トリガだけに頼らず**プロセス境界**で防ぐ。Web worker が誤って
     cron 相当のトリガを継承しても重い経路に入れない。
2. `render.yaml`: 本番 Web の startCommand を
   `create_app(cached_predictions_only=True)` に変更。
3. `scripts/prewarm_strategy_pages.py`: prewarmer だけ
   `allow_market_signals_recompute=True` を明示付与 (重い再計算は cron に一本化)。

## before / after

| | before | after |
|---|---|---|
| キャッシュミス時の応答 | 42.231 秒 | **0.000940 秒** |
| HTTP | - | 200 (`pending` / `cache_miss`+`cache_only`) |

キャッシュは `page_html_cache` による **DB 永続**のため、デプロイでプロセスが
再起動しても last-good が残る (検品時点で `market_signals:last-good:2026-08-18`
と現行キーの双方が 35,415 B で存在)。よってデプロイ直後に会員へ空表示が出ない。

## 出力の同一性 (速度だけ直す)

修正前後で当日シグナルの判定が一致することを突合:

- 対象 12 レース
- 判定ハッシュ `f972ebc5693ff4b546192cb5909d42f3814d16aea912b85ddaa552922594723f` が**前後で一致**

## テスト

- focused (guard + prewarm): **78 passed** (検品時に再現確認)
- 全体 `pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e`:
  **1098 passed, 1 skipped** (検品時に再現確認)

新規/改称した回帰テスト:
- `test_web_app_never_enters_market_signals_heavy_path_even_with_cron_trigger`
  … Web ファクトリは cron 相当トリガを与えられても `db_connect` に到達しない
- `test_cron_market_signals_recompute_enters_existing_heavy_path`
  … prewarmer 経路は従来どおり重い再計算に入れる
- `test_render_blueprint_separates_web_and_cron_services`
  … blueprint に `create_app(cached_predictions_only=True)` が入っていること

## 用語についての訂正 (重要)

指示書および Codex 出力中の「L4」表記は**誤り**。L4 は不採用の旧世代手法で、
現行は `src/strategies/signals.py` の**採用ROI戦略 (adopted strategy /
matched_levels)**。突合したのは市場シグナル出力そのものなので検証内容は妥当だが、
今後の指示書では「採用ROI戦略」に用語を統一する。

## 残課題

- 本コミットに含めない未コミット変更が別途あり (`docs/handoff.md`,
  `src/web/static/style.css`)。本件と無関係のため分離。
- 中断要因: ホストの新規プロセス起動障害 `0xc0000142` により Codex 側で
  作業ログ作成・commit が未完だったため、リンが引き取って完了させた。
- デプロイ後、当夜 21-23時台に slow_request が出ないことを実データで確認すること。
