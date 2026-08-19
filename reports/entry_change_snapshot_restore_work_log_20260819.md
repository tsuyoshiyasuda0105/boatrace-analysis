# 進入変更スナップショット定期実行 復旧作業ログ

作業日: 2026-08-20
対象HEAD: `58955c9`
指示書: `reports/entry_change_snapshot_restore_spec_20260819.md`

## 結論

コミット `3b1bfbb` で呼び出しだけが消えた `run_entry_change_snapshot` を、04:00〜07:00 JST の maintenance integrity フェーズから再び実行するようにした。対象は従来どおり today と tomorrow の2日である。

各日を独立して実行し、today側が `False` または例外でも tomorrow側を続行する。スナップショット失敗は既存の `render_entry_change_snapshot` task_runs 行へ記録し、既存 cron failure 通知経路へ渡すが、maintenance integrity フェーズや後続のROI照合・integrity checkの成否には混ぜない。

## 変更内容

- `scripts/render_regular_scheduler.py`
  - `run_entry_change_snapshots_nonfatal(now)` を追加。
  - today/tomorrowを個別に `run_entry_change_snapshot` へ渡す。
  - 失敗・例外を日付単位で隔離し、例外時も `render_entry_change_snapshot` failureを記録する。
  - 失敗通知は既存の `_notify_failure_best_effort` / cron alert経路を利用する。
  - 最新 `racer_entry_change_snapshots.snapshot_date` を監視し、3日以上古い、未生成、または鮮度取得不能なら `cron_watchdog_entry_change_snapshot` を `system_status` にerror記録し、既存watchdog alert経路へ通知する。
- `scripts/render_maintenance_scheduler.py`
  - 06:30以降のintegrityフェーズ冒頭から上記非致命ラッパーを呼ぶ。
  - スナップショット結果はphase detailへ残すが、ROI照合とmorning integrityの戻り値には影響させない。
- `tests/test_source_regression.py`
  - ASTで `run_entry_change_snapshot(...)` の実呼び出しが最低1つ存在することを検査。
  - today/tomorrowの2対象とmaintenance integrityからの到達性も検査。
- focused tests
  - today失敗後もtomorrowが実行されること。
  - 例外時にfailureを記録して次の日付へ進むこと。
  - 片方失敗でもmaintenance integrityが成功できること。
  - ちょうど3日古い時点でstatus/alertが発火すること。

## 検証結果

- 修正前AST確認: `run_entry_change_snapshot(...)` 呼び出し `0` 件。
- focused: `92 passed`。
- 全非E2E: `1159 passed, 1 skipped`。
  - 指定ベースライン `1155 passed, 1 skipped` を維持し、新規4テスト分増加。
- `py_compile`: 対象Pythonファイルすべて成功。
- `git diff --check`: 成功。
- `render.yaml`: 差分なし。
- `src/roi_contract.py`: 差分なし。
- 進入注意閾値: `ENTRY_CHANGE_MIN_STARTS=100`、`ENTRY_CHANGE_HIGH_RATE=0.20`、`ENTRY_CHANGE_INNER_MIN_RATE=0.10` に差分なし。
- 本番Supabase書込み、ローカルscheduler起動、push、deployはいずれも未実施。

## 8/13〜8/19 バックフィル手順（リン実行）

以下は本番DBへ書き込むため、Codex作業PCでは実行しない。リンが本番Render Shellなど、`DATABASE_URL` が対象Supabaseを指すことを確認できる環境で実行する。

PowerShellの場合:

```powershell
$dates = '2026-08-13','2026-08-14','2026-08-15','2026-08-16','2026-08-17','2026-08-18','2026-08-19'
foreach ($date in $dates) {
  python scripts/build_racer_entry_change_stats.py --date $date
  if ($LASTEXITCODE -ne 0) { throw "entry-change backfill failed: $date" }
}
```

Render Shell（bash）の場合:

```bash
for d in 2026-08-13 2026-08-14 2026-08-15 2026-08-16 2026-08-17 2026-08-18 2026-08-19; do
  python scripts/build_racer_entry_change_stats.py --date "$d" || break
done
```

このbuilderは対象日の既存行を削除して再生成するため、日付単位で冪等に再実行できる。各行の標準出力が `rows=N written=N` かつ `N > 0` であることを確認する。途中失敗時は修正後、その日付から再開する。

実行後はSupabase SQL Editor等の読取り専用確認で、7日すべてが存在し各日0件でないことを確認する。

```sql
SELECT snapshot_date, COUNT(*) AS rows
FROM racer_entry_change_snapshots
WHERE snapshot_date BETWEEN DATE '2026-08-13' AND DATE '2026-08-19'
GROUP BY snapshot_date
ORDER BY snapshot_date;
```

期待値は `2026-08-13` から `2026-08-19` まで7行の日付集計が返り、各 `rows > 0`。欠けた日付または0件の日付だけbuilderを再実行する。

## 残る本番確認

push/deploy後、次回maintenance integrityでtoday/tomorrowの `render_entry_change_snapshot` がそれぞれsuccessになること、`cron_watchdog_entry_change_snapshot` の古いerrorが新たに発生しないことをリンが確認する。ローカルテスト成功だけでは本番データ更新済みとは判定しない。
