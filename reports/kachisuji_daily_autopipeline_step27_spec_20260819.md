# バックテスト日次自動更新パイプライン Step 27 仕様書

作成: 2026-08-19 リン（Claude Code）/ 発注先: Codex
前提: Step 26まで本番稼働。風向きはコース基準で分類済み（`relative_wind_direction`）。
ローカル日次リフレッシュ `scripts/refresh_kachisuji_daily.py`（Step 1-2）は実装・検証済み。
本ステップは **3.本番反映 + 4.定時実行** の完全自動化。

## 現状（調査で確定）
- バックテストデータ（asof/slim）の**日次自動更新は存在しない**。asofが最近まであるのは手動ビルド。
- 定時タスクは2つのみ: `BoatracePcNightlyPrepare`(01:00) / `BoatraceLocalSupabaseSync`(23:45)。
  どちらも asof/slim を作らない。**新規に足す**（既存を壊さない）。
- `refresh_kachisuji_daily.py` は「完了日の asof 構築 → slim へ冪等追記 → 236KBの差分DB出力」まで動作。
- 本番 Render の `/data/kachisuji_slim.db` は現在 race_date=2026-08-17 まで（wind_dir は修正版で埋め済み）。
  ローカル slim は 2026-08-18 まで前進済み。**08-18以降が本番未反映**。

## 目標アーキテクチャ（完全自動・ユーザー選択済み）

```
PC 夜間 (既存 BoatracePcNightlyPrepare に追記)
  ├─ (既存) daily_collect 等で boatrace.db を更新
  ├─ ★ refresh_kachisuji_daily.py --date <昨日>   # asof構築 + ローカルslim追記 + 差分DB出力
  └─ ★ upload_kachisuji_delta.py                   # 差分DB(236KB)を Supabase Storage へ

Render cron (新規サービス, 1日1回)
  └─ ★ apply_kachisuji_deltas.py                   # Storageの未適用差分を /data/kachisuji_slim.db に取込
```

差分DBは自己完結（`asof_race_features`+`racers` の新規行のみ）。**190列のテーブルミラー不要**、
Postgres 行数も消費しない。Storage 上のファイルを運ぶだけなので堅い。

## 絶対的な制約（違反禁止）
1. **`src/search/roi_search.py` / `src/search/strategies.py` は変更禁止**（検証計算ロジックに触れない）。
2. **未来情報を使わない設計を維持**：asof は完了日のみ構築（`refresh_kachisuji_daily.py` の既定=昨日）。
   365日集計は `[asof_date-364, asof_date)` のまま。
3. **既存の定時タスクを壊さない・無効タスクを復活させない**。追記は `pc_nightly_prepare.py` の steps に1〜2行。
4. **INSERT OR IGNORE で冪等**。差分の二重適用・再実行が安全なこと。
5. DB破壊防止：Render 側取込は `/data/kachisuji_slim.db` のバックアップを取ってから適用、失敗時ロールバック。
6. 変更してよいファイル: 新規スクリプト3本、`scripts/pc_nightly_prepare.py`（steps 追記）、
   対応テスト、`docs/` 結果レポート、`render.yaml`等の cron 定義（あれば）。

## 実装内容

### A. `scripts/upload_kachisuji_delta.py`（PC→Storage）
- 入力: `refresh_kachisuji_daily.py --emit-delta` が出した差分DB（`data/kachisuji_delta_YYYYMMDD.db`）。
- Supabase Storage のバケット `kachisuji-deltas` に `YYYYMMDD.db` として **upsert アップロード**。
- 認証は環境変数（`SUPABASE_URL` / `SUPABASE_SERVICE_KEY`）。ハードコード禁止。
- 冪等：同名再アップロードは上書き。ネットワーク失敗は非ゼロ終了（夜間ログに残す）。
- アップロード済みでもローカル差分ファイルは消さない（`data/` に日付付きで残す）。

### B. `scripts/apply_kachisuji_deltas.py`（Render cron）
- Storage の `kachisuji-deltas/` を列挙し、**まだ適用していない差分**を古い順に取得。
  - 「適用済み」は `/data/kachisuji_slim.db` 内の小テーブル `applied_deltas(name TEXT PK, applied_at TEXT)` で管理。
- 各差分DBを一時DL → `ATTACH ... mode=ro`（**main接続は uri=True 必須**）→
  `INSERT OR IGNORE INTO asof_race_features/racers` → `applied_deltas` に記録。
- 適用前に `/data/kachisuji_slim.db` を `.bak` にコピー、途中失敗なら復元。
- 完了後サマリ出力（適用ファイル数・追加行数・最新 race_date）。
- **アプリは mode=ro で読む**ため、cron の書込みと衝突しないよう短時間トランザクションで。

### C. `scripts/pc_nightly_prepare.py` への追記
- 既存 steps の後ろに2ステップ追加（完了日=前日を対象）:
  1. `refresh_kachisuji_daily.py --date <yesterday> --emit-delta data/kachisuji_delta_<yyyymmdd>.db`
  2. `upload_kachisuji_delta.py --delta data/kachisuji_delta_<yyyymmdd>.db`
- `<yesterday>` は JST。01:00 実行時点で前日は全レース完了・結果/風取得済みの想定。
- 失敗しても既存の予測パイプライン成否に影響させない（別 try/ログ、`ok` は既存維持）。

### D. Render cron 定義
- 新規 cron サービス（1日1回、JST 早朝の PC アップロード後、例: 02:30 JST 相当）。
  `python scripts/apply_kachisuji_deltas.py`。
- 環境変数 `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` / `KACHISUJI_DB=/data/kachisuji_slim.db`。
- **Render ダッシュボードでの作成はユーザー作業**（Codexは render 定義/手順書のみ用意）。

## 初回ギャップの解消（08-18以降）
- 本番は 08-17 まで。パイプライン稼働後、08-18…の差分が順次適用され追いつく。
- 手動即時反映したい場合: ローカルの `data/kachisuji_delta_20260818.db`(236KB) を
  Render Shell で `apply_kachisuji_deltas.py` にかける手順もレポートに記載。

## テスト
1. `upload`/`apply` はネットワークをモックし、Storage列挙→未適用抽出→INSERT OR IGNORE→applied_deltos記録を検証。
2. 差分の**二重適用が冪等**（2回流しても行数不変）。
3. 破損差分DBで **`.bak` から復元**され slim が無傷。
4. `pc_nightly_prepare` の追記ステップが失敗しても既存 steps の戻り値に影響しない。
5. 既存の全テストがグリーン（特に roi_search / strategies / kachisuji 統合）。

## DoD
1. 全テストグリーン。ローカルコミット（push しない）。
2. 結果レポート `docs/kachisuji_daily_autopipeline_step27_result_20260819.md` に:
   変更ファイル / Storage方式の設計 / 冪等・ロールバックの実装 / **ユーザーがやる設定**
   （Supabaseバケット作成・環境変数・Render cron作成）/ 初回ギャップ手順 / 既知の制限。

## ユーザー作業（Codex完了後・リンが案内）
1. Supabase: Storage バケット `kachisuji-deltas` 作成（private）。
2. PC の `.env` と Render に `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` 設定。
3. Render に cron サービス追加（上記D）。
4. 初回 08-18 差分の手動適用（任意）。
