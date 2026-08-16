# 失敗テスト2件 修正作業ログ

- 作業日: 2026-08-16
- 対象: `test_db_pk_map_parity` / `test_race_detail_transient_error_uses_stale_then_preparing`
- 方針: テスト健全化のみ。製品コード、ROI、予測、DBスキーマ、`render.yaml` は無変更。push なし。

## 修正内容

### 1. PKマップ番人の誤検知

`tests/test_db_pk_map_parity.py` の静的スキャンから、`src/features/odds_sync.py` だけを明示除外した。このファイルは `sqlite3.connect(output)` でローカル検索DBへ直接書き、本番Postgresシム `src.db.connection` を通らないため、`odds_snapshot` を `_TABLE_PRIMARY_KEYS` に登録する必要がない。

除外はファイル単位の狭い allowlist とし、他の `.py` / `.sql` は従来どおり全て走査する。加えて、`src.db.connection` を import する架空の本番writerが未登録テーブルへ `INSERT OR IGNORE` した場合、そのテーブルが欠落として検知されるメタ回帰テストを追加した。これにより本番シム経由の番人は弱体化していない。

### 2. 日付依存の graceful degradation テスト

`tests/test_graceful_db_degradation.py` で `_today_jst_iso` を `2026-08-15` に monkeypatch した。固定race IDを常に「今日」の経路へ入れ、transient error時に stale HTMLを返し、その後キャッシュが無ければ preparing画面を返す既存アサーションを維持した。

## 検証

- 修正前の対象2件: 2 failed（再現確認）
- 対象2ファイル: `11 passed`
- 指定全体テスト: `pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e --basetemp=.pytest_tmp_test_health_20260816_final`
  - 結果: `921 passed, 0 failed, 1 warning`
  - warningは既存 `.pytest_cache` のWindows ACLによる `PytestCacheWarning`。
- `git diff --check`: pass

全体テストの途中、別作業のKachisuji全期間再生成が年度単位でローカル検索DBを入れ替えており、完全性テストが一時的に1件失敗した。読み取り専用で件数推移を確認し、ソースと検索DBが `557,617 / 557,617` に戻った後に再実行して全件greenを確認した。本タスクからDB書き込み、scheduler、production writerは実行していない。

## 変更範囲

- `tests/test_db_pk_map_parity.py`
- `tests/test_graceful_db_degradation.py`
- `reports/test_health_fix_work_log_20260816.md`

製品コード、ROI、予測、DBスキーマ、`render.yaml` は変更していない。originへのpush、デプロイも行っていない。
