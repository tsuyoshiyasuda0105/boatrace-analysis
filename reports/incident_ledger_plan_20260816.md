# 作業指示書: インシデント台帳 (エラー検知→記録→AI対応履歴) の構築 (Codex CLI 用)

作成: 2026-08-16 / 発注者: リッキー / 検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本のみ)
現行 main: 本番 `0225c0f`。テスト基準 `--ignore=tests/e2e --ignore=tests/round3_e2e` (976 passed)。

## 背景と狙い

発注者の要望:
> バグを検知してメール通知し、**AIが対応した履歴を記載**する。**他のアプリ作成でも使用できる**ようにしたい。

現状:
- **メール通知は完成・本番稼働中** (`src/notifications/cron_alerts.py::notify_cron_failure`、
  `src/notifications/error_handler.py` の ERROR ハンドラ、`mailer.py` 経由。
  Env Group `boatrace-shared` で web + 全 cron が送信可能)。
- **足りないのは「台帳」**: 何が起きて、誰が/何が、いつ、どう対応したかの履歴が残らない。

方針 (発注者承認済み): **Supabase に共通のインシデント台帳テーブルを1つ作る**。
Google スプレッドシート/外部サービスは使わない (新しい認証・契約が増えないため)。
**`app_name` 列で複数アプリを1つの台帳に集約**でき、将来の別アプリも同じ台帳に書ける。

## ゴール

1. エラー/障害が**メール通知されると同時に台帳へ1行記録**される。
2. **対応履歴 (誰が/いつ/何をしたか) を追記**できる (リンが対応内容を書き込む)。
3. **管理画面で一覧・確認**できる。
4. **他アプリから再利用可能** (app_name 指定で同じ台帳に書ける汎用ヘルパー)。

## 絶対ルール

1. **origin/main へ push 禁止** (ローカル main まで)。
2. **既存テーブルのスキーマは変更しない。** 追加するのは**新テーブル1つだけ** (と必要な index)。
   `src/db/schema.sql` の既存流儀 (`CREATE TABLE IF NOT EXISTS`) に従う。
   Postgres/SQLite 両対応 (既存シム `src.db.connection` 経由。**PK は
   `_TABLE_PRIMARY_KEYS` に登録**し、既存の PK パリティテストを通すこと)。
3. ROI 戦略・予測・収集ロジック・render.yaml は変更しない。**新 cron を追加しない**。
4. **通知の既存挙動を壊さない**。台帳書き込みは**ベストエフォート**で、失敗しても
   メール送信や本流を止めない (例外を握って log)。
5. `pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e` を割らない + 新規 green。
6. 作業ログ `reports/incident_ledger_work_log_20260816.md`。コミット2〜4個。

## やること

### 1. 台帳テーブル `incident_log` を追加

`src/db/schema.sql` に追加 (列名は提案。過不足は判断してよいが目的を満たすこと):

| 列 | 型 | 説明 |
|---|---|---|
| `incident_id` | TEXT (PK) | 一意ID (例: `<app>-<utc_ts>-<hash8>`)。**決定的に生成** |
| `app_name` | TEXT | **複数アプリ共通化の要**。既定は `boatrace` (env で上書き可) |
| `occurred_at` | TEXT | 発生時刻 (JST naive iso、既存流儀に合わせる) |
| `category` | TEXT | 種別 (例: `cron_failure` / `app_error` / `watchdog`) |
| `source` | TEXT | 発生元 (job 名 / logger 名 / エンドポイント) |
| `title` | TEXT | 短い件名 (正規化済みメッセージ先頭) |
| `detail` | TEXT | 詳細 (JSON 文字列可。stats 等) |
| `severity` | TEXT | `error` / `warning` |
| `dedup_key` | TEXT | **同種判定キー** (error_handler の正規化キーと同じ思想: 可変 stats/数値を除去) |
| `occurrence_count` | INTEGER | 同種の再発回数 (同じ dedup_key なら加算) |
| `last_seen_at` | TEXT | 最終発生時刻 |
| `notified` | INTEGER | メール通知したか (0/1) |
| `status` | TEXT | `open` / `investigating` / `resolved` / `wontfix` |
| `resolved_at` | TEXT | 対応完了時刻 |
| `handled_by` | TEXT | 対応者 (例: `rin` / `codex` / `human`) |
| `response_note` | TEXT | **AI/人の対応内容** (ここが要望の核心) |
| `updated_at` | TEXT | 更新時刻 |

**重要**: 同種エラーは**行を増やし続けず、`dedup_key` で 1 行に集約して
`occurrence_count` / `last_seen_at` を更新**する (メール1時間1通の思想と揃える)。
ただし `status='resolved'` の後に再発したら**新しい行を起こす** (再発が埋もれない)。

### 2. 汎用ヘルパー `src/notifications/incident_ledger.py` (新規)

他アプリでも再利用できる薄い API:

```
record_incident(*, category, source, title, detail=None, severity="error",
                dedup_key=None, app_name=None, notified=False, conn=None) -> str|None
resolve_incident(incident_id_or_dedup_key, *, handled_by, response_note,
                 status="resolved") -> bool
list_incidents(*, app_name=None, status=None, since=None, limit=50) -> list[dict]
```

- `app_name` 既定は env `BOATRACE_INCIDENT_APP_NAME` → 無ければ `"boatrace"`。
  **他アプリはこの env を変えるだけで同じ台帳を共有できる**。
- `dedup_key` 未指定なら title/source から正規化生成 (error_handler の
  正規化ロジックと同じ思想。可能なら共通化して重複実装を避ける)。
- **すべてベストエフォート**: DB 不通でも例外を投げず None/False を返して log。

### 3. 既存の通知経路にフックする (通知と同時に記録)

- `cron_alerts.notify_cron_failure`: メール送信の可否にかかわらず
  `record_incident(category="cron_failure", source=job, ...)` を呼ぶ
  (`notified` は実際に送ったかを反映)。**宛先未設定でも台帳には残す** (ここ重要:
  メールが飛ばない環境でも履歴は貯まる)。
- `error_handler` の ERROR ハンドラ: 同様に `category="app_error"` で記録。
  **既存の正規化キー (可変 stats 除去) を dedup_key に流用**。
- `render_regular_scheduler` の watchdog 異常: `category="watchdog"` で記録
  (既に system_status には書いているので、**台帳にも1行**)。
- いずれも**失敗しても本流を止めない**。

### 4. 管理画面に一覧を追加

`/admin/incidents` (既存 `/admin/data-status` と同じ認可・実装流儀) を追加:
- 直近のインシデント一覧 (発生時刻/アプリ/種別/件名/深刻度/再発回数/状態/対応内容)。
- `status` でフィルタ (既定は `open` + `investigating` を上に)。
- **軽量に** (単純な SELECT + LIMIT。重い集計をしない)。
- 既存の管理画面ナビに導線を1つ追加 (あれば)。

### 5. リンが対応履歴を書けるCLI

`scripts/incident.py` (仮) を追加:
```
python scripts/incident.py list [--status open] [--app boatrace] [--limit 20]
python scripts/incident.py resolve <incident_id> --by rin --note "原因と対応内容"
```
リン (Claude) がターミナルから対応履歴を記録できるようにする。

## テスト (`tests/` に追加)

- `record_incident` が新規行を作り、同じ dedup_key の再発で
  `occurrence_count` 加算 + `last_seen_at` 更新 (行は増えない)。
- `resolved` 後の再発は**新しい行**になる。
- `resolve_incident` が status/handled_by/response_note/resolved_at を更新。
- DB 不通時に例外を投げず、**通知/本流を止めない** (ベストエフォート)。
- `app_name` が env で切り替わり、`list_incidents(app_name=...)` で分離できる
  (**複数アプリ共有の担保**)。
- `incident_log` が `_TABLE_PRIMARY_KEYS` に登録され、既存 PK パリティテストが通る。
- `/admin/incidents` が認可済みで 200 を返し、重いクエリを投げない。

## 受け入れ条件

- [ ] `incident_log` テーブル追加 (既存テーブル無改変・PK マップ登録)
- [ ] 汎用ヘルパーで記録/解決/一覧ができ、`app_name` で複数アプリ共有可能
- [ ] 既存3経路 (cron失敗/アプリERROR/watchdog) から通知と同時に記録される
- [ ] `/admin/incidents` で一覧・対応履歴が見える
- [ ] `scripts/incident.py` でリンが対応内容を記録できる
- [ ] 台帳書き込み失敗が通知・本流を止めない (ベストエフォート)
- [ ] `pytest ... --ignore=e2e --ignore=round3_e2e` 維持 + 新規 green / push なし / 作業ログ

## 検品 (リンが実施)

「既存テーブルを壊していないか」「台帳書き込みが本流を止めないか」「同種集約と再発の
扱いが妥当か」「app_name で他アプリに再利用できるか」「管理画面が軽量か」
「PK マップ登録と既存パリティテスト通過」「テスト green か」を照合。デプロイは発注者承認後。
