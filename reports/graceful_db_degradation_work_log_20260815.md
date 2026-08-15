# DB一時障害 graceful degradation 作業ログ

作業日: 2026-08-15
対象指示書: `reports/graceful_db_degradation_plan_20260815.md`

## 実装結果

- `src/db/connection.py`
  - PostgreSQL接続取得に一時障害判定を追加した。
  - `PoolTimeout`、SQLSTATE `08`系、`57P01`〜`57P03`、接続timeout/reset/refused相当だけを再試行する。
  - 既定は初回失敗後に0.2秒、0.5秒待つ最大2回再試行（取得試行は合計3回）。
  - `BOATRACE_DB_CONNECT_RETRIES` と `BOATRACE_DB_CONNECT_RETRY_DELAYS_SEC` で減量・調整できるが、再試行2回、各待機0.5秒、合計待機1.0秒をハード上限にした。
  - SQLSTATE `28`系（認証）と`3D`系（DB不存在）は即時送出し、再試行しない。
- `src/web/app.py`
  - DB依存処理を実行し、一時接続障害だけをstale/準備中へ落とす共通ヘルパー `_with_transient_db_fallback` を追加した。
  - `/races`: 通常スナップショット → DB描画 → 期限切れを含む直近スナップショット → 穏当な再読込案内200の順。障害時のスナップショットはコンテキストプロセッサを通さずDBなしで描画する。
  - `/race/<race_id>`: fresh HTML → stale HTML → 既存の準備中ページ200の順。ルート全体も共通ヘルパーで包み、prewarm経路の接続障害も500にしない。
  - stale表示には「最新ではない可能性があります」を控えめに付け、`X-Boatrace-Data-Stale: 1` と `no-store` を返す。データは保存済みキャッシュだけで、生成・捏造していない。
  - 一時障害は最大50件のプロセス内バッファへ記録し、次の成功済み接続を再利用して60秒に1回まで既存`system_status`の`transient_db_error`へmergeする。障害中に記録用の新規接続を追加取得しない。書込み失敗は本流へ影響せず、イベントを戻して次回回復時に再試行する。
- `src/web/templates/error_temporary.html`
  - `race.html`流用を廃止した独立軽量HTML。30秒meta refresh、穏当な文言、500監視status維持、DBコンテキストプロセッサ不使用。
  - 「OR Error」「0: ERROR」や内部例外文字列は表示しない。

## テスト

- 編集前全体: `793 passed`。
- 接続層: `tests/test_db_connection_pool.py` — `8 passed`。
- Web集中: graceful degradation、接続層、TOP、詳細prewarm — `58 passed`。
- 編集後全体: `.venv/Scripts/python.exe -m pytest tests/ -q` — `802 passed`。
- Python compilation: pass。
- `git diff --check`: pass。
- pytest warningは既存の`.pytest_cache`書込み警告1件だけ。

追加回帰は、1回/2回の一時失敗後の成功、回数・待機ハード上限、認証失敗の非再試行、`/races` stale/案内200、詳細stale/準備中200、500描画時DB非参照、既存`system_status`だけへの記録を固定した。

## 読み取り専用データ整合性

- `data/boatrace.db`をSQLite URI `mode=ro`で確認した。DB書込みは行っていない。
- `race_results`自然キー重複: 0件。
- `racer_accident_rank_snapshots`: `snapshot_date=2026-08-15`、期間2026-05-01〜2026-08-15が1621件。
- `roi_race_history`の2026-08-15行: 2件。

## 失敗と修正

- 初回の日本語指示書読込がWindows既定コードページで文字化けした。UTF-8明示で再読込し、以後固定した。
- 初回の複合`rg`はPowerShell引用符解釈で実行前に失敗した。単純な単一引用符パターンへ分割した。
- 初回Web集中テストは55/58。DBなしTOP描画が存在しない局所変数`static_version`を参照していたため、既存の`app.jinja_env.globals`参照へ修正し、58/58と全802件を再確認した。

## スコープ・運用

- 変更: 接続層、Webフォールバック、TOPのstale注記、専用一時画面、専用テスト、作業ログ、handoffのみ。
- 非変更: ROI戦略、予測、DBスキーマ/データ、新テーブル、`render.yaml`、cron、production writer。
- ローカルserver、scheduler、browser、watcherは起動していない。
- push、deployは行っていない。
- 実装コミット: `52f70a0`、`cd37699`。文書コミットは最終ローカルHEADとして納品時に報告する。
