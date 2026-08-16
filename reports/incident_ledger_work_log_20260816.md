# インシデント台帳 作業ログ

作業日: 2026-08-16
対象: `reports/incident_ledger_plan_20260816.md`
方針: メール通知は既存経路を維持し、不足していた「検知→記録→AI/人の対応履歴」を共通Supabase台帳として追加する。

## 実装結果

### 1. `incident_log` テーブル

`src/db/schema.sql` に新規テーブルを1つだけ追加した。既存テーブルの定義変更はない。

- 主キー: `incident_id` (`TEXT PRIMARY KEY`)
- アプリ分離: `app_name`
- 発生情報: `occurred_at`, `category`, `source`, `title`, `detail`, `severity`
- 同種集約: `dedup_key`, `occurrence_count`, `last_seen_at`, `notified`
- 対応履歴: `status`, `resolved_at`, `handled_by`, `response_note`, `updated_at`
- 部分ユニーク索引: `(app_name, dedup_key)` のうち `open` / `investigating` の行だけを一意にする
- 一覧用索引: `(app_name, status, last_seen_at DESC)`

未解決の同種障害は原子的な `occurrence_count + 1` と `last_seen_at` 更新で1行に集約する。`resolved` / `wontfix` は部分ユニーク索引の対象外なので、対応完了後の再発は新しい `incident_id` の行になる。`incident_log` は `src.db.connection._TABLE_PRIMARY_KEYS` に `['incident_id']` として登録した。

### 2. 汎用ヘルパー

新規 `src/notifications/incident_ledger.py` に以下を実装した。

```python
record_incident(*, category, source, title, detail=None, severity="error",
                dedup_key=None, app_name=None, notified=False, conn=None) -> str | None
resolve_incident(incident_id_or_dedup_key, *, handled_by, response_note,
                 status="resolved") -> bool
list_incidents(*, app_name=None, status=None, since=None,
               limit=50) -> list[dict]
```

- `app_name` は引数 → `BOATRACE_INCIDENT_APP_NAME` → `boatrace` の順で決定する。
- `dedup_key` 未指定時は発生元と件名から生成し、可変 `stats={...}` と数値を正規化する。
- 詳細は文字列をそのまま、dict等はJSON文字列として保存する。
- 一覧は最大200行に制限し、`open` / `investigating` を先頭にする単純SELECTである。
- 全公開APIはDB接続・SQL・commit・closeの失敗を吸収し、`None` / `False` / `[]` を返す。台帳障害で呼び出し元を停止しない。

### 3. 既存経路へのフック

1. `src/notifications/cron_alerts.py::notify_cron_failure`
   - 宛先未設定、クールダウン抑制、送信成功、送信失敗の全return経路で台帳へ1回記録する。
   - 実際の `_send` 成否を `notified` に入れる。
   - cron単位の既存メールクールダウン思想と揃え、`cron_failure|<job>` をdedup keyにする。
2. `src/notifications/error_handler.py::EmailErrorHandler`
   - ERROR/CRITICALごとに `app_error` として記録する。メールの1時間抑制中も発生回数は加算する。
   - 既存の正規化ロジックを共通ヘルパーへ移し、メールと台帳で同じエラーファミリーキーを使う。
   - 宛先未設定でも台帳専用handlerを設置する。app/root loggerへ同一LogRecordが伝播した場合は1回だけ記録する。
3. `scripts/render_regular_scheduler.py` watchdog
   - 既存 `notify_cron_failure` 呼び出しに `incident_category="watchdog"` を渡す。
   - メールと台帳を別々に呼ばず、二重行を作らない。

いずれも台帳側の例外を外へ出さず、既存メールやcron/Web本流の戻り値・制御フローを止めない。

### 4. 管理画面

- `GET /admin/incidents`
- `/admin/data-status` と同じ `@admin_required` 認可
- 状態フィルタ: all / open / investigating / resolved / wontfix
- limit: 1〜200、既定50
- 表示: 最終発生、アプリ、種別/発生元、件名/ID、深刻度、回数、状態、対応者/対応内容/解決時刻
- 会員管理とデータ取得状況の既存管理画面から導線を追加

DB処理は `list_incidents` の単純SELECT + LIMITのみで、集計やN+1クエリは追加していない。

### 5. CLI

```powershell
python scripts/incident.py list --status open --app boatrace --limit 20
python scripts/incident.py resolve <incident_id> --by rin --note "原因と対応内容"
python scripts/incident.py resolve <incident_id> --by rin --note "調査開始" --status investigating
```

`resolve` は incident ID を優先し、現在のappの未解決dedup keyでも指定できる。成功は終了コード0、対象なし/DB失敗は1を返す。

## 複数アプリ共有の担保

同じ `DATABASE_URL` を使う各アプリで `BOATRACE_INCIDENT_APP_NAME` だけを変更すれば、同じ `incident_log` に書き込みつつ、一覧とdedupはアプリ単位に分離される。部分ユニーク索引も `(app_name, dedup_key)` なので、別アプリの同名エラーが混ざらない。テストでは `app-a` と `app-b` を同一SQLiteテーブルへ記録し、env既定と明示フィルタの双方で分離を確認した。

## テスト・検証

- 初回対象セット: 38 passed
- 重複LogRecordガード修正後の対象セット: 8 passed
- 最終指定全体:
  - `pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e`
  - **1010 passed, 1 skipped**
  - warningは既存 `.pytest_cache` のWindows ACL警告1件のみ
- `python -W error -m py_compile` 成功
- `git diff --check` / 各コミットの `git diff --cached --check` 成功
- PKパリティテスト成功
- 本番Supabaseは `default_transaction_read_only=on` の短命接続で読み取り確認のみ実施:
  - 既存PK: `races`, `system_status`, `task_runs`
  - 直近7日 `races`: 1,464件
  - 直近7日 `system_status`: 122件
- SupabaseへのDDL/DML、ローカルscheduler、production writer、Web server、browserは起動していない。

## 禁止領域・差分監査

- push/deployなし
- 既存テーブルのスキーマ変更なし。追加は `incident_log` 1テーブルとその索引のみ
- 新cronなし
- ROI、予測、収集、`render.yaml` 変更なし
- `data/boatrace.db` 変更なし
- 作業中に現れた別タスクのkachisuji UI/E2E差分、一時領域、未追跡レポートは未ステージ・未変更
- 今回作成したpytest basetempだけを絶対パス確認後に削除し、残存なし

## 作業中の失敗と予防

1. 日本語指示書の初回PowerShell表示が既定decodeで文字化けした。以後 `-Encoding UTF8` を指定した。
2. 複合 `rg` の二重引用符がPowerShellで閉じず、読取前に構文エラーとなった。単一引用符の単純パターンと分割読取へ変更した。
3. 同一LogRecordの二重台帳防止ガードで `finally` 内 `return` のSyntaxWarningを検出した。正条件ブロックへ修正し、`-W error` compilationと対象テストを再実行した。
4. 本番読み取り確認は最初sandbox TCP制限で失敗し、承認済みの限定昇格で再実行した。次にTEXT `race_date` とtimestampの型比較を誤りPostgreSQLが拒否した。セッションは先にread-only化済みで書き込みはなく、`YYYY-MM-DD` 文字列境界へ修正して成功した。

## ローカルコミット

- `b87edb5` Add shared incident ledger storage
- `ead9873` Record notification failures in incident ledger
- `88ffaad` Add incident ledger admin tools
- 本作業ログは4つ目のローカルコミットに含める。正確なIDは最終報告に記載する。

origin/mainへのpushと本番DDL適用は行っていない。デプロイ/本番テーブル作成は発注者承認後の別工程とする。
