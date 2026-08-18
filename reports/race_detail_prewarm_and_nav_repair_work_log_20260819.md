# レース詳細 prewarm / 会員ナビ修理 作業ログ（2026-08-19）

## 実装内容

### 問題A: 05:30 detail フェーズの診断

- `scripts/render_maintenance_scheduler.py` の tags / pages / integrity の3子プロセスを `run_py_detailed` 経由へ統一した。
- 各子プロセスについて `return_code`、`timed_out`、`oom_suspected`、`stderr_tail`（末尾500字）、`peak_rss_mb` を `task_runs.detail` の `subprocesses` JSONへ保存する。
- `subprocess.run` 自体が `OSError` 等で起動できない場合も、`return_code: null` と `spawn_error=<型>: <内容>` を残す。失敗は従来どおりフェーズ失敗として伝播し、通知・再試行を握りつぶさない。
- `peak_rss_mb` は Render/Linux の `RUSAGE_CHILDREN` が返す完了済み子プロセスの高水位。利用不能な環境ではフィールドを省略せず `null` とする。これは各実行時点までの子プロセス最大値なので、単独プロセスの厳密な時系列サンプリングではない。
- 600秒のbudget、3 attempt上限、08:08 self-heal、cron構成は変更していない。

診断例:

```json
{
  "remaining": 144,
  "subprocesses": {
    "tags": {
      "return_code": 137,
      "timed_out": false,
      "oom_suspected": true,
      "stderr_tail": "...Killed",
      "peak_rss_mb": 511.8
    },
    "pages": {
      "return_code": null,
      "timed_out": true,
      "oom_suspected": false,
      "stderr_tail": "pages timed out",
      "peak_rss_mb": 511.8
    },
    "integrity": {
      "return_code": 9,
      "timed_out": false,
      "oom_suspected": false,
      "stderr_tail": "integrity failed",
      "peak_rss_mb": 511.8
    }
  }
}
```

### prewarm メモリ軽量化

- デフォルトbatchを25ページから8ページへ縮小（同時に保持するprefetch入力グラフを最大68%削減）。180レース日でも23 batchで完了する。
- batchごとのメモリキャッシュclear、prefetch参照解放、`gc.collect()` を維持し、Flask response bufferを生成直後・cache-read検証直後に明示closeするよう追加した。
- 起動時importを棚卸しし、集計に必須でなかった `statistics` importを除去して、組み込み演算で平均・中央値を算出するようにした。`web_app` / `config` / `json` 等はページ生成またはレポート出力に必要なため維持した。
- 実際の2026-08-19 144ページ再生成は本番書込み禁止のため再実行していない。依頼元実測の144/144・51.4秒を基準値として保持する。

### 問題B: 共有HTMLを壊さない会員ナビ

- 通常の会員ページでは「本日のレース」を先頭へ復活し、公開ROIボタンを外した。管理者の並びは `本日のレース / バックテスト / プラン申込 / ROI / 月別推移 / 健全度 / 事故率 / 展示精度 / 管理` の9個。
- race-detailの永続共有HTMLは引き続きcache-neutralで、会員URL・会員メニューを含まない。
- race-detailだけ `/api/session-navigation` を `fetch` し、署名済みFlask sessionから会員ヘッダーをブラウザ側で復元する。エンドポイントはDB・Supabase・予測処理を呼ばず、`Cache-Control: no-store, private` と `Vary: Cookie` を付ける。ゲスト応答は `{"is_member": false}` のみ。
- テンプレート変更に合わせ、race-detail page cache versionをv17からv18へ更新した。

## 測定値

- session-navigation（ローカルFlask test client、管理者session、warm後300回）: median 0.184 ms / p95 0.208 ms / max 5.394 ms。
- race-detail本体のキャッシュ読取り経路にはDB処理を追加していない。追加コストはブラウザからの同一origin軽量GET 1回で、サーバー処理は通常1 ms未満、ネットワーク込みでも数ms級を想定する。

## テスト結果

- 対象回帰: 105 passed。追加のspawn-error / response-closeテスト: 2 passed。
- public ROI新仕様とStep 27テスト隔離: 3 passed。
- exact full non-E2E: `1118 passed, 1 skipped`。
- scoped `py_compile`、small-file Ruff、`git diff --check`: passed。`src/web/app.py` 全体のRuffには既存96件があるため、同ファイルはcompileと全回帰で検証した。
- モック回帰で、失敗時の3子プロセス診断が `record_phase` から `task_runs.detail` 相当JSONへ格納され、stderrが500字に切られ、rc=137がOOM候補になることを確認した。
- 採用ROI戦略、cron定義、render.yamlは変更していない。本番Supabase書込み、push、deployは実施していない。

## 残課題

- 真因（OOM、timeout、spawn失敗等）は次回05:30の実データで確定する。今回の修理はその証拠を必ず残しつつ、疑わしいページprewarmのピーク保持量を下げるもの。
- `peak_rss_mb` は完了子プロセス高水位であり、瞬間値のプロセス別監視が必要になった場合は次段で `/proc/<pid>/status` のポーリングを検討する。

## コミット

- 実装コミット: `PENDING`（ローカルのみ。push / deploy禁止）
