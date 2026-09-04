# 作業指示書: TOP スナップショット生成の 169 回キャッシュ読みをまとめる (Codex CLI 用)

作成: 2026-08-23 / 発注: リッキー / 診断・検品: リン (Claude)
テスト: .venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e
(現状 1204 passed, 1 skipped。割らないこと)

## 症状と原因 (リン実測済み・確定)

`scripts/build_top_page_snapshot.py` が本番で **毎回 180 秒**かかる
(ログ実測: elapsed=182.4s / 180.8s / 182.9s)。10 分毎に走るため、その 3 分間
web ワーカーを占有し、ログイン画面などが断続的に 502 になっている。

**原因は 1 件ずつのキャッシュ読み。** `_build_top_page_snapshot_payload()` の
SQL を数えたところ:

```
169回 | SELECT html, updated_at FROM page_html_cache WHERE cache_key = ?
  2回 | SELECT r.race_id, r.stadium_number, ... (races)
  1回 | SELECT detail_json FROM system_status ...
  ...
合計 179 回 / 種類 11
```

ローカル (本番 DB) で 10.7 秒。本番は Render(シンガポール)↔Supabase(東京) の
往復が 1 回ずつ乗るため 180 秒に膨らむ。169 回を 1 回のまとめ読みにすれば
往復も 1 回で済む。

## やること

### [必須1] キャッシュ読みをまとめる
`_build_top_page_snapshot_payload()` (および同じ経路でループ内から
`_read_page_html_cache` / `_read_page_html_cache_stale` を呼んでいる箇所) を、
**必要な cache_key を集めて 1 回の `WHERE cache_key IN (...)` で取得**する形に
変更する。

- Postgres のパラメータ上限に配慮し、**900 件程度でチャンク分割**すること
  (既存 `scripts/prewarm_race_detail_pages.py::_missing_persistent_page_ids`
   が同じ作法で書かれているので合わせる)
- **TTL 判定 (新鮮/期限切れ) の意味を変えないこと**。まとめ読みしても
  各キーの updated_at から個別に判定する
- インメモリキャッシュ (`_PAGE_HTML_MEM_CACHE`) を使っている場合、
  まとめ読みでもヒット判定が従来と一致すること

### [必須2] 共通処理への影響を確認
`_read_page_html_cache` はレース詳細など他経路でも使われている。
**シグネチャを壊さない**か、壊す場合は全呼び出し元を追随させること。
まとめ読み用の関数を新設して、TOP 生成側だけが使う形でもよい。

### [必須3] 効果を実測して作業ログに残す
変更前後で `_build_top_page_snapshot_payload("2026-08-23")` の
**SQL 回数と所要秒**を計測し、作業ログに数値で記載すること。
(本番 DB への読み取りのみ。書き込みはしない)

### [必須4] 回帰テスト
- TOP 生成のキャッシュ読みが 1 件ずつのループになっていないこと (静的チェック)
- まとめ読みでも新鮮/期限切れの判定結果が 1 件ずつと一致すること
- キーが 900 を超えるときにチャンク分割されること

## 絶対ルール
- push 禁止・デプロイ禁止・本番 Supabase 書込み禁止
- 採用ROI戦略の判定結果を変えない / render.yaml を変更しない
- 展示データの反映内容を減らさない
- **レース詳細の表示経路 (fresh/stale, 背景再生成の同時1本上限,
  RACE_DETAIL_PAGE_FRESH_SEC) を変更しない**。今日ようやく 0.4-0.7 秒で
  安定したところなので触らないこと
- 直近の d2e4534 / a8b6e65 / 374d731 / d26e587 を壊さない
- 作業ログ: reports/top_snapshot_cache_batch_work_log_20260823.md
  (計測結果 / 変更点 / テスト結果 / コミットID)
