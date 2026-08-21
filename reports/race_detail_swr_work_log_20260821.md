# レース詳細ページ SWR 化 作業ログ

作業日: 2026-08-21

## 変更点

- `/race/<race_id>` の当日・未来レース用 HTML キャッシュを stale-while-revalidate 化した。
  - 180 秒以内の新鮮なキャッシュは従来どおり即時返却する。
  - 期限切れだが存在するキャッシュは `no-store` と `X-Boatrace-Data-Stale: 1` を付けて即時返却し、canonical route の `?recompute=1` を daemon thread から呼び直す。
  - ログイン済み利用者でキャッシュが全く無い場合は、指示書どおり従来の同期生成へ進む。
  - unauthenticated guest のキャッシュ欠損は、既存の cache-only 安全契約を守り、DB を読まず準備中ページを返す。
- race_id 単位の lock/set による in-flight guard を追加し、同一 Web プロセスで再生成を多重起動しないようにした。完了・失敗・thread 起動失敗のいずれでも guard を解放する。
- background thread だけに有効な `ContextVar` を追加した。ブラウザからの `?recompute=1` は従来どおり重い再計算を許可しない。
- `refresh_race_detail_after_exhibition.py` と `prewarm_race_detail_pages.py` は変更していない。cron/prewarm の同期生成経路はそのままで、Web 側との重複書込みは既存の `page_html_cache` UPSERT により冪等である。
- 回帰テストを更新・追加し、新鮮なキャッシュ、stale キャッシュの即時返却と background 起動、同一 race_id の二重起動防止、キャッシュ皆無時の member 同期生成、guest cache-only、内部再生成 context の解放を検証した。

## テスト結果

- focused（guest / SWR / 展示反映 / prewarm / DB 劣化 / recompute guard）:
  - `133 passed`
- 指示書指定の非 E2E 全体テスト:
  - `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e`
  - `1185 passed, 1 skipped`
  - 指示書基準 `1184 passed, 1 skipped` を維持し、新規 lifecycle テスト 1 件分増加した。
- 静的確認:
  - `py_compile`（変更 Python 2 ファイル）: pass
  - focused test file の Ruff: pass
  - `git diff --check`: pass
  - `render.yaml`、`scripts/prewarm_race_detail_pages.py`、`scripts/refresh_race_detail_after_exhibition.py` の差分なし
  - `src/web/app.py` 全体 Ruff には既存の 95 件、undefined-name 限定監査には既存の到達不能コード 7 件があるため、今回の差分としては修正していない。全体テストとコンパイルは通過している。
- pytest の警告は既存 `.pytest_cache` ACL 警告のみ。

## 作業中の失敗と対応

- PowerShell の既定表示で UTF-8 日本語が文字化けし、最初の route/test patch が照合失敗した。UTF-8 を明示した repository virtualenv 出力で再確認し、安定した行を境界に再適用した。
- 全体テスト初回は shell timeout を誤って 1 秒にしたため判定前に終了した。残存 pytest がないことを確認して 10 分の有限 timeout で再実行した。
- 全体テスト初回の有効な実行は `1184 passed / 1 failed / 1 skipped`。guest の cache-only 契約まで同期生成へ広げたのが原因だったため、guest の cache miss だけ準備中レスポンスを維持し、focused と全体を再実行して通過した。
- Ruff の `E999` selector は現行 Ruff から削除済みでコマンドが拒否された。selector を除外して再監査し、変更テストの Ruff、コンパイル、diff check を通した。

## コミット

- 実装コミット: `0b19093` (`Add stale-while-revalidate for race details`)
- 上記コミットに含まれるファイル:
  - `src/web/app.py`
  - `tests/test_race_detail_page_prewarm.py`

## 制約・後続

- push、deploy、本番 Supabase 書込み、ローカル production scheduler 実行は行っていない。
- `render.yaml`、採用 ROI 判定、メンテ窓、prewarm 同期生成経路は変更していない。
- task-created pytest basetemp 2 件はリポジトリ内であることを確認して削除済み。サーバー、watcher、scheduler、background test process は残っていない。
- deploy 後の本番確認は owner 作業。stale hit 時の `race_detail stale-cache served ... refresh_started=` と、その後の `race_detail built ...` の時間差を確認する。
