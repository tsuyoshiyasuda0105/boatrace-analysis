# バックテスト日次自動更新パイプライン Step 27 実装結果

作成日: 2026-08-19

## 結果

完了済みレース日の kachisuji as-of 差分を PC で生成・Supabase Storage にアップロードし、本番 slim DB に安全に取り込むコードを実装した。

ただし、仕様書が想定する「新規Render cron jobからWebサービスの永続ディスク `/data` を直接更新する」構成は、現行Renderの[公式Persistent Disks仕様](https://render.com/docs/disks)と[公式Cron Jobs仕様](https://render.com/docs/cronjobs)では実行できない。cron jobは永続ディスクを付与・参照できず、永続ディスクは単一サービスだけが参照できる。このため、StorageまでのPC側自動化と安全な取込コマンドは完成しているが、Render側の完全自動化を有効にするには、下記の代替方式についてユーザー判断と追加実装が必要である。

Supabase バケット、環境変数、Renderサービスは作成していない。下記「ユーザーが行う設定」に、実行可能な作業と追加設計が必要な作業を分けて記載した。push / deploy も行っていない。

## 変更ファイル

- `scripts/upload_kachisuji_delta.py`
  - `data/kachisuji_delta_YYYYMMDD.db` を検証し、private bucket `kachisuji-deltas` の `YYYYMMDD.db` へ upsert する。
  - 認証情報は `SUPABASE_URL` と `SUPABASE_SERVICE_KEY` のみから読む。
  - アップロード後もローカル差分DBを削除しない。
- `scripts/apply_kachisuji_deltas.py`
  - Storageを列挙し、`applied_deltas` にない差分だけを古い順でダウンロードする。
  - ローカル差分を直接指定する `--delta` も用意した。
  - `/data/kachisuji_slim.db` への冪等適用、バックアップ、失敗時復元、サマリ出力を実装した。
- `scripts/pc_nightly_prepare.py`
  - 既存処理と既存SQLite→Supabase同期が成功した後に、JSTの前日を対象として既存 `refresh_kachisuji_daily.py` とアップローダーを独立実行する。
  - 既存予測パイプラインの戻り値には差分生成・アップロードの失敗を波及させず、失敗は夜間ログへ残す。
  - 同日再実行時は保持済み差分を再利用する。
- `tests/test_kachisuji_daily_autopipeline.py`
  - Storage通信、未適用抽出、二重適用、`uri=True`、破損差分からの復元、JST前日、夜間処理の分離を検証する。
- `tests/test_pc_nightly_prepare.py`
  - 既存同期の呼び出しを、追加された独立処理の順序に依存せず検証するよう更新した。
- `tests/test_db_pk_map_parity.py`
  - PostgreSQL shim の静的監査から、直接 `sqlite3` を使う2本のローカル専用スクリプトを明示的に除外した。
- `tests/e2e/conftest.py`, `tests/round3_e2e/conftest.py`
  - 全 `tests/` 同時実行時に同期Playwrightのイベントループが次のテスト群へ残らないよう、browser fixtureをmodule scopeに限定した。

`src/search/roi_search.py` と `src/search/strategies.py` は変更していない。既存 `scripts/refresh_kachisuji_daily.py` も重複作成・変更していない。

## Storage方式

- bucket: `kachisuji-deltas`（private）
- object: `YYYYMMDD.db`
- upload: Supabase Storage REST APIの `x-upsert: true`
- list/download: service role keyをBearer/API keyとして使用
- 差分DB: `asof_race_features` と `racers` を持つ自己完結SQLite
- 取込順: object名の昇順（古い日付から）
- 適用済み管理: slim DB内の `applied_deltas(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)`

## 未来情報防止

夜間連携が渡す日付はJSTの前日だけである。as-of構築は既存 `scripts/refresh_kachisuji_daily.py` をそのまま再利用しており、完了日のみを対象にする。365日集計の `[asof_date-364, asof_date)` も変更していない。

## 冪等性とロールバック

- `asof_race_features` と `racers` は `INSERT OR IGNORE` で追加する。
- 同一objectは `applied_deltas.name` により再適用対象から外れる。
- 同じ差分を二度渡してもテーブル行数は増えないことをテストした。
- 差分をATTACHするmain接続は必ず `sqlite3.connect(..., uri=True)` で開き、差分側は `file:...?...mode=ro` URIでATTACHする。
- 適用開始前に slim DBを同じパスの `.bak`（通常 `/data/kachisuji_slim.db.bak`）へ `copy2` する。
- schema不一致、破損SQLite、SQL例外などが起きた場合は接続を閉じ、`.bak` を slim DBへコピーして復元してから非ゼロ終了する。
- 各差分の書込みは短い `BEGIN IMMEDIATE` トランザクションで行い、Storage通信は書込みトランザクション開始前に完了させる。

## ユーザーが行う設定

### 1. Supabase

Supabase Dashboardでprivate Storage bucket `kachisuji-deltas` を作成する。

PCの `.env` と、選定後のRender側実行主体の環境変数に次を設定する。

```text
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_KEY=<service-role-key>
```

service role keyはGit、チャット、ログへ記載しない。

### 2. PC定時タスク

既存 `BoatracePcNightlyPrepare` が更新後の `scripts/pc_nightly_prepare.py` を実行することを確認する。新しいローカル定時タスクは不要で、無効タスクも復活させない。

### 3. Render側の制約と選択が必要な構成

現行Renderでは、次のcron serviceを作成しても `/data/kachisuji_slim.db` へは到達できないため、作成しないこと。

```text
Command: python scripts/apply_kachisuji_deltas.py
Schedule: 30 17 * * *
```

上記scheduleはUTC 17:30、JST 02:30相当だが、cron jobには永続ディスクを付与できず、Webサービスのディスクも共有できない。

Render側を完全自動化するには、次のいずれかを別ステップとして設計・承認する必要がある。

1. Webサービス自身に認証済み内部メンテナンス入口を追加し、diskなしcronはその入口を呼ぶだけにする。実際の `apply_kachisuji_deltas.py` 相当処理はWebサービス内で実行する。
2. Webサービス内の既存定時実行機構へ取込処理を同居させる。Webプロセスとの排他・失敗分離・起動保証の追加設計が必要である。
3. slim DBの配置を、複数サービスから安全に更新・参照できる管理データストアへ変更する。既存SQLite読取契約への影響が大きい。

いずれも今回許可された変更範囲を超えるため実装していない。方式決定後、実行主体には次を設定する。

```text
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_KEY=<service-role-key>
KACHISUJI_DB=/data/kachisuji_slim.db
```

最初の実行ログで `applied_files`、`asof_added`、`racers_added`、`latest_race_date` を確認する。

## 初回08-18ギャップ手順

推奨手順はStorage経由である。

PCで既存差分をアップロードする。

```powershell
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe scripts/upload_kachisuji_delta.py --delta data/kachisuji_delta_20260818.db
```

その後、永続ディスクを持つWebサービス自身のRender Shellで次を実行する。cron jobのShellではWebサービスのディスクへ到達できない。

```text
python scripts/apply_kachisuji_deltas.py
```

Render Shellへ差分ファイルを別途安全に配置済みの場合は、Storageを介さず次でも適用できる。

```text
python scripts/apply_kachisuji_deltas.py --delta /tmp/kachisuji_delta_20260818.db
```

いずれも二重実行でデータ行数は増えない。実行前の `/data/kachisuji_slim.db.bak` が残る。

## 検証

- Step 27対象回帰: 12 passed
- E2E/asyncio/SQL監査を含む再現回帰: 115 passed
- 指定全テスト:

```text
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest tests/ -q
1194 passed, 1 skipped
```

- scoped Python compile: success
- scoped Ruff: success
- `src/search/roi_search.py` / `src/search/strategies.py`: 差分なし
- push / deploy / Supabase書込み / Renderサービス作成: 未実施

## 既知の制限

- PC側は当日の前日差分だけを自動生成・アップロードする。過去日にネットワーク障害が残った場合、保持されている日付付き差分を `upload_kachisuji_delta.py --delta ...` で再送する必要がある。
- Storage objectとPC上の差分DBは自動削除しない。保持期間・削除運用は別途決める必要がある。
- `.bak` は直前の適用前状態1世代で、次回適用時に上書きされる。
- slim DBの同時writerは想定しない。Webアプリは既定どおり `mode=ro` で読む必要がある。
- Render Blueprint (`render.yaml`) は変更していない。Render側の代替方式が未選定のため、サービス設定も変更していない。
- 現行Renderではcron jobが永続ディスクを参照できず、別サービス間でディスク共有もできない。したがって、仕様書どおりの新規cronを作るだけでは完全自動化にならない。Render側の実行主体は上記3案から別途選定する必要がある。
